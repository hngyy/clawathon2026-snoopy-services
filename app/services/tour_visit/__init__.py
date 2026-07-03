"""Tour-visit service registration.

Builds its own repository (service-specific infra) from the general
container.settings, then wires role-keyed tools + prompts into the registry.
The role names ("requester", "owner") must match roles defined in config.yaml.
"""
from __future__ import annotations

import logging

from app.container import Container
from app.services.registry import Service, ServiceRegistry
from app.services.tour_visit.config import load
from app.services.tour_visit.prompts import build_owner_prompt, build_requester_prompt
from app.services.tour_visit.repository import TourRepository
from app.services.tour_visit.sheet_repository import SheetTourRepository
from app.services.tour_visit.tools import build_tools

logger = logging.getLogger("tour_bot.tour_visit")


def register(container: Container, registry: ServiceRegistry) -> None:
    cfg = load(container.config.service("tour_visit"))
    # Prefer the Sheets-backed store: local files are wiped on every runtime
    # redeploy, so a JSON file loses all requests on deploy. Fall back to the file
    # only when Google is not configured (e.g. local dev).
    if container.google_client and container.settings.google_sheet_id:
        repo = SheetTourRepository(container.google_client)
        logger.info("tour_visit using Sheets-backed repository (durable across redeploys)")
    else:
        repo = TourRepository(container.settings.data_dir / "tour_requests.json")
        logger.info("tour_visit using file-backed repository (data_dir JSON; not deploy-durable)")

    tools = build_tools(
        repo=repo,
        mailer=container.mailer,
        cfg=cfg,
        trello_client=container.trello_client,
        google_client=container.google_client,
    )
    registry.add(Service(
        key="tour_visit",
        display_name=cfg.display_name,
        description="Requesting, booking, and managing guided campus tour visits.",
        tools=tools,
        prompts={
            "requester": build_requester_prompt(cfg.request_fields),
            "owner": build_owner_prompt(cfg.teams),
        },
    ))
