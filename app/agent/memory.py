"""AgentBase Memory wiring for LangGraph.

Three factories, each used once by the router at startup:
  build_checkpointer  → short-term: persists conversation turns per session
  build_memory_tools  → long-term: remember/recall @tools for the LLM
  make_recall_node    → long-term: auto-injects relevant facts before chatbot

actor_id is always read from LangGraph configurable (set by the router at
invoke time) — never exposed as a tool parameter so the LLM cannot
impersonate another user. strategy_id is a deployment-time constant.
"""
from __future__ import annotations

from langchain_core.messages import SystemMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.config import get_config

from greennode_agent_bridge import AgentBaseMemoryEvents
from greennode_agentbase.memory import MemoryClient
from greennode_agentbase.memory.models import (
    MemoryRecordInsertDirectlyRequest,
    MemoryRecordSearchRequest,
)

from app.agent.state import State


def build_checkpointer(memory_id: str) -> AgentBaseMemoryEvents:
    return AgentBaseMemoryEvents(memory_id=memory_id)


def build_memory_tools(memory_client: MemoryClient, memory_id: str, strategy_id: str) -> list:
    def _actor_id() -> str:
        return get_config()["configurable"].get("actor_id", "default")

    def _namespace(actor_id: str) -> str:
        return f"/strategies/{strategy_id}/actors/{actor_id}"

    @tool
    def remember(fact: str) -> str:
        """Store a fact in long-term memory for later retrieval."""
        try:
            memory_client.insert_memory_records_directly(
                id=memory_id,
                namespace=_namespace(_actor_id()),
                request=MemoryRecordInsertDirectlyRequest(memory_records=[fact]),
            )
        except Exception as e:  # degrade gracefully — don't crash the turn
            return f"Could not save to long-term memory: {e}"
        return f"Remembered: {fact}"

    @tool
    def recall(query: str) -> str:
        """Search long-term memory for facts relevant to a query."""
        try:
            results = memory_client.search_memory_records(
                id=memory_id,
                namespace=_namespace(_actor_id()),
                request=MemoryRecordSearchRequest(query=query, limit=5),
            )
            if not results:
                return "No relevant memories found."
            lines = []
            for r in results:
                mem = getattr(r, "memory", None) or ""
                score = getattr(r, "score", None)
                lines.append(f"- {mem} (score: {score:.2f})" if isinstance(score, (int, float)) else f"- {mem}")
            return "\n".join(lines)
        except Exception as e:  # search or formatting failed — degrade gracefully
            return f"Could not search long-term memory: {e}"

    return [remember, recall]


def make_recall_node(memory_client: MemoryClient, memory_id: str, strategy_id: str):
    """Return a node that auto-injects relevant memories before the chatbot responds."""

    def recall_memories(state: State, config: RunnableConfig) -> dict:
        actor_id = config["configurable"].get("actor_id", "default")
        last = state["messages"][-1].content if state["messages"] else ""
        if not last:
            return state
        try:
            namespace = f"/strategies/{strategy_id}/actors/{actor_id}"
            results = memory_client.search_memory_records(
                id=memory_id,
                namespace=namespace,
                request=MemoryRecordSearchRequest(query=last, limit=5),
            )
            if results:
                facts = "\n".join(f"- {r.memory}" for r in results)
                return {"messages": [SystemMessage(content=f"Relevant memories:\n{facts}")]}
        except Exception:
            pass
        return state

    return recall_memories
