"""File-based persistence for tour requests (v1).

Swap this class for a database-backed one later — the public methods
(`create`, `get`, `list`, `update`) are the contract the service depends on.
"""
from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime
from pathlib import Path

from app.services.tour_visit.domain import StatusEvent, TourRequest, TourStatus


class TourRepository:
    def __init__(self, path: Path):
        self._path = path
        self._lock = threading.Lock()

    # ── storage primitives ────────────────────────────────────────────
    def _read(self) -> dict:
        if not self._path.exists():
            return {}
        with open(self._path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _write(self, data: dict) -> None:
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    # ── public API ─────────────────────────────────────────────────────
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
            data = self._read()
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
            data[request.id] = request.to_dict()
            self._write(data)
            return request

    def get(self, req_id: str) -> "TourRequest | None":
        raw = self._read().get(req_id)
        return TourRequest.from_dict(raw) if raw else None

    def list(self, status: str | None = None) -> list:
        items = [TourRequest.from_dict(r) for r in self._read().values()]
        if status:
            items = [r for r in items if r.status == status]
        return sorted(items, key=lambda r: r.created_at, reverse=True)

    def update(
        self,
        req_id: str,
        status: str | None = None,
        note: str | None = None,
        external_refs: dict | None = None,
    ) -> "TourRequest | None":
        with self._lock:
            data = self._read()
            raw = data.get(req_id)
            if not raw:
                return None
            request = TourRequest.from_dict(raw)
            now = datetime.now().isoformat()
            if status:
                request.status = status
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
            data[req_id] = request.to_dict()
            self._write(data)
            return request
