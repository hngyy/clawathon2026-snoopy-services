"""LangGraph graph state shared across graph.py, memory.py, and supervisor.py."""
from __future__ import annotations

from typing import Annotated, TypedDict

from langgraph.graph.message import add_messages


class State(TypedDict):
    messages: Annotated[list, add_messages]
    # Service key chosen by the supervisor's classify node. Unused in flat routing.
    # Persisted across turns by the checkpointer to enable sticky routing.
    route: str
