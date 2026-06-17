"""Tour-visit tools — visitor intake, owner follow-up, and auto-generated rule tools.

Rule tools (get_tour_process_info, get_booking_restrictions, …) are generated
automatically from the rules/ directory. Adding a new rule file creates a new
tool with no code change required.
"""
from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from langchain_core.tools import StructuredTool, tool

from app.infrastructure.outlook_client import OutlookSender
from app.services.config import Rule
from app.services.tour_visit.config import TourServiceConfig
from app.services.tour_visit.domain import TourStatus
from app.services.tour_visit.repository import TourRepository


logger = logging.getLogger("tour_bot.tour_visit")

_VNG_DOMAIN = "vng.com.vn"
_TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def _fmt_slot(start: datetime, end: datetime) -> str:
    """Render a (start, end) window as 'YYYY-MM-DD HH:MM–HH:MM'."""
    return f"{start.strftime('%Y-%m-%d %H:%M')}–{end.strftime('%H:%M')}"

# Requester-facing status copy (curated — never exposes owner/team internals).
_STATUS_OVERVIEW = {
    "new":       "Received — waiting for the campus tour coordinator to review.",
    "in_review": "Under review by the campus tour coordinator.",
    "approved":  "Approved — the coordinator is arranging the schedule.",
    "scheduled": "Scheduled — your visit date is confirmed and our teams are preparing.",
    "completed": "Completed — thank you for hosting!",
    "rejected":  "Not approved. Please contact the campus tour coordinator for details.",
}
# Ordered stages shown as the progress track (rejected is terminal/off-track).
_STAGE_TRACK = [
    ("new", "Received"), ("in_review", "Reviewed"),
    ("approved", "Approved"), ("scheduled", "Scheduled"), ("completed", "Completed"),
]

# Email subject + opening line sent to the requester on each status transition.
# Keys match TourStatus values. Statuses that don't warrant a requester email are omitted.
_STATUS_EMAIL: dict[str, tuple[str, str]] = {
    "in_review": (
        "[Visit Update] {id} — {org} | Under Review",
        "Your campus visit request is now under review by the coordinator.",
    ),
    "approved": (
        "[Visit Update] {id} — {org} | Approved",
        "Great news — your campus visit request has been approved!\n"
        "The coordinator is now arranging the final schedule with the support teams.",
    ),
    "scheduled": (
        "[Visit Update] {id} — {org} | Confirmed & Scheduled",
        "Your visit has been confirmed and scheduled.\n"
        "Date: {visit_date}   Time: {visit_time}\n"
        "Our teams are now preparing for the visit.",
    ),
    "completed": (
        "[Visit Update] {id} — {org} | Completed",
        "Your campus visit has been marked as completed. Thank you for organising it!",
    ),
    "rejected": (
        "[Visit Update] {id} — {org} | Not Approved",
        "We're sorry — your campus visit request could not be approved at this time.\n"
        "Please contact the campus tour coordinator for more details.",
    ),
}


def _normalize_contact_email(value: str) -> str:
    """Accept 'hieunx', 'hieunx@vng.com.vn', or any full email.
    If no '@' present, appends '@vng.com.vn'.
    """
    value = value.strip()
    return value if "@" in value else f"{value}@{_VNG_DOMAIN}"


def _make_rule_tool(rule: Rule) -> StructuredTool:
    def fn() -> str:
        return rule.content
    return StructuredTool.from_function(
        func=fn,
        name=rule.tool_name,
        description=rule.description,
    )


