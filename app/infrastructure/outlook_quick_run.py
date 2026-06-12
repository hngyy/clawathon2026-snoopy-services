from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
import json
import logging
import os
from pathlib import Path
import sys
import threading
import time
from zoneinfo import ZoneInfo

import requests


logger = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
GRAPH_SCOPES = ["User.Read", "Calendars.ReadWrite"]
TOKEN_SCOPES = GRAPH_SCOPES + ["offline_access"]
REQUEST_TIMEOUT = 20

# Cron-friendly config. No app import and no .env loading.
TENANT_ID = "7c112a6e-10e2-4e09-afc4-2e37bc60d821" # contact sonnh3 - Azure - ZA HieuNX M365
CLIENT_ID = "86643fd2-b622-400f-9cfa-b288281e0534" # contact sonnh3 - Azure - ZA HieuNX M365
GRAPH_USER_ID = "hieunx@vng.com.vn"
OUTLOOK_TIMEZONE = "Asia/Ho_Chi_Minh"
TOKEN_CACHE = Path(os.getenv("O365_TOKEN_CACHE_PATH", "./.o365_token_cache.json"))
WAIT_UNTIL_BOOKING_TIME = os.getenv("WAIT_UNTIL_BOOKING_TIME", "false").lower() in {
    "1",
    "true",
    "yes",
    "y",
}
BOOKING_TRIGGER_CLOCKS = os.getenv(
    "BOOKING_TRIGGER_CLOCKS",
    os.getenv("BOOKING_TRIGGER_CLOCK", "23:59:59,00:00:00"),
)

# Event to create when this file is run directly.
EVENT_TITLE = "b349523c3b0aed1792778bc636f37110"
EVENT_WINDOWS = [
    ("09:00:00", "12:00:00"),
    ("14:00:00", "17:00:00"),
]
EVENT_ATTENDEE_NAMES = ["Yangoon Campus", "Jakarta Campus"]
EVENT_ATTENDEE_EMAILS = ["campus.yangoon@vng.com.vn", "campus.jakarta@vng.com.vn"]
BOOK_DAYS_AHEAD = 14

# Pre-computed constants to avoid repeated string construction
_AUTHORITY = f"https://login.microsoftonline.com/{TENANT_ID}"
_DEVICE_CODE_ENDPOINT = f"{_AUTHORITY}/oauth2/v2.0/devicecode"
_TOKEN_ENDPOINT = f"{_AUTHORITY}/oauth2/v2.0/token"
_TOKEN_SCOPES_STR = " ".join(TOKEN_SCOPES)
_PREFER_HEADER = f'outlook.timezone="{OUTLOOK_TIMEZONE}"'
_TZ = ZoneInfo(OUTLOOK_TIMEZONE)
_PREWARM_LEAD_SECONDS = 30

# Shared HTTP session for TCP connection reuse across all Graph API requests
_http = requests.Session()

# In-memory token cache to avoid repeated disk reads during concurrent bookings
_token_lock = threading.Lock()
_mem_token: dict | None = None

_device_flow_lock = threading.Lock()
_DEVICE_FLOW: dict | None = None

_refresh_lock = threading.Lock()


class OutlookQuickRunError(RuntimeError):
    """Raised when Microsoft Graph delegated auth or requests fail."""


