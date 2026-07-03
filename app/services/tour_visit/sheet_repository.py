"""Google Sheets-backed persistence for tour requests.

Drop-in alternative to TourRepository with the identical public contract
(create / get / list / update) — but durable across container redeploys. The
runtime's local filesystem is wiped on every new version, so a local JSON file
loses every request on deploy; a Sheet lives outside the container.

Layout: a dedicated `TourRequests` tab, one row per request —
    A id | B status | C organization | D visit_date | E updated_at | F json
Column F is the full serialized TourRequest (the source of truth); A–E are
denormalized for humans skimming the sheet. A process-level lock serializes the
read-modify-write in update() (the runtime runs a single replica).
"""
from __future__ import annotations

import json
import logging
import threading
import uuid
from datetime import datetime

from app.infrastructure.google_client import GoogleClient
from app.services.tour_visit.domain import StatusEvent, TourRequest, TourStatus

logger = logging.getLogger("tour_bot.tour_visit")

_TAB = "TourRequests"
_HEADER = ["id", "status", "organization", "visit_date", "updated_at", "json"]
_JSON_COL = 5  # index of column F in a row


class SheetTourRepository:
    def __init__(self, google: GoogleClient, tab: str = _TAB):
        self._google = google
        self._tab = tab
        self._lock = threading.Lock()
        self._ready = False

    # ── helpers ─────────────────────────────────────────────────────────
    def _ensure(self) -> None:
        if not self._ready:
            self._google.ensure_tab(self._tab, _HEADER)
            self._ready = True

    def _row(self, request: TourRequest) -> list:
        return [
            request.id, request.status, request.organization,
            request.visit_date, request.updated_at,
            json.dumps(request.to_dict(), ensure_ascii=False),
        ]

    @staticmethod
    def _parse(row: list) -> "TourRequest | None":
        if len(row) <= _JSON_COL or not row[_JSON_COL]:
            return None
        try:
            return TourRequest.from_dict(json.loads(row[_JSON_COL]))
        except (ValueError, json.JSONDecodeError):
            logger.warning("Skipping unparseable tour-request row id=%s", row[0] if row else "?")
            return None

    def _find(self, req_id: str):
        """Return (row_number, TourRequest) for req_id, or (None, None).
        row_number is 1-based (header is row 1)."""
        rows = self._google.read_tab(self._tab)
        for idx, row in enumerate(rows):
            if idx == 0 or not row or row[0] != req_id:
                continue  # header or non-match
            return idx + 1, self._parse(row)
        return None, None

    # ── public API (mirrors TourRepository) ─────────────────────────────
    def create(
        self,
        requester_name: str,
        organization: str,
        visit_date: str,
        group_size: int,
        purpose: str,
        contact_email: str,
        visit_time: str = "",
        visit_type: str = "",
        guest_profile: str = "",
        partner_gift: str = "",
        meeting_topic: str = "",
    ) -> TourRequest:
        with self._lock:
            self._ensure()
            now = datetime.now().isoformat()
            request = TourRequest(
                id="TOUR-" + uuid.uuid4().hex[:8].upper(),
                requester_name=requester_name,
                organization=organization,
                visit_date=visit_date,
                group_size=group_size,
                purpose=purpose,
                contact_email=contact_email,
                visit_time=visit_time,
                visit_type=visit_type,
                guest_profile=guest_profile,
                partner_gift=partner_gift,
                meeting_topic=meeting_topic,
                status=TourStatus.NEW.value,
                created_at=now,
                updated_at=now,
                history=[StatusEvent(at=now, status=TourStatus.NEW.value, note="Request submitted").__dict__],
            )
            self._google.append_tab_row(self._tab, self._row(request))
            return request

    def get(self, req_id: str) -> "TourRequest | None":
        with self._lock:
            self._ensure()
            _, request = self._find(req_id)
            return request

    def list(self, status: str | None = None) -> list:
        with self._lock:
            self._ensure()
            rows = self._google.read_tab(self._tab)
        items = []
        for idx, row in enumerate(rows):
            if idx == 0:
                continue  # header
            request = self._parse(row)
            if request:
                items.append(request)
        if status:
            items = [r for r in items if r.status == status]
        return sorted(items, key=lambda r: r.created_at, reverse=True)

    def update(
        self,
        req_id: str,
        status: str | None = None,
        note: str | None = None,
        external_refs: dict | None = None,
        visit_date: str | None = None,
        visit_time: str | None = None,
    ) -> "TourRequest | None":
        with self._lock:
            self._ensure()
            row_number, request = self._find(req_id)
            if not request:
                return None
            now = datetime.now().isoformat()
            if status:
                request.status = status
            if visit_date:
                request.visit_date = visit_date
            if visit_time:
                request.visit_time = visit_time
            if external_refs:
                # Shallow-merge so e.g. {"trello": {"bie": id}} doesn't clobber other teams.
                for key, value in external_refs.items():
                    if isinstance(value, dict) and isinstance(request.external_refs.get(key), dict):
                        request.external_refs[key].update(value)
                    else:
                        request.external_refs[key] = value
            request.updated_at = now
            # Only record a history event for meaningful status/note changes — not for
            # bookkeeping-only updates (e.g. saving an external ref).
            if status or note:
                request.history.append(
                    StatusEvent(at=now, status=status or request.status, note=note or "").__dict__
                )
            self._google.update_tab_row(self._tab, row_number, self._row(request))
            return request
