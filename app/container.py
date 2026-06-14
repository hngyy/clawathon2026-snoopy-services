"""Composition root dependencies.

The Container holds only GENERAL infrastructure that any service may use
(settings, parsed config, outlook sender, optional memory client). It is built once in
`server.create_app()` and passed to each service's `register(...)`.

Service-specific infrastructure (e.g. a tour-request repository) is NOT here —
each service builds its own from `container.settings` / `container.config`, so
adding a service never touches this file.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.config import AppConfig
from app.infrastructure.google_client import GoogleClient
from app.infrastructure.outlook_client import OutlookSender
from app.infrastructure.trello_client import TrelloClient
from app.infrastructure.zalo_bot_client import ZaloBotClient
from app.settings import Settings


@dataclass(frozen=True)
class Container:
    settings: Settings
    config: AppConfig
    mailer: OutlookSender
    trello_client: TrelloClient | None  # built only when Trello creds are set
    google_client: GoogleClient | None  # built only when a service account is set
    zalo_client: ZaloBotClient | None  # built only when a Zalo bot token is set
    memory_client: object | None  # greennode_agentbase.memory.MemoryClient when memory enabled

    @classmethod
    def build(cls, settings: Settings, config: AppConfig) -> "Container":
        memory_client = None
        if settings.memory_enabled:
            from greennode_agentbase.memory import MemoryClient
            memory_client = MemoryClient()

        trello_client = None
        if settings.trello_api_key and settings.trello_token:
            trello_client = TrelloClient(settings.trello_api_key, settings.trello_token)

        google_client = None
        if settings.google_service_account:
            google_client = GoogleClient(
                service_account=settings.google_service_account,
                sheet_id=settings.google_sheet_id,
                calendar_id=settings.google_calendar_id,
            )

        zalo_client = ZaloBotClient(settings.zalo_bot_token) if settings.zalo_enabled else None

        return cls(
            settings=settings,
            config=config,
            mailer=OutlookSender(
                from_address=config.email.from_address,
                from_name=config.email.from_name,
                outbox_path=settings.outbox_path,
            ),
            trello_client=trello_client,
            google_client=google_client,
            zalo_client=zalo_client,
            memory_client=memory_client,
        )