def _load_cache_file() -> dict:
    try:
        payload = json.loads(TOKEN_CACHE.read_text())
        return payload if isinstance(payload, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _load_token_cache() -> dict:
    payload = _load_cache_file()
    if isinstance(payload.get("delegated_oauth"), dict):
        return payload["delegated_oauth"]
    if "access_token" in payload:
        return payload
    return {}


def _save_token_cache(token_payload: dict) -> None:
    global _mem_token

    if not token_payload.get("access_token"):
        raise OutlookQuickRunError("Token response did not include access_token.")

    expires_in = int(token_payload.get("expires_in") or 3600)
    # Read file once; reuse for existing refresh_token lookup and final write
    file_payload = _load_cache_file()
    existing_oauth = file_payload.get("delegated_oauth") or {}

    cached = {
        "access_token": token_payload["access_token"],
        "refresh_token": token_payload.get("refresh_token") or existing_oauth.get("refresh_token"),
        "expires_at": time.time() + expires_in - 60,
        "scope": token_payload.get("scope"),
        "token_type": token_payload.get("token_type"),
    }

    file_payload["delegated_oauth"] = cached
    TOKEN_CACHE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_CACHE.write_text(json.dumps(file_payload, indent=2, sort_keys=True))

    with _token_lock:
        _mem_token = cached


def start_device_login() -> dict:
    global _DEVICE_FLOW

    if not CLIENT_ID:
        raise OutlookQuickRunError("Missing CLIENT_ID.")

    resp = _http.post(
        _DEVICE_CODE_ENDPOINT,
        data={"client_id": CLIENT_ID, "scope": _TOKEN_SCOPES_STR},
        timeout=REQUEST_TIMEOUT,
    )
    try:
        flow = resp.json()
    except ValueError as exc:
        raise OutlookQuickRunError(resp.text) from exc

    if "user_code" not in flow:
        error = flow.get("error_description") or flow.get("error") or str(flow)
        raise OutlookQuickRunError(f"Could not start Outlook device login: {error}")

    with _device_flow_lock:
        _DEVICE_FLOW = flow
    return flow


def complete_device_login(flow: dict | None = None) -> dict:
    with _device_flow_lock:
        device_flow = flow or _DEVICE_FLOW
    if not device_flow:
        raise OutlookQuickRunError("No Outlook device login is in progress.")

    result = _poll_device_token(device_flow)
    _save_token_cache(result)
    return check_connection()


def _poll_device_token(device_flow: dict) -> dict:
    deadline = time.time() + int(device_flow.get("expires_in") or 900)
    interval = int(device_flow.get("interval") or 5)

    while time.time() < deadline:
        resp = _http.post(
            _TOKEN_ENDPOINT,
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "client_id": CLIENT_ID,
                "device_code": device_flow["device_code"],
            },
            timeout=REQUEST_TIMEOUT,
        )
        try:
            result = resp.json()
        except ValueError as exc:
            raise OutlookQuickRunError(resp.text) from exc

        if "access_token" in result:
            return result

        error = result.get("error")
        if error == "authorization_pending":
            time.sleep(interval)
            continue
        if error == "slow_down":
            interval += 5
            time.sleep(interval)
            continue

        detail = result.get("error_description") or error or str(result)
        raise OutlookQuickRunError(f"Could not complete Outlook device login: {detail}")

    raise OutlookQuickRunError("Outlook device login expired. Start login again.")


def login_with_device_code() -> dict:
    flow = start_device_login()
    print(flow["message"])
    return complete_device_login(flow)


def _get_access_token() -> str:
    global _mem_token

    # Fast path: valid token already in memory
    with _token_lock:
        tok = _mem_token
        if tok and float(tok.get("expires_at") or 0) > time.time():
            return tok["access_token"]

        # Cold path: read disk once under lock to avoid redundant concurrent reads
        cached = _load_token_cache()
        if cached.get("access_token") and float(cached.get("expires_at") or 0) > time.time():
            _mem_token = cached
            return cached["access_token"]

        refresh_token = cached.get("refresh_token")

    if not refresh_token:
        raise OutlookQuickRunError(
            "Outlook delegated access is not connected. "
            "Run `python3 app/services/outlook/outlook_quick_run.py login` once first."
        )

    with _refresh_lock:
        with _token_lock:
            tok = _mem_token
            if tok and float(tok.get("expires_at") or 0) > time.time():
                return tok["access_token"]
        refreshed = _refresh_access_token(refresh_token)
        _save_token_cache(refreshed)
        return refreshed["access_token"]


