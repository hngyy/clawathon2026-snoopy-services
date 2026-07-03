"""HTTP adapter.

Exposes the agent (built by app.bootstrap) as the AgentBase entrypoint plus a
health check. The layer wiring lives in app/bootstrap.py, shared with the CLI.

When a Zalo Bot token is configured (app/credentials/zalo.credentials.json or
ZALO_BOT_TOKEN), a `/zalo/webhook` route is also mounted so the Zalo Bot Platform
(bot.zaloplatforms.com) can push user messages straight into router.chat().
"""
from __future__ import annotations

import asyncio
import logging
import secrets
from datetime import datetime

from greennode_agentbase import GreenNodeAgentBaseApp, PingStatus, RequestContext
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.agent.router import AgentRouter
from app.bootstrap import bootstrap
from app.config import AppConfig
from app.infrastructure.zalo_bot_client import ZaloBotClient
from app.infrastructure.zalo_owner_store import OwnerStore
from app.settings import Settings

logger = logging.getLogger("tour_bot.server")

# Header Zalo attaches to every webhook push, echoing the secret_token we set.
_ZALO_SECRET_HEADER = "X-Bot-Api-Secret-Token"
# user_id that resolves to the `owner` role — config.yaml lists it in owner.identities.
_OWNER_UID = "owner"
# The Zalo "typing" indicator expires client-side after a few seconds, but the agent
# can take 8-17s — so re-send it on this interval until the reply is ready.
_TYPING_REFRESH_S = 4.0


async def _keep_typing(zalo: ZaloBotClient, chat_id: str, stop: asyncio.Event) -> None:
    """Re-send the "typing" chat action every _TYPING_REFRESH_S seconds until `stop`
    is set (i.e. the agent has produced a reply). Best-effort — a failed action never
    interrupts the turn. Wakes immediately when `stop` is set, so no trailing sleep."""
    while not stop.is_set():
        try:
            await run_in_threadpool(zalo.send_chat_action, chat_id, "typing")
        except Exception:
            logger.debug("sendChatAction failed for chat=%s (non-fatal)", chat_id)
        try:
            await asyncio.wait_for(stop.wait(), timeout=_TYPING_REFRESH_S)
        except asyncio.TimeoutError:
            pass


def create_app() -> GreenNodeAgentBaseApp:
    config, router = bootstrap()
    settings = Settings.from_env()  # dotenv already loaded by bootstrap()

    app = GreenNodeAgentBaseApp()

    @app.entrypoint
    def handler(payload: dict, context: RequestContext) -> dict:
        """POST /invocations — {"message": "...", "history": [{role, content}, ...]}.

        history is optional (stateless build). Role is derived from
        context.user_id; see AgentRouter.
        """
        message = payload.get("message", "")
        if not message:
            return {"status": "error", "error": "Missing 'message' in payload."}

        result = router.chat(
            user_id=context.user_id,
            session_id=context.session_id,
            message=message,
        )
        return {
            "status": "success",
            "timestamp": datetime.now().isoformat(),
            **result,
        }

    @app.ping
    def health_check() -> PingStatus:
        return PingStatus.HEALTHY

    if settings.zalo_enabled:
        _mount_zalo_webhook(app, router, config, settings)
        logger.info("Zalo webhook mounted at /zalo/webhook")

    return app


def _mount_zalo_webhook(
    app: GreenNodeAgentBaseApp, router: AgentRouter, config: AppConfig, settings: Settings
) -> None:
    """Add POST /zalo/webhook. Verifies the secret token, runs the agent for text
    messages, and replies via the Zalo Bot sendMessage API — all in-process.

    In-chat commands (handled before the agent): /whoami, /owner <password>, /signout.
    """
    zalo = ZaloBotClient(settings.zalo_bot_token)
    secret = settings.zalo_webhook_secret
    owner_password = settings.zalo_owner_password
    owners = OwnerStore(settings.data_dir / "zalo_owners.json")

    def effective_role(sender: str) -> str:
        return _OWNER_UID if owners.is_owner(sender) else config.role_for(sender)

    def handle_command(stripped: str, sender: str, display_name: str) -> str | None:
        """Return a reply string if `stripped` is a recognised command, else None."""
        low = stripped.lower()
        if low == "/whoami":
            return f"Zalo id: {sender}\ndisplay_name: {display_name}\nrole: {effective_role(sender)}"
        if low == "/owner" or low.startswith("/owner "):
            pw = stripped.split(maxsplit=1)[1].strip() if " " in stripped else ""
            if not owner_password:
                return "Owner elevation is not configured on this bot."
            if pw and secrets.compare_digest(pw, owner_password):
                owners.add(sender)
                return "✅ You are now the owner. You can manage tour requests. Send /signout to switch back."
            return "❌ Wrong or missing password. Usage: /owner <password>"
        if low in ("/signout", "/logout", "/requester"):
            owners.remove(sender)
            return "You are back to the requester role."
        return None

    async def zalo_webhook(request):
        if secret and request.headers.get(_ZALO_SECRET_HEADER) != secret:
            logger.warning("Rejected Zalo webhook with bad/missing secret token")
            return JSONResponse({"ok": False, "error": "invalid secret token"}, status_code=403)

        try:
            update = await request.json()
        except Exception:
            return JSONResponse({"ok": False, "error": "invalid json"}, status_code=400)

        message = update.get("message") or {}
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        text = message.get("text")
        event_name = update.get("event_name", "")

        # No chat to reply to (e.g. a non-message event) — ack and move on.
        if not chat_id:
            return JSONResponse({"ok": True})
        chat_id = str(chat_id)

        # Always ack 200 to Zalo, even on internal failure, to avoid retry storms.
        try:
            if text:
                from_ = message.get("from") or {}
                sender = str(from_.get("id") or chat_id)

                command_reply = handle_command(text.strip(), sender, from_.get("display_name", ""))
                if command_reply is not None:
                    await run_in_threadpool(zalo.send_message, chat_id, command_reply)
                    return JSONResponse({"ok": True})

                # Elevated senders act as the owner; everyone else keeps their Zalo id.
                actor = _OWNER_UID if owners.is_owner(sender) else sender

                # Keep the "typing" indicator alive (it expires client-side after a few
                # seconds) until the agent returns, so the chat never looks frozen.
                stop_typing = asyncio.Event()
                typing_task = asyncio.create_task(_keep_typing(zalo, chat_id, stop_typing))
                try:
                    result = await run_in_threadpool(router.chat, actor, chat_id, text)
                finally:
                    stop_typing.set()
                    await typing_task
                # LLM replies sometimes have leading/trailing blank lines — trim before sending.
                reply = (result.get("response") or "").strip() or "Xin lỗi, mình chưa có câu trả lời."
                await run_in_threadpool(zalo.send_message, chat_id, reply)
            elif event_name.startswith("message."):
                # image / sticker / voice / unsupported — the agent is text-only.
                await run_in_threadpool(
                    zalo.send_message,
                    chat_id,
                    "Hiện mình chỉ đọc được tin nhắn dạng văn bản. Bạn nhập giúp mình bằng chữ nhé.",
                )
        except Exception:
            logger.exception("Zalo webhook handling failed for chat=%s", chat_id)

        return JSONResponse({"ok": True})

    app.router.routes.append(Route("/zalo/webhook", zalo_webhook, methods=["POST"]))


app = create_app()
