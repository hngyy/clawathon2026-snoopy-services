"""Zalo media — lets the agent send stickers/photos into the current Zalo chat.

This is a transport capability, not a domain service: it is registered ONLY when
a Zalo bot is configured (container.zalo_client present). The tools resolve the
current chat from the RunnableConfig `thread_id` — which the /zalo/webhook handler
sets to the Zalo `chat_id` (passed as `session_id` into router.chat). Outside a
Zalo chat (e.g. the /invocations API or CLI) there is no usable chat, so the tools
no-op gracefully and tell the LLM to just reply with text.

Note: bound to every role for `flat` routing (the configured mode). It is not a
routable destination service — `description` is intentionally empty.
"""
from __future__ import annotations

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from app.container import Container
from app.infrastructure.zalo_bot_client import ZaloBotClient
from app.services.registry import Service, ServiceRegistry

_PROMPT = (
    "## Zalo media\n"
    "When the user is chatting on Zalo you can send rich content to the current chat:\n"
    "- `send_zalo_photo(photo_url, caption)` — send an image by public URL (optional caption).\n"
    "- `send_zalo_sticker(sticker)` — send a sticker; `sticker` is a value from stickers.zaloapp.com.\n"
    "Use these sparingly, only when they add value (e.g. a welcome sticker). If a tool reports "
    "no chat context, the user is not on Zalo — just reply with text."
)


def _build_tools(zalo: ZaloBotClient) -> list:
    def _chat_id(config: RunnableConfig | None) -> str | None:
        cid = (config or {}).get("configurable", {}).get("thread_id")
        return str(cid) if cid else None

    @tool
    def send_zalo_photo(photo_url: str, caption: str = "", config: RunnableConfig = None) -> str:
        """Send an image (by public URL) to the current Zalo chat, with an optional caption."""
        chat_id = _chat_id(config)
        if not chat_id:
            return "No Zalo chat context; cannot send a photo here."
        try:
            zalo.send_photo(chat_id, photo_url, caption or None)
            return "Photo sent."
        except Exception as e:  # surface to the LLM, don't crash the turn
            return f"Failed to send photo: {e}"

    @tool
    def send_zalo_sticker(sticker: str, config: RunnableConfig = None) -> str:
        """Send a Zalo sticker to the current chat. `sticker` is a value from stickers.zaloapp.com."""
        chat_id = _chat_id(config)
        if not chat_id:
            return "No Zalo chat context; cannot send a sticker here."
        try:
            zalo.send_sticker(chat_id, sticker)
            return "Sticker sent."
        except Exception as e:
            return f"Failed to send sticker: {e}"

    return [send_zalo_photo, send_zalo_sticker]


def register(container: Container, registry: ServiceRegistry) -> None:
    if not container.zalo_client:
        return  # no Zalo bot configured → nothing to bind
    tools = _build_tools(container.zalo_client)
    roles = container.config.roles()
    registry.add(Service(
        key="zalo_media",
        display_name="Zalo Media",
        description="",  # not a routable destination service (see module docstring)
        tools={role: tools for role in roles},
        prompts={role: _PROMPT for role in roles},
    ))