def _refresh_access_token(refresh_token: str) -> dict:
    resp = _http.post(
        _TOKEN_ENDPOINT,
        data={
            "grant_type": "refresh_token",
            "client_id": CLIENT_ID,
            "refresh_token": refresh_token,
            "scope": _TOKEN_SCOPES_STR,
        },
        timeout=REQUEST_TIMEOUT,
    )
    try:
        result = resp.json()
    except ValueError as exc:
        raise OutlookQuickRunError(resp.text) from exc

    if "access_token" in result:
        return result

    error = result.get("error_description") or result.get("error") or str(result)
    raise OutlookQuickRunError(f"Could not refresh Outlook token: {error}")


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {_get_access_token()}",
        "Content-Type": "application/json",
        "Prefer": _PREFER_HEADER,
    }


def _graph_request(method: str, url: str, **kwargs) -> requests.Response:
    resp = _http.request(
        method,
        url,
        headers=_headers(),
        timeout=REQUEST_TIMEOUT,
        **kwargs,
    )
    if resp.ok:
        return resp

    detail = _extract_graph_error(resp)
    logger.error(
        "Graph %s %s failed (%s): %s",
        method.upper(),
        url,
        resp.status_code,
        detail,
    )
    raise OutlookQuickRunError(f"Microsoft Graph error {resp.status_code}: {detail}")


def _extract_graph_error(resp: requests.Response) -> str:
    try:
        payload = resp.json()
    except ValueError:
        return resp.text

    error = payload.get("error")
    if isinstance(error, dict):
        return error.get("message") or error.get("code") or str(error)
    return str(payload)


def _signed_in_user() -> dict:
    url = f"{GRAPH_BASE}/me?$select=id,userPrincipalName,mail,displayName"
    user = _graph_request("get", url).json()
    _validate_configured_user(user)
    return user


def _validate_configured_user(user: dict) -> None:
    if "@" not in GRAPH_USER_ID:
        return

    signed_in_ids = {
        (user.get("mail") or "").lower(),
        (user.get("userPrincipalName") or "").lower(),
    }
    if GRAPH_USER_ID.lower() not in signed_in_ids:
        raise OutlookQuickRunError(
            f"Signed-in Outlook user does not match GRAPH_USER_ID={GRAPH_USER_ID}."
        )


def check_connection() -> dict:
    me = _signed_in_user()
    return {
        "token": "ok",
        "configured_user": GRAPH_USER_ID,
        "user": me.get("userPrincipalName") or me.get("mail") or me.get("id"),
        "display_name": me.get("displayName"),
    }


def _as_list(value: str | list[str], field_name: str) -> list[str]:
    if isinstance(value, str):
        values = [value]
    else:
        values = value

    cleaned = [item.strip() for item in values if item and item.strip()]
    if not cleaned:
        raise ValueError(f"{field_name} is required.")
    return cleaned


def create_event(
    title: str,
    start_time: str,
    end_time: str | None = None,
    attendee_name: str | list[str] = "",
    attendee_email: str | list[str] = "",
    additional_guests: list[str] | None = None,
    duration_minutes: int = 30,
    attendee_type: str = "required",
) -> dict:
    if not end_time:
        try:
            dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
        except ValueError:
            dt = datetime.fromisoformat(start_time)
        end_time = (dt + timedelta(minutes=duration_minutes)).strftime(
            "%Y-%m-%dT%H:%M:%S"
        )

    attendee_names = _as_list(attendee_name, "attendee_name")
    attendee_emails = _as_list(attendee_email, "attendee_email")
    if len(attendee_names) != len(attendee_emails):
        raise ValueError("attendee_name and attendee_email must have the same length.")

    attendees = [
        {
            "emailAddress": {"address": email, "name": name},
            "type": attendee_type,
        }
        for name, email in zip(attendee_names, attendee_emails)
    ]
    for guest_email in additional_guests or []:
        attendees.append({"emailAddress": {"address": guest_email}, "type": "required"})

    body = {
        "subject": title,
        "start": {"dateTime": start_time, "timeZone": OUTLOOK_TIMEZONE},
        "end": {"dateTime": end_time, "timeZone": OUTLOOK_TIMEZONE},
        "attendees": attendees,
        "isOnlineMeeting": True,
        "onlineMeetingProvider": "teamsForBusiness",
        "body": {
            "contentType": "HTML",
            "content": (""),
        },
    }

    event = _graph_request("post", f"{GRAPH_BASE}/me/events", json=body).json()
    logger.info(
        "Created Outlook event id=%s subject=%s webLink=%s",
        event.get("id"),
        title,
        event.get("webLink"),
    )
    return event


