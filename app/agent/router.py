"""Role-based routing with optional AgentBase Memory and pluggable graph topology.

Two layers of routing:
  1. Role — config.role_for(user_id) resolves the caller's role (data-driven, with
     inheritance via `extends`). One graph is built per role declared in config.
  2. Graph topology — config.routing selects how each role's graph is built:
       flat       — all the role's service tools bound to one LLM (build_tool_graph)
       supervisor — classify intent → per-service subgraph (build_supervisor_graph)

Memory (checkpointer + tools + recall node) is layered into either topology.
"""
from __future__ import annotations

from functools import partial

from langchain_core.messages import HumanMessage

from app.agent.graph import build_tool_graph
from app.agent.llm import build_llm
from app.agent.prompts import assemble_prompt
from app.agent.supervisor import build_supervisor_graph
from app.config import AppConfig
from app.container import Container
from app.services.registry import ServiceRegistry
from app.settings import Settings


class AgentRouter:
    def __init__(self, config: AppConfig, graphs: dict):
        self._config = config
        self._graphs = graphs

    @classmethod
    def build(cls, settings: Settings, config: AppConfig, registry: ServiceRegistry, container: Container) -> "AgentRouter":
        llm = build_llm(settings)
        services = registry.services()

        checkpointer = None
        recall_node = None
        memory_tools: list = []
        if settings.memory_enabled:
            from app.agent.memory import build_checkpointer, build_memory_tools, make_recall_node
            checkpointer = build_checkpointer(settings.memory_id)
            memory_tools = build_memory_tools(container.memory_client, settings.memory_id, settings.memory_strategy_id)
            recall_node = make_recall_node(container.memory_client, settings.memory_id, settings.memory_strategy_id)

        graphs = {}
        for role in config.roles():
            chain = config.role_chain(role)          # e.g. owner → [owner, requester]
            assemble = partial(assemble_prompt, role)
            if config.routing == "supervisor":
                graphs[role] = build_supervisor_graph(
                    llm, services, role_chain=chain, assemble=assemble,
                    extra_tools=memory_tools, checkpointer=checkpointer, recall_node=recall_node,
                )
            else:  # flat
                tools = [t for r in chain for t in registry.tools_for(r)]
                sections = [p for r in chain for p in registry.prompts_for(r)]
                graphs[role] = build_tool_graph(
                    llm, tools + memory_tools, assemble(sections),
                    checkpointer=checkpointer, recall_node=recall_node,
                )

        return cls(config=config, graphs=graphs)

    def chat(self, user_id: str | None, session_id: str, message: str) -> dict:
        role = self._config.role_for(user_id)
        graph = self._graphs.get(role) or self._graphs[self._config.default_role]
        config = {
            "configurable": {
                "thread_id": session_id,
                "actor_id": user_id or "anonymous",
            }
        }
        result = graph.invoke({"messages": [HumanMessage(content=message)]}, config)
        return {
            "role": role,
            "response": result["messages"][-1].content,
        }
