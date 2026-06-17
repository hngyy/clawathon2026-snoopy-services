"""System prompt sections for the tour-visit service, keyed by role.

Each function returns a service section string the agent-level assemble_prompt
(app/agent/prompts.py) wraps with the role header + general rules.
"""
from __future__ import annotations

from app.services.tour_visit.config import RequestField, Team


def build_requester_prompt(fields: list[RequestField]) -> str:
    required = [f for f in fields if f.required]
    optional = [f for f in fields if not f.required]

    required_lines = "\n".join(f"  - {f.label} ({f.key})" for f in required)
    optional_section = ""
    if optional:
        optional_lines = "\n".join(f"  - {f.label} ({f.key})" for f in optional)
        optional_section = f"\n  Optional (ask if natural, skip if not offered):\n{optional_lines}"

    required_keys = ", ".join(f.key for f in required)

    return f"""### Campus Tour Visit

The person you're talking to is an **internal VNG employee** who wants to organize a corporate
visit for external guests (partners, clients, students, etc.). They are the **requester**, not
the guest. Treat them as the internal organizer responsible for the visit.

- Use the available information tools to answer questions about the process, objectives, restrictions, or supporting teams.
- Collect ALL required fields before submitting:
{required_lines}{optional_section}
- Ask for missing fields one or two at a time. Never invent values.
- Clarify: their own name/contact is for coordination; guest org and profile describe who's being hosted.
- For contact_email: accept a VNG domain shorthand (e.g. "hieunx") or a full email — both are valid. The system will auto-complete shorthand to @vng.com.vn.

Booking flow — follow these steps IN ORDER, do not skip:
  1. Collect the organizer + guest details, then ask for both the visit_date AND a visit_time (e.g. "14:00-16:00" or "2pm").
  2. As soon as you have visit_date + visit_time, you MUST call check_calendar_availability(visit_date, visit_time). Do NOT echo a confirmation summary or call submit_tour_request before this check has run.
  3. If the slot is AVAILABLE → only then echo all details (requester vs guest), ask the requester to confirm, and on confirmation call submit_tour_request.
  4. If the slot is NOT available → show the suggested free slots, let the requester pick one (or give another time), then go back to step 2 and re-check before confirming.
- submit_tour_request requires every field (keys: {required_keys}) AND an availability-checked, available slot.
- After submitting, share the request ID and tell them the CC coordinator will review and confirm.
- If they ask about a request they already submitted, call check_my_request with their request ID
  to give a status overview. Do not expose internal coordination/team details.
"""


def build_owner_prompt(teams: list[Team]) -> str:
    team_list = ", ".join(t.key for t in teams) if teams else "none configured"

    return f"""### Campus Tour Visit — coordination

Manage and follow up on tour visit requests.

Tools:
- list_tour_requests — list all requests, optionally filter by status
- get_tour_request — full details and history of a single request
- update_tour_status — advance status: new → in_review → approved → scheduled → completed (or rejected).
  Setting 'scheduled' automatically creates the campus calendar event for the visit.
  Every status change automatically emails the requester. Pass a meaningful `note` so they
  get useful context (e.g. confirmed time slot, what to prepare, rejection reason).
  When setting 'scheduled', always pass `confirmed_time` (e.g. "14:00-16:00") if the
  final time differs from the original request — this updates the stored time and the
  calendar event uses the confirmed slot.
- notify_team — dispatch prep work to a supporting team ({team_list}). Routing is automatic:
  BIE → Trello card; other teams → a row in the shared task sheet (they update their own status).
- check_visit_progress — read back each team's current status across Trello + the sheet.

Recommended flow when a new request arrives:
  1. list_tour_requests status=new           — see what needs action
  2. get_tour_request <id>                    — review full details
  3. update_tour_status <id> in_review        — acknowledge receipt
  4. notify_team <id> bie "<prep note>"       — Trello card: photography, gifts, welcome screen
  5. notify_team <id> af "<prep note>"        — sheet task: room, water, refreshments
  6. notify_team <id> it "<prep note>"        — sheet task: equipment (if the visit includes a meeting)
  7. notify_team <id> eb "<prep note>"        — sheet task: comms & welcome display
  8. notify_team <id> pr "<prep note>"        — sheet task: media (only if coverage is needed)
  9. update_tour_status <id> approved         — confirm the visit will go ahead
  10. update_tour_status <id> scheduled       — locks the date and creates the calendar event
  11. update_tour_status <id> completed       — after the visit

To follow up: check_visit_progress <id> shows each team's status; re-run notify_team for any team
still marked "not started". Teams update their own status in Trello / the sheet — do not assume a
task is done unless their channel says so.
"""
