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
from datetime import datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo

logger = logging.getLogger("tour_bot.google")

_SHEET_RANGE = "A:J"
_STATUS_DEFAULT = "Not started"

# Availability / scheduling constants (could move to config.yaml later).
_TZ_NAME = "Asia/Ho_Chi_Minh"
_TZ = ZoneInfo(_TZ_NAME)
_WORK_START = 9            # bookable hours: 09:00 ..
_WORK_END = 17            # .. 17:00
_DEFAULT_DURATION_H = 2    # assumed visit length when only a start time is given
_SUGGEST_DAYS = 14         # how far ahead to look for free slots
_SLOT_STEP_MIN = 30        # granularity when scanning for free slots


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
        visit_time: str = "",
    ) -> str:
        """Create an event on the shared calendar. Returns the event htmlLink.

        Creates a TIMED event when visit_date + visit_time parse into a window
        (so time-slot availability checks are meaningful); otherwise falls back to
        an all-day event with the raw date flagged, so the visit is never dropped.
        """
        if not self._calendar_id:
            raise GoogleClientError("GOOGLE_CALENDAR_ID is not configured.")

        description = (
            f"Request ID: {request_id}\n"
            f"Organization: {organization}\n"
            f"Visit type: {visit_type}\n"
            f"Requested date (raw): {visit_date}\n"
            f"Requested time (raw): {visit_time}\n"
            f"Group size: {group_size}\n"
            f"Purpose: {purpose}"
        )
        summary = f"Campus Visit — {organization} ({group_size} guests)"

        window = self._parse_visit_window(visit_date, visit_time)
        if window:
            start_dt, end_dt = window
            body = {
                "summary": summary,
                "description": description,
                "start": {"dateTime": start_dt.isoformat(), "timeZone": _TZ_NAME},
                "end": {"dateTime": end_dt.isoformat(), "timeZone": _TZ_NAME},
            }
        else:
            day, summary_suffix = self._parse_visit_day(visit_date)
            body = {
                "summary": summary + summary_suffix,
                "description": description,
                "start": {"date": day.isoformat()},
                "end": {"date": (day + timedelta(days=1)).isoformat()},
            }
        event = self._calendar_api().events().insert(calendarId=self._calendar_id, body=body).execute()
        logger.info("Created calendar event %s for %s", event.get("id"), request_id)
        return event.get("htmlLink", event.get("id", ""))

    # ── Availability ──────────────────────────────────────────────────────────
    def find_conflicts(self, start_dt: datetime, end_dt: datetime) -> list:
        """Return [{summary, start, end}] for calendar events overlapping [start, end).
        All-day events block that day's full working hours."""
        if not self._calendar_id:
            raise GoogleClientError("GOOGLE_CALENDAR_ID is not configured.")
        day_start = start_dt.astimezone(_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = end_dt.astimezone(_TZ).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        events = self._list_events(day_start, day_end)
        conflicts = []
        for ev in events:
            ev_start, ev_end = self._event_window(ev)
            if ev_start and ev_start < end_dt and start_dt < ev_end:  # half-open overlap
                conflicts.append({"summary": ev.get("summary", "(busy)"), "start": ev_start, "end": ev_end})
        return conflicts

    def suggest_free_slots(self, from_dt: datetime, duration_h: int = _DEFAULT_DURATION_H, count: int = 3) -> list:
        """Return up to `count` free (start, end) windows of `duration_h` hours within
        working hours, starting from `from_dt`'s day and scanning forward."""
        if not self._calendar_id:
            raise GoogleClientError("GOOGLE_CALENDAR_ID is not configured.")
        duration = timedelta(hours=duration_h)
        now = datetime.now(_TZ)
        base_day = max(from_dt.astimezone(_TZ), now).date()
        suggestions: list = []
        for d in range(_SUGGEST_DAYS):
            day = base_day + timedelta(days=d)
            day_start = datetime.combine(day, dtime(0, 0), tzinfo=_TZ)
            events = self._list_events(day_start, day_start + timedelta(days=1))
            windows = [self._event_window(ev) for ev in events]
            cursor = datetime.combine(day, dtime(_WORK_START, 0), tzinfo=_TZ)
            close = datetime.combine(day, dtime(_WORK_END, 0), tzinfo=_TZ)
            while cursor + duration <= close:
                cand_end = cursor + duration
                if cursor >= now and not any(s and s < cand_end and cursor < e for s, e in windows):
                    suggestions.append((cursor, cand_end))
                    if len(suggestions) >= count:
                        return suggestions
                cursor += timedelta(minutes=_SLOT_STEP_MIN)
        return suggestions

    def _list_events(self, time_min: datetime, time_max: datetime) -> list:
        return self._calendar_api().events().list(
            calendarId=self._calendar_id,
            timeMin=time_min.isoformat(),
            timeMax=time_max.isoformat(),
            singleEvents=True,
            orderBy="startTime",
        ).execute().get("items", [])

    @staticmethod
    def _event_window(ev: dict):
        """Resolve an event to a tz-aware (start, end). All-day events block the
        working hours of their start day. Returns (None, None) if unresolvable."""
        from dateutil import parser as date_parser

        s, e = ev.get("start", {}), ev.get("end", {})
        try:
            if s.get("dateTime"):
                return (date_parser.isoparse(s["dateTime"]).astimezone(_TZ),
                        date_parser.isoparse(e["dateTime"]).astimezone(_TZ))
            if s.get("date"):
                day = date_parser.parse(s["date"]).date()
                return (datetime.combine(day, dtime(_WORK_START, 0), tzinfo=_TZ),
                        datetime.combine(day, dtime(_WORK_END, 0), tzinfo=_TZ))
        except (ValueError, OverflowError):
            pass
        return (None, None)

    @classmethod
    def _parse_visit_window(cls, visit_date: str, visit_time: str):
        """Parse visit_date + visit_time into a tz-aware (start, end). Accepts
        "14:00-16:00", "2pm-4pm", or a single start ("2pm" → +default duration).
        Returns None if either part is unparseable."""
        from dateutil import parser as date_parser

        try:
            day = date_parser.parse(visit_date, fuzzy=True).date()
        except (ValueError, OverflowError):
            return None
        vt = (visit_time or "").strip().lower().replace("–", "-").replace(" to ", "-")
        parts = [p.strip() for p in vt.split("-") if p.strip()]
        if not parts:
            return None
        try:
            start_t = date_parser.parse(parts[0]).time()
            end_t = date_parser.parse(parts[1]).time() if len(parts) >= 2 else None
        except (ValueError, OverflowError):
            return None
        start_dt = datetime.combine(day, start_t, tzinfo=_TZ)
        end_dt = datetime.combine(day, end_t, tzinfo=_TZ) if end_t else start_dt + timedelta(hours=_DEFAULT_DURATION_H)
        if end_dt <= start_dt:
            end_dt = start_dt + timedelta(hours=_DEFAULT_DURATION_H)
        return start_dt, end_dt

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