def _target_booking_date(days_ahead: int = BOOK_DAYS_AHEAD) -> date:
    return datetime.now(_TZ).date() + timedelta(days=days_ahead)


def _booking_window_for_clock(
    target_date: date,
    start_clock: str,
    end_clock: str,
) -> tuple[str, str]:
    start_time = datetime.strptime(start_clock, "%H:%M:%S").time()
    end_time = datetime.strptime(end_clock, "%H:%M:%S").time()
    return (
        datetime.combine(target_date, start_time).isoformat(timespec="seconds"),
        datetime.combine(target_date, end_time).isoformat(timespec="seconds"),
    )


def _room_targets() -> list[tuple[str, str]]:
    attendee_names = _as_list(EVENT_ATTENDEE_NAMES, "EVENT_ATTENDEE_NAMES")
    attendee_emails = _as_list(EVENT_ATTENDEE_EMAILS, "EVENT_ATTENDEE_EMAILS")
    if len(attendee_names) != len(attendee_emails):
        raise ValueError("EVENT_ATTENDEE_NAMES and EVENT_ATTENDEE_EMAILS must match.")
    return list(zip(attendee_names, attendee_emails))


def _booking_requests(target_date: date) -> list[dict]:
    booking_requests = []
    room_targets = _room_targets()
    for start_clock, end_clock in EVENT_WINDOWS:
        window_start, window_end = _booking_window_for_clock(
            target_date,
            start_clock,
            end_clock,
        )
        for room_name, room_email in room_targets:
            booking_requests.append(
                {
                    "start_time": window_start,
                    "end_time": window_end,
                    "room_name": room_name,
                    "room_email": room_email,
                }
            )
    return booking_requests


def _prewarm_connection() -> None:
    """Ensure the TCP/TLS connection to Graph API is live before the trigger fires."""
    try:
        _signed_in_user()
        logger.info("Graph API connection pre-warmed, auth confirmed")
    except Exception as exc:
        logger.warning("Pre-warm failed (non-fatal, will retry at trigger): %s", exc)


def _prebuild_booking_payloads(booking_requests: list[dict]) -> list[dict]:
    """Capture auth token and pre-serialize JSON bodies so the trigger fires raw bytes."""
    access_token = _get_access_token()
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "Prefer": _PREFER_HEADER,
    }
    url = f"{GRAPH_BASE}/me/events"
    return [
        {
            "url": url,
            "headers": headers,
            "data": json.dumps({
                "subject": EVENT_TITLE,
                "start": {"dateTime": req["start_time"], "timeZone": OUTLOOK_TIMEZONE},
                "end": {"dateTime": req["end_time"], "timeZone": OUTLOOK_TIMEZONE},
                "attendees": [
                    {
                        "emailAddress": {"address": req["room_email"], "name": req["room_name"]},
                        "type": "resource",
                    }
                ],
                "isOnlineMeeting": True,
                "onlineMeetingProvider": "teamsForBusiness",
                "body": {"contentType": "HTML", "content": ""},
            }).encode(),
            "meta": req,
        }
        for req in booking_requests
    ]


def _fire_prebuilt_event(payload: dict) -> tuple[dict, float]:
    """POST a pre-serialized event payload — zero per-request overhead at trigger time."""
    started_at = time.perf_counter()
    resp = _http.request(
        "post",
        payload["url"],
        headers=payload["headers"],
        data=payload["data"],
        timeout=REQUEST_TIMEOUT,
    )
    duration = time.perf_counter() - started_at
    if not resp.ok:
        detail = _extract_graph_error(resp)
        logger.error("Graph POST %s failed (%s): %s", payload["url"], resp.status_code, detail)
        raise OutlookQuickRunError(f"Microsoft Graph error {resp.status_code}: {detail}")
    return resp.json(), duration


