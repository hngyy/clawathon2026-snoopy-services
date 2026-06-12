"""LangGraph builder.

Compiles a tool-calling graph with an optional checkpointer (short-term memory)
and an optional recall node that runs before the chatbot (long-term memory).
"""
from __future__ import annotations

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import SystemMessage
from langgraph.graph import START, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from app.agent.state import State


def build_tool_graph(
    llm: BaseChatModel,
    tools: list,
    system_prompt: str,
    *,
    checkpointer=None,
    recall_node=None,
):
    model = llm.bind_tools(tools) if tools else llm

    def chatbot(state: State) -> dict:
        msgs = state["messages"]
        if not msgs or not isinstance(msgs[0], SystemMessage):
            msgs = [SystemMessage(content=system_prompt)] + msgs
        return {"messages": [model.invoke(msgs)]}

    builder = StateGraph(State)
    builder.add_node("chatbot", chatbot)

    if recall_node is not None:
        builder.add_node("recall", recall_node)
        builder.add_edge(START, "recall")
        builder.add_edge("recall", "chatbot")
    else:
        builder.add_edge(START, "chatbot")

    if tools:
        builder.add_node("tools", ToolNode(tools))
        builder.add_conditional_edges("chatbot", tools_condition)
        builder.add_edge("tools", "chatbot")

    return builder.compile(checkpointer=checkpointer)
