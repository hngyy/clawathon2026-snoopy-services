"""Google Sheets + Calendar client — shared task sheet and campus calendar.

Auth uses a service account (no browser login; works headlessly on the runtime).
GOOGLE_SERVICE_ACCOUNT may be either a path to the key file OR the raw JSON content
(handy for secret injection on the deployed runtime).

The google-* libraries are imported lazily inside the client so the app still runs
when Google is unconfigured or the libraries aren't installed. Every method raises
GoogleClientError on failure so callers can fall back gracefully.

Shared sheet layout (first tab) — one row per team task:
    A timestamp | B request_id | C team | D organization | E visit_date
    F responsibilities | G note | H status | I updated_by | J updated_at
Teams update column H ("Status": Not started / In progress / Done) themselves.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta

logger = logging.getLogger("tour_bot.google")

_SHEET_RANGE = "A:J"
_STATUS_DEFAULT = "Not started"


class GoogleClientError(RuntimeError):
    pass


class GoogleClient:
    SCOPES = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/calendar",
    ]

    def __init__(self, service_account: str, sheet_id: str | None, calendar_id: str | None):
        # Nothing loaded here — credentials, libraries, and the SA file are all
        # resolved lazily on first use, so constructing the client never fails.
        # A missing/invalid SA only surfaces when an operation actually runs, where
        # the caller (notify_team / update_tour_status) falls back to email.
        self._service_account = service_account
        self._sheet_id = sheet_id
        self._calendar_id = calendar_id
        self._credentials = None  # built lazily
        self._sheets = None
        self._calendar = None

    # ── auth + service builders (lazy) ────────────────────────────────────────
    def _creds(self):
        if self._credentials is None:
            try:
                from google.oauth2 import service_account as sa
            except ImportError as exc:  # pragma: no cover
                raise GoogleClientError(
                    "google-auth / google-api-python-client not installed."
                ) from exc

            value = (self._service_account or "").strip()
            if not value:
                raise GoogleClientError("GOOGLE_SERVICE_ACCOUNT is not configured.")
            try:
                if value.startswith("{"):
                    info = json.loads(value)
                    self._credentials = sa.Credentials.from_service_account_info(info, scopes=self.SCOPES)
                else:
                    self._credentials = sa.Credentials.from_service_account_file(value, scopes=self.SCOPES)
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                raise GoogleClientError(f"Could not load Google service account: {exc}") from exc
        return self._credentials

    def _sheets_api(self):
        if self._sheets is None:
            from googleapiclient.discovery import build
            self._sheets = build("sheets", "v4", credentials=self._creds(), cache_discovery=False)
        return self._sheets

    def _calendar_api(self):
        if self._calendar is None:
            from googleapiclient.discovery import build
            self._calendar = build("calendar", "v3", credentials=self._creds(), cache_discovery=False)
        return self._calendar

    # ── Sheets ────────────────────────────────────────────────────────────────
    def add_task_row(
        self,
        team_name: str,
        request_id: str,
        organization: str,
        visit_date: str,
        responsibilities: list,
        note: str,
    ) -> None:
        if not self._sheet_id:
            raise GoogleClientError("GOOGLE_SHEET_ID is not configured.")
        row = [
            datetime.now().isoformat(timespec="seconds"),
            request_id,
            team_name,
            organization,
            visit_date,
            "; ".join(responsibilities),
            note,
            _STATUS_DEFAULT,
            "",  # updated_by — filled by the team
            "",  # updated_at — filled by the team
        ]
        self._sheets_api().spreadsheets().values().append(
            spreadsheetId=self._sheet_id,
            range=_SHEET_RANGE,
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": [row]},
        ).execute()
        logger.info("Appended sheet task row for %s / %s", request_id, team_name)

    def get_task_rows(self, request_id: str) -> list:
        """Return [{team, status, note, updated_by, updated_at}] for one request."""
        if not self._sheet_id:
            raise GoogleClientError("GOOGLE_SHEET_ID is not configured.")
        result = self._sheets_api().spreadsheets().values().get(
            spreadsheetId=self._sheet_id, range=_SHEET_RANGE,
        ).execute()
        values = result.get("values", [])
        rows = []
        for r in values:
            # Skip a header row and any row not matching this request.
            if len(r) < 2 or r[1] != request_id:
                continue
            rows.append({
                "team": r[2] if len(r) > 2 else "",
                "status": r[7] if len(r) > 7 else _STATUS_DEFAULT,
                "note": r[6] if len(r) > 6 else "",
                "updated_by": r[8] if len(r) > 8 else "",
                "updated_at": r[9] if len(r) > 9 else "",
            })
        return rows

    # ── Calendar ────────────────────────────────────────────────────────────--
    def create_visit_event(
        self,
        request_id: str,
        organization: str,
        visit_date: str,
        group_size: int,
        purpose: str,
        visit_type: str,
    ) -> str:
        """Create an all-day event on the shared calendar. Returns the event htmlLink.

        visit_date is free text; we parse best-effort. On parse failure we still
        create the event (on today's date) with the raw text flagged in the title,
        so the visit is never silently dropped.
        """
        if not self._calendar_id:
            raise GoogleClientError("GOOGLE_CALENDAR_ID is not configured.")

        day, summary_suffix = self._parse_visit_day(visit_date)
        summary = f"Campus Visit — {organization} ({group_size} guests){summary_suffix}"
        description = (
            f"Request ID: {request_id}\n"
            f"Organization: {organization}\n"
            f"Visit type: {visit_type}\n"
            f"Requested date (raw): {visit_date}\n"
            f"Group size: {group_size}\n"
            f"Purpose: {purpose}"
        )
        body = {
            "summary": summary,
            "description": description,
            "start": {"date": day.isoformat()},
            "end": {"date": (day + timedelta(days=1)).isoformat()},
        }
        event = self._calendar_api().events().insert(calendarId=self._calendar_id, body=body).execute()
        logger.info("Created calendar event %s for %s", event.get("id"), request_id)
        return event.get("htmlLink", event.get("id", ""))

    @staticmethod
    def _parse_visit_day(visit_date: str):
        """Best-effort date parse. Returns (date, summary_suffix)."""
        from dateutil import parser as date_parser

        try:
            parsed = date_parser.parse(visit_date, fuzzy=True)
            return parsed.date(), ""
        except (ValueError, OverflowError):
            logger.warning("Could not parse visit_date '%s' — using today + flag", visit_date)
            return datetime.now().date(), f"  [CHECK DATE: '{visit_date}']"
