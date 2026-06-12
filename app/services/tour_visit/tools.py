"""Tour-visit tools — visitor intake, owner follow-up, and auto-generated rule tools.

Rule tools (get_tour_process_info, get_booking_restrictions, …) are generated
automatically from the rules/ directory. Adding a new rule file creates a new
tool with no code change required.
"""
from __future__ import annotations

from langchain_core.tools import StructuredTool, tool

from app.infrastructure.mailer import EmailSender
from app.services.config import Rule
from app.services.tour_visit.config import TourServiceConfig
from app.services.tour_visit.domain import TourStatus
from app.services.tour_visit.repository import TourRepository


_VNG_DOMAIN = "vng.com.vn"


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


def build_tools(repo: TourRepository, mailer: EmailSender, cfg: TourServiceConfig) -> dict[str, list]:
    """Return role-keyed tools: {"requester": [...], "owner": [...]}.

    The requester set includes all rule tools auto-generated from cfg.rules.
    (The owner role inherits the requester tools via `extends` in config.yaml.)
    """

    # ── Rule tools (auto-generated from rules/ directory) ─────────────
    rule_tools = [_make_rule_tool(r) for r in cfg.rules]

    # ── Requester tools ───────────────────────────────────────────────

    @tool
    def submit_tour_request(
        requester_name: str = "",
        organization: str = "",
        visit_date: str = "",
        group_size: int = 0,
        purpose: str = "",
        contact_email: str = "",
        visit_type: str = "",
        guest_profile: str = "",
        partner_gift: str = "",
        meeting_topic: str = "",
    ) -> str:
        """Submit a corporate visit request on behalf of the internal requester.
        Only call this once all required fields have been collected and confirmed.

        Args:
            requester_name: Name of the internal VNG employee organizing the visit (NOT the guest).
            organization: Guest organization / company being hosted.
            visit_date: Preferred date (and time, if provided) of the visit.
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

        request = repo.create(
            requester_name=requester_name,
            organization=organization,
            visit_date=visit_date,
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
        return (
            f"Your corporate visit request has been submitted (ID: {request.id}).\n"
            f"I've notified {cfg.owner_name}, who will review and confirm "
            f"the schedule with you at {contact_email}."
        )

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
    def update_tour_status(request_id: str, status: str, note: str = "") -> str:
        """(Owner) Update the status of a tour request.
        Valid statuses: new, in_review, approved, scheduled, completed, rejected."""
        if not TourStatus.is_valid(status):
            return f"Invalid status '{status}'. Valid: {', '.join(TourStatus.values())}."
        r = repo.update(request_id, status=status, note=note)
        if not r:
            return f"No request found with ID {request_id}."
        return f"Request {request_id} updated to '{status}'."

    @tool
    def notify_team(request_id: str, team_key: str, message: str) -> str:
        """(Owner) Loop in a supporting team about a visit request to prepare their part.
        team_key is one of the configured team keys (e.g. bie, eb, pr, it, af).
        Use get_supporting_teams to see who handles what and via which channel."""
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
        # NOTE: Trello-channel teams are emailed for now; Trello card creation is wired later.
        mailer.send(to=team.email, subject=f"[Visit {r.id}] Action needed — {team.name}", body=body)
        repo.update(request_id, note=f"Notified team '{team_key}' (via {team.channel}): {message}")
        channel_note = " (Trello card pending integration; emailed for now)" if team.channel == "trello" else ""
        return f"Notified {team.name} about {request_id}{channel_note}."

    return {
        "requester": rule_tools + [submit_tour_request],
        "owner": [list_tour_requests, get_tour_request, update_tour_status, notify_team],
    }
