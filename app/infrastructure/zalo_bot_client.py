"""Zalo Bot Platform client — https://bot.zaloplatforms.com

This is the Telegram-style Bot API (bot.zaloplatforms.com), NOT the Zalo OA
Customer Service API. Auth is a single static bot token placed in the URL path:

    https://bot-api.zaloplatforms.com/bot<TOKEN>/<method>

Used for two directions:
  * outbound — send_message() replies to a chat after the agent answers.
  * setup    — set_webhook()/get_webhook_info()/delete_webhook() register the
               public webhook URL so Zalo pushes updates to us.

Incoming updates are handled in app/server.py (the /zalo/webhook route); this
client only owns the outbound calls. Every method raises ZaloBotError on failure
so callers can fall back gracefully.
"""
from __future__ import annotations

import logging
import re
import warnings

import requests
import urllib3

logger = logging.getLogger("tour_bot.zalo")

_BASE = "https://bot-api.zaloplatforms.com"
_TIMEOUT = 20
# Zalo's setWebhook constraint: 8-256 chars, only A-Z a-z 0-9 _ - (no ':' etc.).
_SECRET_RE = re.compile(r"[A-Za-z0-9_-]{8,256}")
# Zalo sendMessage rejects text > 2000 chars with a 400 — longer replies are chunked.
_MAX_MESSAGE_LEN = 2000


def _split_message(text: str, limit: int = _MAX_MESSAGE_LEN) -> list:
    """Split `text` into pieces of at most `limit` chars, breaking on the last
    newline (then the last space) within each window so words/lines stay intact;
    falls back to a hard cut only when there is no break point."""
    parts: list = []
    remaining = text
    while len(remaining) > limit:
        window = remaining[:limit]
        cut = window.rfind("\n")
        if cut <= 0:
            cut = window.rfind(" ")
        if cut <= 0:
            cut = limit  # no whitespace in the window — hard cut
        parts.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    if remaining:
        parts.append(remaining)
    return parts


class ZaloBotError(RuntimeError):
    pass


class ZaloBotClient:
    def __init__(self, token: str):
        if not token:
            raise ValueError("ZaloBotClient requires a bot token")
        self._token = token
        self._http = requests.Session()
        # Mirror TrelloClient: corporate SSL-inspection proxies intercept outbound
        # TLS — suppress verify errors rather than shipping a custom CA bundle.
        self._http.verify = False
        warnings.filterwarnings("ignore", category=urllib3.exceptions.InsecureRequestWarning)

    def _request(self, method: str, body: dict) -> dict:
        url = f"{_BASE}/bot{self._token}/{method}"
        resp = self._http.post(url, json=body, timeout=_TIMEOUT)
        if not resp.ok:
            raise ZaloBotError(f"Zalo {method} failed ({resp.status_code}): {resp.text}")
        data = resp.json()
        if not data.get("ok", False):
            raise ZaloBotError(f"Zalo {method} returned not-ok: {data}")
        return data

    # ── outbound ────────────────────────────────────────────────────────
    def send_message(self, chat_id: str, text: str) -> dict:
        """Send a text reply to a chat. chat_id comes from the update's
        message.chat.id. Replies longer than Zalo's 2000-char limit are split into
        several sequential messages (Zalo rejects >2000 with a 400). Returns the
        API result of the last part sent."""
        parts = _split_message(text)
        result: dict = {}
        for chunk in parts:
            result = self._request("sendMessage", {"chat_id": chat_id, "text": chunk}).get("result", {})
        logger.info("Sent Zalo message to chat=%s (%d part(s))", chat_id, len(parts))
        return result

    def send_chat_action(self, chat_id: str, action: str = "typing") -> dict:
        """Show a transient action indicator in the chat (e.g. "typing") while the
        agent works. Valid actions: "typing" (upload_photo is coming soon)."""
        return self._request("sendChatAction", {"chat_id": chat_id, "action": action}).get("result", {})

    def send_photo(self, chat_id: str, photo: str, caption: str | None = None) -> dict:
        """Send an image by URL. caption is optional (1-2000 chars)."""
        body = {"chat_id": chat_id, "photo": photo}
        if caption:
            body["caption"] = caption
        return self._request("sendPhoto", body).get("result", {})

    def send_sticker(self, chat_id: str, sticker: str) -> dict:
        """Send a sticker. `sticker` is a value sourced from https://stickers.zaloapp.com/."""
        return self._request("sendSticker", {"chat_id": chat_id, "sticker": sticker}).get("result", {})

    # ── webhook setup ─────────────────────────────────────────────────────
    def set_webhook(self, url: str, secret_token: str) -> dict:
        """Register the HTTPS webhook URL. secret_token (8-256 chars) is echoed
        back by Zalo on every push in the X-Bot-Api-Secret-Token header."""
        if not _SECRET_RE.fullmatch(secret_token or ""):
            raise ZaloBotError(
                "secret_token must be 8-256 chars of A-Z a-z 0-9 _ - only "
                "(got an invalid value — did you reuse the bot token by mistake?)"
            )
        return self._request("setWebhook", {"url": url, "secret_token": secret_token}).get("result", {})

    def get_webhook_info(self) -> dict:
        return self._request("getWebhookInfo", {}).get("result", {})

    def delete_webhook(self) -> dict:
        return self._request("deleteWebhook", {}).get("result", {})

    def get_me(self) -> dict:
        return self._request("getMe", {}).get("result", {})
