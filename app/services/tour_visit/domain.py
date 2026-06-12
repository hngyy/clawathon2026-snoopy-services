"""Domain models for the tour-visit service. Pure data — no I/O, no framework."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum


class TourStatus(str, Enum):
    NEW = "new"
    IN_REVIEW = "in_review"
    APPROVED = "approved"
    SCHEDULED = "scheduled"
    COMPLETED = "completed"
    REJECTED = "rejected"

    @classmethod
    def values(cls) -> list:
        return [s.value for s in cls]

    @classmethod
    def is_valid(cls, value: str) -> bool:
        return value in cls.values()


@dataclass
class StatusEvent:
    at: str
    status: str
    note: str = ""


@dataclass
class TourRequest:
    id: str
    requester_name: str    # internal VNG employee who organizes the visit (NOT the guest)
    organization: str      # guest's organization / company being hosted
    visit_date: str
    group_size: int        # number of external guests
    purpose: str
    contact_email: str     # requester's contact for confirmation
    visit_type: str = ""       # "tour only" | "tour + meeting"
    guest_profile: str = ""    # background/seniority of the visiting guests
    partner_gift: str = ""     # selected partner gift (catalog pick wired later)
    meeting_topic: str = ""    # only when visit_type includes a meeting
    status: str = TourStatus.NEW.value
    created_at: str = ""
    updated_at: str = ""
    history: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "TourRequest":
        return cls(**data)
