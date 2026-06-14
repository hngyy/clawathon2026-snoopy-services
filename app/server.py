"""HTTP adapter.

Exposes the agent (built by app.bootstrap) as the AgentBase entrypoint plus a
health check. The layer wiring lives in app/bootstrap.py, shared with the CLI.

When a Zalo Bot token is configured (app/credentials/zalo.credentials.json or
ZALO_BOT_TOKEN), a `/zalo/webhook` route is also mounted so the Zalo Bot Platform
(bot.zaloplatforms.com) can push user messages straight into router.chat().
"""
from __future__ import annotations

import logging
from datetime import datetime

from greennode_agentbase import GreenNodeAgentBaseApp, PingStatus, RequestContext
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.agent.router import AgentRouter
from app.bootstrap import bootstrap
from app.infrastructure.zalo_bot_client import ZaloBotClient
from app.settings import Settings

logger = logging.getLogger("tour_bot.server")

# Header Zalo attaches to every webhook push, echoing the secret_token we set.
_ZALO_SECRET_HEADER = "X-Bot-Api-Secret-Token"


def create_app() -> GreenNodeAgentBaseApp:
    _config, router = bootstrap()
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
        _mount_zalo_webhook(app, router, settings)
        logger.info("Zalo webhook mounted at /zalo/webhook")

    return app


def _mount_zalo_webhook(app: GreenNodeAgentBaseApp, router: AgentRouter, settings: Settings) -> None:
    """Add POST /zalo/webhook. Verifies the secret token, runs the agent for text
    messages, and replies via the Zalo Bot sendMessage API — all in-process."""
    zalo = ZaloBotClient(settings.zalo_bot_token)
    secret = settings.zalo_webhook_secret

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
                sender = str((message.get("from") or {}).get("id") or chat_id)
                result = await run_in_threadpool(router.chat, sender, chat_id, text)
                reply = result.get("response") or "Xin lỗi, mình chưa có câu trả lời."
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
