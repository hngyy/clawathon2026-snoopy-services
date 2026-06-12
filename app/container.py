"""Composition root dependencies.

The Container holds only GENERAL infrastructure that any service may use
(settings, parsed config, mailer, optional memory client). It is built once in
`server.create_app()` and passed to each service's `register(...)`.

Service-specific infrastructure (e.g. a tour-request repository) is NOT here —
each service builds its own from `container.settings` / `container.config`, so
adding a service never touches this file.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.config import AppConfig
from app.infrastructure.mailer import EmailSender
from app.settings import Settings


@dataclass(frozen=True)
class Container:
    settings: Settings
    config: AppConfig
    mailer: EmailSender
    memory_client: object | None  # greennode_agentbase.memory.MemoryClient when memory enabled

    @classmethod
    def build(cls, settings: Settings, config: AppConfig) -> "Container":
        memory_client = None
        if settings.memory_enabled:
            from greennode_agentbase.memory import MemoryClient
            memory_client = MemoryClient()

        return cls(
            settings=settings,
            config=config,
            mailer=EmailSender(
                from_address=config.email.from_address,
                from_name=config.email.from_name,
                outbox_path=settings.outbox_path,
            ),
            memory_client=memory_client,
        )
