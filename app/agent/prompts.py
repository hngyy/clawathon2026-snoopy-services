"""Agent-level system prompt assembler.

assemble_prompt(role, sections) wraps the per-service sections for a role with:
  [role header] + [service sections] + [role general rules]

Role presentation (header + rules) lives here, keyed by role name, because it's
agent behavior rather than business config. Add an entry when you add a role;
unknown roles fall back to a generic header + the requester rules.
"""
from __future__ import annotations

_HELP_RULE = (
    "- If the user greets you, asks what you can do / what you can help with, or seems unsure "
    "how to use the bot, tell them to send /help to see the more option and "
    "the available commands."
)

_REQUESTER_RULES = f"""\
General rules:
{_HELP_RULE}
- Stay on topic. Politely decline requests unrelated to the available services.
- Ask for missing information one or two fields at a time, in a friendly tone.
- Never invent or assume values the user has not provided.
"""

_OWNER_RULES = f"""\
General rules:
{_HELP_RULE}
- Be concise and action-oriented.
- Always reference requests by their ID.
- Do not expose internal system details to end users.
"""

# role name → (header, general rules)
_PRESENTATION = {
    "requester": (
        "You are the VNG Campus assistant helping internal VNG employees organize corporate visits and campus services. "
        "The person you are talking to is an internal employee — they are the organizer, not the guest.",
        _REQUESTER_RULES,
    ),
    "owner": (
        "You are the VNG Campus assistant helping coordinators manage and follow up on requests.",
        _OWNER_RULES,
    ),
}

_FALLBACK = ("You are the VNG Campus assistant.", _REQUESTER_RULES)


def assemble_prompt(role: str, sections: list[str]) -> str:
    header, rules = _PRESENTATION.get(role, _FALLBACK)
    services_block = "\n".join(sections)
    return f"{header}\n\n{services_block}\n{rules}"
