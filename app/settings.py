"""Environment-driven settings (LLM provider + general paths).

Kept separate from `config.py` (which reads config.yaml): env holds secrets and
deployment wiring, config.yaml holds business configuration. Nothing here is
service-specific — services derive their own paths under `data_dir`.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from app.credentials import load_credentials


@dataclass(frozen=True)
class Settings:
    llm_model: str
    llm_base_url: str
    llm_api_key: str
    config_path: Path
    data_dir: Path
    outbox_path: Path
    memory_id: str | None
    memory_strategy_id: str | None
    history_token_budget: int  # max tokens of conversation history sent to the LLM per turn
    # Trello (BIE team cards) — optional; unset → notify_team falls back to email.
    trello_api_key: str | None
    trello_token: str | None
    # Google (shared sheet + campus calendar) — optional; unset → falls back to email / skips calendar.
    google_service_account: str | None  # path to a key file OR raw JSON content
    google_sheet_id: str | None
    google_calendar_id: str | None
    # Zalo Bot Platform (bot.zaloplatforms.com) — optional; unset → /zalo/webhook is not mounted.
    zalo_bot_token: str | None
    zalo_webhook_secret: str | None
    zalo_owner_password: str | None  # enables `/owner <password>` in-chat elevation; unset → disabled

    @property
    def zalo_enabled(self) -> bool:
        return bool(self.zalo_bot_token)

    @property
    def memory_enabled(self) -> bool:
        return bool(self.memory_id and self.memory_strategy_id)

    @classmethod
    def from_env(cls) -> "Settings":
        llm_model = os.environ.get("LLM_MODEL", "")
        llm_base_url = os.environ.get("LLM_BASE_URL", "")
        llm_api_key = os.environ.get("LLM_API_KEY", "")
        if not (llm_model and llm_base_url and llm_api_key):
            raise ValueError(
                "LLM_MODEL, LLM_BASE_URL, and LLM_API_KEY are required. "
                "Set them in .env or use /agentbase-llm to get a platform API key."
            )
        return cls(
            llm_model=llm_model,
            llm_base_url=llm_base_url,
            llm_api_key=llm_api_key,
            config_path=Path(os.environ.get("CONFIG_PATH", "config.yaml")),
            data_dir=Path(os.environ.get("DATA_DIR", ".")),
            outbox_path=Path(os.environ.get("OUTBOX_PATH", "outbox.log")),
            memory_id=os.environ.get("MEMORY_ID") or None,
            memory_strategy_id=os.environ.get("MEMORY_STRATEGY_ID") or None,
            history_token_budget=int(os.environ.get("HISTORY_TOKEN_BUDGET", "8000")),
            **_integration_credentials(),
            **_zalo_credentials(),
        )


def _zalo_credentials() -> dict:
    """Zalo Bot token + webhook secret, sourced from app/credentials/zalo.credentials.json
    (env overrides file), same resolution as the other integrations."""
    zalo = load_credentials("zalo", {
        "bot_token": "ZALO_BOT_TOKEN",
        "webhook_secret": "ZALO_WEBHOOK_SECRET",
        "owner_password": "ZALO_OWNER_PASSWORD",
    })
    return {
        "zalo_bot_token": zalo["bot_token"],
        "zalo_webhook_secret": zalo["webhook_secret"],
        "zalo_owner_password": zalo["owner_password"],
    }


def _integration_credentials() -> dict:
    """Trello + Google config, sourced from per-service credential files
    (env overrides file). See app/infrastructure/credentials.py."""
    trello = load_credentials("trello", {
        "api_key": "TRELLO_API_KEY",
        "token": "TRELLO_TOKEN",
    })
    google = load_credentials("google", {
        "service_account": "GOOGLE_SERVICE_ACCOUNT",
        "sheet_id": "GOOGLE_SHEET_ID",
        "calendar_id": "GOOGLE_CALENDAR_ID",
    })
    return {
        "trello_api_key": trello["api_key"],
        "trello_token": trello["token"],
        "google_service_account": google["service_account"],
        "google_sheet_id": google["sheet_id"],
        "google_calendar_id": google["calendar_id"],
    }