def _parse_trigger_clocks(value: str) -> list[str]:
    clocks = [item.strip() for item in value.replace(";", ",").split(",") if item.strip()]
    if not clocks:
        raise ValueError("BOOKING_TRIGGER_CLOCKS must include at least one HH:MM:SS value.")

    for clock in clocks:
        try:
            datetime.strptime(clock, "%H:%M:%S")
        except ValueError as exc:
            raise ValueError(
                "BOOKING_TRIGGER_CLOCKS must use comma- or semicolon-separated "
                "HH:MM:SS values."
            ) from exc
    return clocks


def _next_trigger_datetimes(clocks: list[str]) -> list[datetime]:
    now = datetime.now(_TZ)
    logger.info("Current time=%s", now.isoformat(timespec="seconds"))
    trigger_datetimes = []
    previous: datetime | None = None

    for clock in clocks:
        trigger_at = datetime.combine(
            now.date(),
            datetime.strptime(clock, "%H:%M:%S").time(),
            tzinfo=_TZ,
        )
        if previous is None:
            while trigger_at <= now:
                trigger_at += timedelta(days=1)
        else:
            while trigger_at <= previous:
                trigger_at += timedelta(days=1)
        trigger_datetimes.append(trigger_at)
        previous = trigger_at

    return trigger_datetimes


def _wait_until(trigger_at: datetime) -> None:
    wait_seconds = (trigger_at - datetime.now(_TZ)).total_seconds()
    if wait_seconds <= 0:
        logger.info(
            "Booking trigger time has passed; firing immediately. trigger=%s",
            trigger_at.isoformat(timespec="seconds"),
        )
        return

    logger.info(
        "Waiting until booking trigger time=%s duration=%.3fs",
        trigger_at.isoformat(timespec="seconds"),
        wait_seconds,
    )
    while True:
        remaining = (trigger_at - datetime.now(_TZ)).total_seconds()
        if remaining <= 0:
            break
        # Far from trigger: sleep in 30s chunks to avoid CPU waste.
        # Within 1s: tight 20ms polling to fire as close to the trigger as possible.
        time.sleep(min(remaining - 0.5, 30.0) if remaining > 1.0 else min(remaining, 0.02))


def _create_booking_event(request: dict) -> tuple[dict, float]:
    started_at = time.perf_counter()
    event = create_event(
        title=EVENT_TITLE,
        start_time=request["start_time"],
        end_time=request["end_time"],
        attendee_name=request["room_name"],
        attendee_email=request["room_email"],
        attendee_type="resource",
    )
    return event, time.perf_counter() - started_at


def _run_booking_batch(
    booking_requests: list[dict],
    attempt: int,
    trigger_at: datetime | None,
    prebuilt_payloads: list[dict] | None = None,
) -> tuple[list[dict], list[str], list[dict]]:
    created_events: list[dict] = []
    errors: list[str] = []
    succeeded_requests: list[dict] = []
    started_at = time.perf_counter()
    workers = len(booking_requests)
    trigger_label = (
        trigger_at.isoformat(timespec="seconds") if trigger_at else "immediate"
    )

    logger.info(
        "Starting room booking attempt=%d trigger=%s requests=%d workers=%d prebuilt=%s",
        attempt,
        trigger_label,
        len(booking_requests),
        workers,
        prebuilt_payloads is not None,
    )

    with ThreadPoolExecutor(max_workers=workers) as executor:
        if prebuilt_payloads:
            future_to_request = {
                executor.submit(_fire_prebuilt_event, payload): payload["meta"]
                for payload in prebuilt_payloads
            }
        else:
            future_to_request = {
                executor.submit(_create_booking_event, request): request
                for request in booking_requests
            }
        for future in as_completed(future_to_request):
            request = future_to_request[future]
            try:
                event, duration_seconds = future.result()
            except Exception as exc:
                detail = (
                    f'attempt={attempt} {request["room_email"]} '
                    f'{request["start_time"]}-{request["end_time"]}: {exc}'
                )
                logger.exception("Room booking request failed: %s", detail)
                errors.append(detail)
                continue

            created_events.append(event)
            succeeded_requests.append(request)
            logger.info(
                "Room booking request created attempt=%d room=%s start=%s "
                "end=%s event=%s duration=%.3fs",
                attempt,
                request["room_email"],
                request["start_time"],
                request["end_time"],
                event.get("id"),
                duration_seconds,
            )

    logger.info(
        "Room booking attempt completed attempt=%d trigger=%s created=%d "
        "failed=%d duration=%.3fs",
        attempt,
        trigger_label,
        len(created_events),
        len(errors),
        time.perf_counter() - started_at,
    )
    return created_events, errors, succeeded_requests


