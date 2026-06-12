"""Intent classifier node for supervisor routing.

Routes the latest user message to a registered service key (or "general").
This is the FIRST routing layer — it narrows the candidate tool set to one
service. The chosen service's subgraph then does normal ReAct tool-selection.
See docs/supervisor-routing.md.
"""
from __future__ import annotations

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import SystemMessage
from pydantic import BaseModel, Field

from app.agent.state import State
from app.services.registry import Service


class _Route(BaseModel):
    service: str = Field(description="A service key, or 'general' for greetings/off-topic.")


def make_classifier(llm: BaseChatModel, services: list[Service]):
    catalog = "\n".join(f'- "{s.key}": {s.description}' for s in services)
    valid = {s.key for s in services}
    router_llm = llm.with_structured_output(_Route)

    def classify(state: State) -> dict:
        recent = state["messages"][-6:]
        transcript = "\n".join(
            f"{getattr(m, 'type', 'msg')}: {m.content}"
            for m in recent if getattr(m, "content", "")
        )
        current = state.get("route") or "none"
        prompt = (
            "Route the LATEST user message to the service that should handle it.\n\n"
            f"Services:\n{catalog}\n"
            '- "general": greetings, thanks, or anything off-topic.\n\n'
            f"Currently active: {current}. If the latest message CONTINUES that task "
            "(e.g. answering a follow-up question, or providing a field that was just "
            "asked for), KEEP the current service. Switch only on a clear topic change.\n\n"
            f"Conversation:\n{transcript}"
        )
        result = router_llm.invoke([SystemMessage(content=prompt)])
        return {"route": result.service if result.service in valid else "general"}

    return classify
