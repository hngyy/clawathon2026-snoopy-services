"""Supervisor graph: classify intent once, then dispatch to a per-service subgraph.

Role-agnostic: the caller passes a `role_chain` (the role plus the roles it extends,
most-specific first) and an `assemble` callable already bound to the role's header.
Each service subgraph binds only that service's tools + section for the chain, so
the per-turn tool list and prompt stay small no matter how many services exist.
The parent graph owns the checkpointer and the recall node.
See docs/supervisor-routing.md.
"""
from __future__ import annotations

from typing import Callable

from langchain_core.language_models import BaseChatModel
from langgraph.graph import END, START, StateGraph

from app.agent.classify import make_classifier
from app.agent.graph import build_tool_graph
from app.agent.state import State
from app.services.registry import Service


def build_supervisor_graph(
    llm: BaseChatModel,
    services: list[Service],
    *,
    role_chain: list[str],
    assemble: Callable[[list[str]], str],
    extra_tools: list | None = None,
    checkpointer=None,
    recall_node=None,
):
    extra_tools = extra_tools or []  # e.g. memory tools — bound in every subgraph + general

    def tools_of(s: Service) -> list:
        return [t for r in role_chain for t in s.tools.get(r, [])]

    def section_of(s: Service) -> str:
        return "\n".join(s.prompts[r] for r in role_chain if s.prompts.get(r))

    included = [s for s in services if tools_of(s) or section_of(s)]

    builder = StateGraph(State)
    builder.add_node("classify", make_classifier(llm, included))
    if recall_node is not None:
        builder.add_node("recall", recall_node)
        builder.add_edge(START, "recall")
        builder.add_edge("recall", "classify")
    else:
        builder.add_edge(START, "classify")

    route_map: dict[str, str] = {}
    for s in included:
        subgraph = build_tool_graph(llm, tools_of(s) + extra_tools, assemble([section_of(s)]))
        node = f"svc_{s.key}"
        builder.add_node(node, subgraph)
        builder.add_edge(node, END)
        route_map[s.key] = node

    # Fallback: plain chat with no service tools (memory tools still available).
    builder.add_node("general", build_tool_graph(llm, extra_tools, assemble([])))
    builder.add_edge("general", END)
    route_map["general"] = "general"

    builder.add_conditional_edges("classify", lambda s: route_map.get(s.get("route", "general"), "general"))
    return builder.compile(checkpointer=checkpointer)