def main() -> list[dict]:
    # Validate and refresh delegated auth once before the booking burst;
    # also warms _mem_token so concurrent workers skip disk reads entirely.
    _signed_in_user()

    created_events: list[dict] = []
    errors: list[str] = []

    if WAIT_UNTIL_BOOKING_TIME:
        trigger_clocks = _parse_trigger_clocks(BOOKING_TRIGGER_CLOCKS)
        trigger_datetimes = _next_trigger_datetimes(trigger_clocks)
        # _next_trigger_datetimes already rolled the final trigger (00:00:00) onto the
        # new day, so its date is the booking reference — no extra +1 day.
        target_date = trigger_datetimes[-1].date() + timedelta(days=BOOK_DAYS_AHEAD)
    else:
        trigger_datetimes = [None]
        target_date = _target_booking_date()

    all_booking_requests = _booking_requests(target_date)
    if not all_booking_requests:
        return []

    logger.info(
        "Booking target date=%s days_ahead=%d reference=%s",
        target_date.isoformat(),
        BOOK_DAYS_AHEAD,
        (
            trigger_datetimes[-1].isoformat(timespec="seconds")
            if trigger_datetimes[-1]
            else "current_date"
        ),
    )

    pending_requests = list(all_booking_requests)
    for attempt, trigger_at in enumerate(trigger_datetimes, start=1):
        if not pending_requests:
            break

        prebuilt_payloads: list[dict] | None = None
        if trigger_at:
            # Phase 1: wait until 30s before trigger. Only pre-warm the connection when we
            # actually waited — on a back-to-back retry (e.g. 00:00:00 right after 23:59:59)
            # the connection is already hot, so an extra GET /me would just delay the burst.
            prewarm_at = trigger_at - timedelta(seconds=_PREWARM_LEAD_SECONDS)
            if (prewarm_at - datetime.now(_TZ)).total_seconds() > 0:
                _wait_until(prewarm_at)
                _prewarm_connection()
            prebuilt_payloads = _prebuild_booking_payloads(pending_requests)
            logger.info("Pre-built %d booking payloads", len(prebuilt_payloads))
            # Phase 2: tight wait for the exact trigger time.
            _wait_until(trigger_at)

        attempt_events, attempt_errors, attempt_succeeded = _run_booking_batch(
            pending_requests,
            attempt,
            trigger_at,
            prebuilt_payloads,
        )
        created_events.extend(attempt_events)

        # Only retry slots that failed — dedup on the request identity we sent, not the
        # Graph response (Graph echoes dateTime with fractional seconds that never match).
        succeeded_keys = {
            (r["start_time"], r["room_email"]) for r in attempt_succeeded
        }
        pending_requests = [
            r for r in pending_requests
            if (r["start_time"], r["room_email"]) not in succeeded_keys
        ]
        errors = attempt_errors

    if errors:
        logger.error(
            "%d room booking request(s) failed; %d created. Errors: %s",
            len(errors),
            len(created_events),
            "; ".join(errors),
        )
        if not created_events:
            raise OutlookQuickRunError(
                f"{len(errors)} room booking request(s) failed; "
                f"0 created. Errors: {'; '.join(errors)}"
            )
    return created_events


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        events = main()
        for event in events:
            logger.info("Event created: %s", event.get("webLink") or event.get("id"))
    except OutlookQuickRunError as exc:
        logger.error("Outlook quick run failed: %s", exc)
        sys.exit(1)