def build_tools(
    repo: TourRepository,
    mailer: OutlookSender,
    cfg: TourServiceConfig,
    trello_client=None,
    google_client=None,
) -> dict[str, list]:
    """Return role-keyed tools: {"requester": [...], "owner": [...]}.

    The requester set includes all rule tools auto-generated from cfg.rules.
    (The owner role inherits the requester tools via `extends` in config.yaml.)

    trello_client / google_client are optional; when a team's channel client is
    unavailable, notify_team falls back to email so the flow always completes.
    """

    # ── Rule tools (auto-generated from rules/ directory) ─────────────
    rule_tools = [_make_rule_tool(r) for r in cfg.rules]

    def _availability_report(start_dt, end_dt) -> tuple[bool, str]:
        """(available?, message) for a parsed window. Assumes google_client is set.
        On conflict, the message lists up to 3 nearest free slots."""
        conflicts = google_client.find_conflicts(start_dt, end_dt)
        if not conflicts:
            return True, f"✅ {_fmt_slot(start_dt, end_dt)} is available."
        lines = [f"⚠️ {_fmt_slot(start_dt, end_dt)} is not available — it overlaps an existing visit."]
        try:
            slots = google_client.suggest_free_slots(start_dt, count=3)
        except Exception:
            slots = []
        if slots:
            lines.append("Nearest available slots:")
            lines += [f"  {i + 1}. {_fmt_slot(s, e)}" for i, (s, e) in enumerate(slots)]
            lines.append("Please pick one of these, or propose another date/time.")
        else:
            lines.append("I couldn't find a free slot in the next two weeks — please propose another date/time.")
        return False, "\n".join(lines)

    # ── Requester tools ───────────────────────────────────────────────

    @tool
    def submit_tour_request(
        requester_name: str = "",
        organization: str = "",
        visit_date: str = "",
        visit_time: str = "",
        group_size: int = 0,
        purpose: str = "",
        contact_email: str = "",
        visit_type: str = "",
        guest_profile: str = "",
        partner_gift: str = "",
        meeting_topic: str = "",
    ) -> str:
        """Submit a corporate visit request on behalf of the internal requester.
        Only call this once all required fields are collected and confirmed AND the
        chosen date/time has been checked with check_calendar_availability.

        Args:
            requester_name: Name of the internal VNG employee organizing the visit (NOT the guest).
            organization: Guest organization / company being hosted.
            visit_date: Preferred date of the visit.
            visit_time: Preferred time window of the visit (e.g. "14:00-16:00" or "2pm").
            group_size: Number of external visiting guests.
            purpose: Purpose / reason for the visit.
            contact_email: Requester's email to receive confirmation.
            visit_type: "tour only" or "tour + meeting".
            guest_profile: Background of the visiting guests (who they are, seniority level).
            partner_gift: Selected partner gift, if any.
            meeting_topic: Topic to align on — only when the visit includes a meeting.
        """
        provided = {
            "requester_name": requester_name,
            "organization": organization,
            "visit_date": visit_date,
            "visit_time": visit_time,
            "group_size": group_size,
            "purpose": purpose,
            "contact_email": contact_email,
            "visit_type": visit_type,
            "guest_profile": guest_profile,
            "partner_gift": partner_gift,
            "meeting_topic": meeting_topic,
        }
        contact_email = _normalize_contact_email(contact_email)
        provided["contact_email"] = contact_email

        missing = [
            f.label for f in cfg.request_fields
            if f.required and not provided.get(f.key)
        ]
        if missing:
            return f"Cannot submit — still missing required fields: {', '.join(missing)}."

        # Re-check the slot against the calendar — gates the submit even if the LLM
        # skipped check_calendar_availability, and catches a slot booked in the meantime.
        if google_client:
            window = google_client._parse_visit_window(visit_date, visit_time)
            if window:
                try:
                    available, report = _availability_report(*window)
                except Exception as exc:
                    logger.warning("Availability re-check failed at submit: %s", exc)
                    available, report = True, ""  # don't block on a calendar outage
                if not available:
                    return f"I can't book that slot — {report}"

        request = repo.create(
            requester_name=requester_name,
            organization=organization,
            visit_date=visit_date,
            visit_time=visit_time,
            group_size=group_size,
            purpose=purpose,
            contact_email=contact_email,
            visit_type=visit_type,
            guest_profile=guest_profile,
            partner_gift=partner_gift,
            meeting_topic=meeting_topic,
        )
        body = (
            "A new corporate visit request has been submitted.\n\n"
            f"Request ID   : {request.id}\n"
            f"── Requester ──────────────────────\n"
            f"Name         : {requester_name}\n"
            f"Contact      : {contact_email}\n"
            f"── Guests ─────────────────────────\n"
            f"Organization : {organization}\n"
            f"Guest profile: {guest_profile}\n"
            f"Group size   : {group_size}\n"
            f"── Visit details ──────────────────\n"
            f"Visit type   : {visit_type}\n"
            f"Visit date   : {visit_date}\n"
            f"Visit time   : {visit_time}\n"
            f"Purpose      : {purpose}\n"
            f"Meeting topic: {meeting_topic}\n"
            f"Partner gift : {partner_gift}\n\n"
            f"Follow up via the bot using request ID {request.id}."
        )
        mailer.send(
            to=cfg.owner_email,
            subject=f"[Visit Request] {request.id} — {organization}",
            body=body,
        )
        confirmation_body = (
            f"Your corporate visit request has been received and is pending review.\n\n"
            f"Request ID   : {request.id}\n"
            f"── Visit details ──────────────────\n"
            f"Organization : {organization}\n"
            f"Guest profile: {guest_profile}\n"
            f"Group size   : {group_size}\n"
            f"Visit type   : {visit_type}\n"
            f"Visit date   : {visit_date}\n"
            f"Visit time   : {visit_time}\n"
            f"Purpose      : {purpose}\n"
            f"Meeting topic: {meeting_topic}\n"
            f"Partner gift : {partner_gift}\n\n"
            f"{cfg.owner_name} will review and confirm the schedule with you shortly.\n"
            f"Keep this request ID for any follow-up: {request.id}"
        )
        mailer.send(
            to=contact_email,
            subject=f"[Visit Request Confirmation] {request.id} — {organization}",
            body=confirmation_body,
        )
        return (
            f"Your corporate visit request has been submitted (ID: {request.id}).\n"
            f"I've notified {cfg.owner_name}, who will review and confirm "
            f"the schedule with you at {contact_email}."
        )

    @tool
    def check_my_request(request_id: str) -> str:
        """Check the status of a corporate visit request you submitted, by its request ID
        (the TOUR-XXXX id from your confirmation). Returns a high-level status overview —
        not the internal team task breakdown."""
        r = repo.get(request_id)
        if not r:
            return ("No request found with that ID. Please check the request ID from your "
                    "confirmation email, or submit a new request.")

        status_line = _STATUS_OVERVIEW.get(r.status, r.status)

        if r.status == TourStatus.REJECTED.value:
            track = ""   # off-track; the generic status line already covers it
        else:
            track = "Progress: " + " -> ".join(
                (f"[{label}]" if key == r.status else label) for key, label in _STAGE_TRACK
            ) + "\n"

        updated = (r.updated_at or r.created_at or "").split("T")[0]
        return (
            f"Request {r.id} — {r.organization}, visit on {r.visit_date}\n"
            f"Status: {status_line}\n"
            f"{track}"
            f"Last updated: {updated}"
        )

    @tool
    def check_calendar_availability(visit_date: str, visit_time: str) -> str:
        """THE calendar tool. Queries the live campus Google Calendar to check whether a
        specific visit date + time is free, and returns up to 3 nearest free slots if it
        is taken. This is the ONLY way to know real availability — never answer an
        availability question from the booking-rules text. Call it whenever the user asks
        if a date/time is free, and ALWAYS before confirming or submitting a request.

        Args:
            visit_date: Requested date, e.g. "2026-07-10".
            visit_time: Requested time window, e.g. "14:00-16:00" or "2pm".
        """
        if not google_client:
            return ("Availability can't be verified right now (calendar not configured) — "
                    "you may proceed to confirm and submit.")
        window = google_client._parse_visit_window(visit_date, visit_time)
        if not window:
            return ("I couldn't read that date/time. Please give a clear date and a start time — "
                    "e.g. date '2026-07-10' and time '14:00' or '2pm-4pm'.")
        start_dt, end_dt = window
        if start_dt < datetime.now(_TZ):
            return "That date/time is in the past. Please choose a future date and time."
        try:
            available, report = _availability_report(start_dt, end_dt)
        except Exception as exc:
            logger.warning("Availability check failed: %s", exc)
            return ("I couldn't reach the calendar to check availability right now — you may proceed; "
                    "the coordinator will confirm the final schedule.")
        return report if not available else f"{report} You can confirm and submit this slot."

    # ── Owner tools ───────────────────────────────────────────────────

    @tool
    def list_tour_requests(status: str = "") -> str:
        """(Owner) List tour requests, optionally filtered by status
        (new, in_review, approved, scheduled, completed, rejected)."""
        items = repo.list(status or None)
        if not items:
            return "No tour requests found."
        return "\n".join(
            f"{r.id} | {r.status} | {r.organization} | {r.visit_date} | {r.group_size} ppl"
            for r in items
        )

    @tool
    def get_tour_request(request_id: str) -> str:
        """(Owner) Get the full details and status history of one tour request."""
        r = repo.get(request_id)
        if not r:
            return f"No request found with ID {request_id}."
        history = "\n".join(
            f"  - {h['at']}: {h['status']} {h.get('note', '')}".rstrip() for h in r.history
        )
        return (
            f"ID: {r.id}  (status: {r.status})\n"
            f"── Requester ────────────────────────\n"
            f"Name   : {r.requester_name}\n"
            f"Contact: {r.contact_email}\n"
            f"── Guests ───────────────────────────\n"
            f"Org    : {r.organization}\n"
            f"Profile: {r.guest_profile}\n"
            f"Size   : {r.group_size}\n"
            f"── Visit ────────────────────────────\n"
            f"Type   : {r.visit_type}   Date: {r.visit_date}\n"
            f"Purpose: {r.purpose}\n"
            f"Meeting: {r.meeting_topic}\n"
            f"Gift   : {r.partner_gift}\n"
            f"── History ──────────────────────────\n"
            f"{history}"
        )

    @tool
    def update_tour_status(request_id: str, status: str, note: str = "", confirmed_time: str = "") -> str:
        """(Owner) Update the status of a tour request and automatically notify the requester by email.
        Valid statuses: new, in_review, approved, scheduled, completed, rejected.
        Setting status to 'scheduled' also creates a calendar event for the visit.
        confirmed_time: final confirmed visit time (e.g. "14:00-16:00") — use when the owner
          locks a different time from the original request. Updates the stored visit_time and
          uses it for the calendar event. If omitted, the originally requested time is used.
        The note is included in the requester email (e.g. schedule details, rejection reason)."""
        if not TourStatus.is_valid(status):
            return f"Invalid status '{status}'. Valid: {', '.join(TourStatus.values())}."

        # Update visit_time first so the calendar event and email use the confirmed time.
        r = repo.update(request_id, status=status, note=note,
                        visit_time=confirmed_time if confirmed_time else None)
        if not r:
            return f"No request found with ID {request_id}."

        calendar_note = ""
        # Create the campus calendar event once the visit is scheduled — idempotent.
        if status == TourStatus.SCHEDULED.value and google_client and not r.external_refs.get("calendar"):
            try:
                link = google_client.create_visit_event(
                    request_id=r.id,
                    organization=r.organization,
                    visit_date=r.visit_date,
                    group_size=r.group_size,
                    purpose=r.purpose,
                    visit_type=r.visit_type,
                    visit_time=r.visit_time,
                )
                repo.update(request_id, external_refs={"calendar": link})
                calendar_note = f"\nCalendar event: {link}"
            except Exception as exc:
                logger.warning("Calendar event creation failed for %s: %s", request_id, exc)
                calendar_note = f"\n(Calendar event could not be created: {exc})"

        # Auto-notify the requester for every status that warrants an email.
        if status in _STATUS_EMAIL and r.contact_email:
            subject_tpl, opening = _STATUS_EMAIL[status]
            subject = subject_tpl.format(id=r.id, org=r.organization)
            opening = opening.format(visit_date=r.visit_date, visit_time=r.visit_time,
                                     id=r.id, org=r.organization)
            body_parts = [
                f"Dear {r.requester_name},\n",
                opening,
                f"\nRequest ID  : {r.id}",
                f"Organization: {r.organization}",
                f"Visit date  : {r.visit_date}   {r.visit_time}",
            ]
            if note:
                body_parts.append(f"\nNote from coordinator:\n{note}")
            body_parts.append(f"\n{cfg.owner_name}")
            mailer.send(to=r.contact_email, subject=subject, body="\n".join(body_parts))
            logger.info("Status email (%s) sent to %s for %s", status, r.contact_email, r.id)

        return f"Request {request_id} updated to '{status}'.{calendar_note}"

    @tool
    def notify_team(request_id: str, team_key: str, message: str) -> str:
        """(Owner) Loop in a supporting team to prepare their part of a visit.
        team_key is one of the configured team keys (e.g. bie, eb, pr, it, af).
        Routes by the team's channel: BIE → Trello card, others → shared Google Sheet,
        with email as the fallback if a channel's client is not configured."""
        r = repo.get(request_id)
        if not r:
            return f"No request found with ID {request_id}."
        team = cfg.team(team_key)
        if not team:
            valid = ", ".join(t.key for t in cfg.teams)
            return f"Unknown team '{team_key}'. Valid teams: {valid}."
        resp = ", ".join(team.responsibilities) if team.responsibilities else "—"
        body = (
            f"Visit request {r.id} needs your team's preparation.\n\n"
            f"Requested by : {r.requester_name} ({r.contact_email})\n"
            f"Guest org    : {r.organization}   Profile: {r.guest_profile}\n"
            f"Visit type   : {r.visit_type}   Date: {r.visit_date}   Size: {r.group_size}\n"
            f"Purpose      : {r.purpose}\n"
            f"Your responsibilities: {resp}\n\n"
            f"Note from coordinator:\n{message}"
        )
        card_name = f"[Visit {r.id}] {r.organization} — {r.visit_date}"
        refs: dict = {}
        try:
            if team.channel == "trello" and trello_client and team.trello_list_id:
                card = trello_client.create_card(team.trello_list_id, card_name, body)
                refs = {"trello": {team.key: card.get("id")}}
                outcome = f"Created a Trello card for {team.name}"
            elif team.channel == "sheets" and google_client:
                google_client.add_task_row(
                    team_name=team.name,
                    request_id=r.id,
                    organization=r.organization,
                    visit_date=r.visit_date,
                    responsibilities=list(team.responsibilities),
                    note=message,
                )
                outcome = f"Added a task row for {team.name} to the shared sheet"
            else:
                raise RuntimeError(f"no client for channel '{team.channel}'")
        except Exception as exc:
            logger.warning("notify_team %s via %s failed (%s) — emailing", team_key, team.channel, exc)
            mailer.send(to=team.email, subject=f"[Visit {r.id}] Action needed — {team.name}", body=body)
            outcome = f"Notified {team.name} by email (fallback)"

        # Always record the notification in the request history (audit trail).
        repo.update(request_id, note=f"Notified '{team_key}' via {team.channel}: {message}",
                    external_refs=refs or None)
        return f"{outcome} for {request_id}."

    @tool
    def check_visit_progress(request_id: str) -> str:
        """(Owner) Consolidated prep status for one request across Trello + the shared sheet.
        Shows each notified team's current status and flags any not yet started."""
        r = repo.get(request_id)
        if not r:
            return f"No request found with ID {request_id}."
        lines = [f"{r.id} — {r.organization} (request status: {r.status})"]

        trello_refs = r.external_refs.get("trello", {})
        for team_key, card_id in trello_refs.items():
            team = cfg.team(team_key)
            label = team.name if team else team_key
            status = "unknown"
            if trello_client:
                try:
                    card = trello_client.get_card(card_id)
                    status = trello_client.list_name(card.get("idList", ""))
                except Exception as exc:
                    logger.warning("Trello read failed for %s: %s", card_id, exc)
            lines.append(f"  - {label} (Trello): {status}")

        if google_client:
            try:
                for row in google_client.get_task_rows(request_id):
                    flag = "  ← not started" if row["status"].strip().lower() == "not started" else ""
                    lines.append(f"  - {row['team']} (Sheet): {row['status']}{flag}")
            except Exception as exc:
                lines.append(f"  - (could not read the shared sheet: {exc})")

        if len(lines) == 1:
            lines.append("  (no team tasks recorded yet — use notify_team to dispatch prep work.)")
        return "\n".join(lines)

    return {
        "requester": rule_tools + [check_calendar_availability, submit_tour_request, check_my_request],
        "owner": [
            list_tour_requests, get_tour_request, update_tour_status,
            notify_team, check_visit_progress,
        ],
    }
