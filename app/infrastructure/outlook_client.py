"""Email delivery via Microsoft Graph (Outlook) with outbox-stub fallback.

Auth uses a self-contained delegated OAuth device-code flow. Tokens are cached on
disk under the `mail_oauth` key in app/credentials/cache/o365_token_cache.json.

One-time setup (run once per deployment / token expiry):
    python -m app.infrastructure.outlook_client login

Config — `app/credentials/o365.credentials.json` (env overrides file):
    {"tenant_id": "<Azure AD tenant ID>", "client_id": "<app client ID w/ Mail.Send>"}
    Env equivalents: O365_TENANT_ID, O365_CLIENT_ID.
Optional: O365_TOKEN_CACHE_PATH — override the token cache file path.

When Outlook is not connected or the Graph call fails, send() falls back to
appending the email to outbox.log so the full flow stays testable without auth.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path

import requests

from app.credentials import CACHE_DIR, load_credentials

logger = logging.getLogger("tour_bot.outlook")

_GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_SCOPES = ["Mail.Send", "offline_access", "Mail.ReadWrite"]
_TIMEOUT = 20
_CACHE_KEY = "mail_oauth"


def _load_o365_config() -> tuple[str, str, Path]:
    """Resolve (tenant_id, client_id, token_cache_path) from o365.credentials.json
    (env overrides file). The token cache keeps its own env var + default."""
    creds = load_credentials("o365", {"tenant_id": "O365_TENANT_ID", "client_id": "O365_CLIENT_ID"})
    cache = Path(os.getenv("O365_TOKEN_CACHE_PATH") or CACHE_DIR / "o365_token_cache.json")
    return creds["tenant_id"] or "", creds["client_id"] or "", cache


_TENANT_ID, _CLIENT_ID, _TOKEN_CACHE = _load_o365_config()

_http = requests.Session()
_token_lock = threading.Lock()
_mem_token: dict | None = None


class OutlookError(RuntimeError):
    pass


# ── Token management ──────────────────────────────────────────────────────────

def _read_cache_entry() -> dict:
    try:
        data = json.loads(_TOKEN_CACHE.read_text())
        entry = data.get(_CACHE_KEY, {})
        return entry if isinstance(entry, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _write_cache_entry(token: dict) -> None:
    global _mem_token
    existing_refresh = _read_cache_entry().get("refresh_token")
    cached = {
        "access_token": token["access_token"],
        "refresh_token": token.get("refresh_token") or existing_refresh,
        "expires_at": time.time() + int(token.get("expires_in") or 3600) - 60,
    }
    try:
        file_data = json.loads(_TOKEN_CACHE.read_text()) if _TOKEN_CACHE.exists() else {}
    except (json.JSONDecodeError, OSError):
        file_data = {}
    file_data[_CACHE_KEY] = cached
    _TOKEN_CACHE.parent.mkdir(parents=True, exist_ok=True)
    _TOKEN_CACHE.write_text(json.dumps(file_data, indent=2, sort_keys=True))
    with _token_lock:
        _mem_token = cached


def _token_url() -> str:
    return f"https://login.microsoftonline.com/{_TENANT_ID}/oauth2/v2.0/token"


def _do_refresh(refresh_token: str) -> dict:
    resp = _http.post(
        _token_url(),
        data={
            "grant_type": "refresh_token",
            "client_id": _CLIENT_ID,
            "refresh_token": refresh_token,
            "scope": " ".join(_SCOPES),
        },
        timeout=_TIMEOUT,
    )
    result = resp.json()
    if "access_token" in result:
        return result
    detail = result.get("error_description") or result.get("error") or str(result)
    raise OutlookError(f"Token refresh failed: {detail}")


def _get_access_token() -> str:
    global _mem_token

    with _token_lock:
        tok = _mem_token
        if tok and float(tok.get("expires_at") or 0) > time.time():
            return tok["access_token"]
        cached = _read_cache_entry()
        if cached.get("access_token") and float(cached.get("expires_at") or 0) > time.time():
            _mem_token = cached
            return cached["access_token"]
        refresh_token = cached.get("refresh_token")

    if not refresh_token:
        raise OutlookError(
            "Outlook mail is not connected. "
            "Run `python -m app.infrastructure.outlook_client login` once to authenticate."
        )
    refreshed = _do_refresh(refresh_token)
    _write_cache_entry(refreshed)
    return refreshed["access_token"]


# ── Device-code login (one-time setup) ───────────────────────────────────────

def login() -> None:
    """Interactive device-code login — run once from the terminal."""
    if not _TENANT_ID or not _CLIENT_ID:
        raise OutlookError("O365_TENANT_ID and O365_CLIENT_ID must be set to log in.")

    device_url = f"https://login.microsoftonline.com/{_TENANT_ID}/oauth2/v2.0/devicecode"
    resp = _http.post(
        device_url,
        data={"client_id": _CLIENT_ID, "scope": " ".join(_SCOPES)},
        timeout=_TIMEOUT,
    )
    flow = resp.json()
    if "user_code" not in flow:
        raise OutlookError(f"Device login failed: {flow.get('error_description') or flow}")

    print(flow["message"])
    deadline = time.time() + int(flow.get("expires_in") or 900)
    interval = int(flow.get("interval") or 5)

    while time.time() < deadline:
        poll = _http.post(
            _token_url(),
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "client_id": _CLIENT_ID,
                "device_code": flow["device_code"],
            },
            timeout=_TIMEOUT,
        ).json()
        if "access_token" in poll:
            _write_cache_entry(poll)
            print("Outlook mail connected.")
            return
        error = poll.get("error")
        if error == "authorization_pending":
            time.sleep(interval)
        elif error == "slow_down":
            interval += 5
            time.sleep(interval)
        else:
            raise OutlookError(f"Login failed: {poll.get('error_description') or poll}")

    raise OutlookError("Device login timed out.")


# ── Graph sendMail ────────────────────────────────────────────────────────────

def _send_via_graph(to: str, subject: str, body: str, cc: list | None = None) -> None:
    def _recipients(addrs: list[str]) -> list[dict]:
        return [{"emailAddress": {"address": a}} for a in addrs if a]

    payload: dict = {
        "message": {
            "subject": subject,
            "body": {"contentType": "Text", "content": body},
            "toRecipients": _recipients([to]),
        },
        "saveToSentItems": True,
    }
    if cc:
        payload["message"]["ccRecipients"] = _recipients(cc)

    token = _get_access_token()
    resp = _http.post(
        f"{_GRAPH_BASE}/me/sendMail",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=payload,
        timeout=_TIMEOUT,
    )
    if not resp.ok:
        try:
            detail = resp.json().get("error", {}).get("message") or resp.text
        except Exception:
            detail = resp.text
        raise OutlookError(f"Graph sendMail {resp.status_code}: {detail}")


# ── OutlookSender ─────────────────────────────────────────────────────────────

class OutlookSender:
    def __init__(self, from_address: str, from_name: str, outbox_path: Path):
        self._from = f"{from_name} <{from_address}>"
        self._outbox_path = outbox_path

    def send(self, to: str, subject: str, body: str, cc: list | None = None) -> dict:
        """Send via Outlook Graph API; falls back to outbox stub when not connected."""
        record = {
            "timestamp": datetime.now().isoformat(),
            "from": self._from,
            "to": to,
            "cc": cc or [],
            "subject": subject,
            "body": body,
        }
        try:
            _send_via_graph(to=to, subject=subject, body=body, cc=cc)
            logger.info("EMAIL (outlook) -> %s | %s", to, subject)
            return {"delivered": True, **record}
        except Exception as exc:
            logger.warning("Outlook send failed (%s) — falling back to outbox stub", exc)
            self._append_outbox(record)
            logger.info("EMAIL (stub) -> %s | %s", to, subject)
            return {"delivered": False, "stubbed": True, **record}

    def _append_outbox(self, record: dict) -> None:
        line = (
            f"\n=== {record['timestamp']} ===\n"
            f"From: {record['from']}\n"
            f"To: {record['to']}\n"
            f"Cc: {', '.join(record['cc']) if record['cc'] else '-'}\n"
            f"Subject: {record['subject']}\n\n"
            f"{record['body']}\n"
            f"{'-' * 50}\n"
        )
        with open(self._outbox_path, "a", encoding="utf-8") as f:
            f.write(line)


if __name__ == "__main__":
    import sys
    from dotenv import load_dotenv
    load_dotenv()
    # Re-resolve config after loading .env (module globals were set at import, before .env).
    _TENANT_ID, _CLIENT_ID, _TOKEN_CACHE = _load_o365_config()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if len(sys.argv) > 1 and sys.argv[1] == "login":
        try:
            login()
        except OutlookError as exc:
            logger.error("%s", exc)
            sys.exit(1)
    else:
        print("Usage: python -m app.infrastructure.outlook_client login")
