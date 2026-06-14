"""Tour-visit service configuration.

Parsed from the raw dict at config.yaml → services.tour_visit.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from app.services.config import ServiceConfig, load_rules


@dataclass(frozen=True)
class RequestField:
    key: str
    label: str
    required: bool = True


@dataclass(frozen=True)
class Team:
    key: str
    name: str
    email: str
    channel: str = "email"  # "trello" | "sheets" | "email" — how the owner reaches this team
    responsibilities: tuple = ()
    trello_list_id: str = ""  # Trello list to create cards in (only for channel="trello")


@dataclass(frozen=True)
class TourServiceConfig(ServiceConfig):
    owner_name: str = ""
    owner_email: str = ""
    teams: list = field(default_factory=list)
    request_fields: list = field(default_factory=list)

    def team(self, key: str) -> "Team | None":
        return next((t for t in self.teams if t.key == key), None)


def load(raw: dict) -> TourServiceConfig:
    owner_raw = raw.get("owner", {})
    rules_dir = Path(raw.get("rules_dir", "knowledge_base/tour_visit"))
    return TourServiceConfig(
        display_name=raw.get("display_name", "Tour Visit"),
        enabled=raw.get("enabled", True),
        rules=load_rules(rules_dir),
        owner_name=owner_raw.get("name", "Tour Coordinator"),
        owner_email=owner_raw.get("email", ""),
        teams=[
            Team(
                key=t["key"],
                name=t.get("name", t["key"]),
                email=t.get("email", ""),
                channel=t.get("channel", "email"),
                responsibilities=tuple(t.get("responsibilities", [])),
                trello_list_id=t.get("trello_list_id", ""),
            )
            for t in raw.get("teams", [])
        ],
        request_fields=[
            RequestField(
                key=f["key"],
                label=f.get("label", f["key"]),
                required=f.get("required", True),
            )
            for f in raw.get("request_fields", [])
        ],
    )
