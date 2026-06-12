"""Tour-visit service registration.

Builds its own repository (service-specific infra) from the general
container.settings, then wires role-keyed tools + prompts into the registry.
The role names ("requester", "owner") must match roles defined in config.yaml.
"""
from __future__ import annotations

from app.container import Container
from app.services.registry import Service, ServiceRegistry
from app.services.tour_visit.config import load
from app.services.tour_visit.prompts import build_owner_prompt, build_requester_prompt
from app.services.tour_visit.repository import TourRepository
from app.services.tour_visit.tools import build_tools


def register(container: Container, registry: ServiceRegistry) -> None:
    cfg = load(container.config.service("tour_visit"))
    repo = TourRepository(container.settings.data_dir / "tour_requests.json")

    tools = build_tools(repo=repo, mailer=container.mailer, cfg=cfg)
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
