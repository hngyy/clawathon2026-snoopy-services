# Supervisor Routing

**Implemented behind a config toggle.** Set `routing:` in `config.yaml`:

```yaml
routing: flat        # all service tools bound to one LLM per role (default)
# routing: supervisor  # classify intent → dispatch to a per-service subgraph
```

`flat` and `supervisor` are interchangeable at runtime — flip the value and restart.
Code: `app/agent/classify.py`, `app/agent/supervisor.py`, branch in `app/agent/router.py`.

## When to use which

| Services | Recommended | Why |
|---|---|---|
| 1–3 | **flat** (default) | all tools bound to one LLM per role. Simpler, one LLM call per turn. |
| 4+ | **supervisor** | classify narrows tools to one service — avoids tool-list explosion / prompt bleed. |

Switch to `supervisor` when tool-list size or combined prompt length starts hurting
routing accuracy. No structural change is needed — services are already self-contained
in `app/services/<name>/`; only the graph wiring differs.

## Topology

```
                                    ┌─ svc_tour_visit  (tour tools + tour section only) ─┐
START → [recall] → [classify] ──────┼─ svc_cafeteria   (cafeteria tools only)            ┼─→ END
         (memory)   (cheap LLM,      ├─ svc_parking     (parking tools only)             │
                     sticky)         └─ general         (no service tools, just chat)  ───┘
```

Each `svc_*` node is a fully independent compiled subgraph (the existing
`build_tool_graph`), bound to **only** that service's tools and prompt section.
The parent owns the checkpointer + recall node.

## Routing layers — IMPORTANT

There are **two distinct routing decisions**, and only the first is the classifier's:

```
[classify] ──→ picks a SERVICE      ← intent routing  (the classify node, LLM)
    │
    ▼
[svc_* subgraph] ──→ picks a TOOL   ← ReAct tool-selection (the subgraph LLM, NOT the classifier)
   (tools_condition)
```

| Layer | Decides | Chosen by | Candidate set |
|---|---|---|---|
| **Intent → service** | `tour_visit` vs `cafeteria` vs `general` | `classify` node | ~5 service descriptions |
| **Service → tool** | `get_booking_restrictions` vs `submit_tour_request` | subgraph LLM (ReAct) | one service's ~3–5 tools |

**The classifier never selects individual tools.** It only narrows the candidate
tool set down to one service; the scoped subgraph LLM then does normal ReAct
tool-selection over that smaller set.

This split is deliberate. If the classifier picked individual tools it would need
to know every tool of every service — recreating the flat-list problem one level
up. Keeping each decision small is what preserves accuracy:
- the classifier reasons over a handful of stable service descriptions
- each subgraph LLM reasons over only the tools it owns

### Example trace — visitor asks *"what are the booking restrictions?"*

| Step | Node | Decision | Mechanism |
|---|---|---|---|
| 1 | `classify` | → `tour_visit` | LLM classifier (intent → service) |
| 2 | `svc_tour_visit` | LLM sees only tour tools + memory tools | scoped subgraph |
| 3 | `svc_tour_visit` chatbot | → call `get_booking_restrictions` | LLM ReAct (tool selection) |
| 4 | `tools` | runs it, returns content → END | `tools_condition` |

### A third layer (tool-level intent routing) — usually unnecessary

Only needed if a **single service** grows many tools with overlapping descriptions
and the subgraph LLM misroutes *within* that service. The fix is the same pattern
nested once more (a per-service mini-classifier). Don't add it pre-emptively —
~3–5 tools per service is well within ReAct's comfort zone.

---

## Sketch code

Additive — new files + two small field/wiring changes. Sits alongside the current
flat path; flip between them with a `routing: flat | supervisor` config toggle.

### 1. State gains a persisted `route` — `app/agent/state.py`

```python
class State(TypedDict):
    messages: Annotated[list, add_messages]
    route: str  # service key chosen by classify; persisted across turns by the checkpointer
```

`route` uses last-write-wins (only `messages` has a reducer). Persisting it enables
**sticky routing** (below).

### 2. `Service` — `description` + role-keyed bindings — `app/services/registry.py`

Roles are data-driven (see "Roles" below), so tools and prompts are keyed by role
name rather than fixed `visitor_*`/`owner_*` fields:

```python
@dataclass
class Service:
    key: str
    display_name: str
    description: str = ""                       # what the classifier routes on
    tools: dict = field(default_factory=dict)   # role -> list of tools
    prompts: dict = field(default_factory=dict) # role -> prompt section string
```

Set at registration, e.g. in `tour_visit/__init__.py`:

```python
registry.add(Service(
    key="tour_visit",
    description="Booking and managing guided campus tour visits.",
    tools={"requester": [...], "owner": [...]},
    prompts={"requester": "...", "owner": "..."},
))
```

### 3. The classifier node — `app/agent/classify.py` (new)

```python
"""Intent classifier — routes the latest message to a service key (or 'general')."""
from __future__ import annotations

from langchain_core.messages import SystemMessage
from pydantic import BaseModel, Field

from app.agent.state import State
from app.services.registry import Service


def make_classifier(llm, services: list[Service]):
    catalog = "\n".join(f'- "{s.key}": {s.description}' for s in services)
    valid = {s.key for s in services}

    class Route(BaseModel):
        service: str = Field(description="A service key, or 'general' for greetings/off-topic.")

    router_llm = llm.with_structured_output(Route)

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
            "(e.g. answering a follow-up, providing a requested field), KEEP it. "
            "Switch only on a clear topic change.\n\n"
            f"Conversation:\n{transcript}"
        )
        result = router_llm.invoke([SystemMessage(content=prompt)])
        return {"route": result.service if result.service in valid else "general"}

    return classify
```

The **sticky-routing** instruction ("currently active / keep it") is the critical
correctness fix — without it, a mid-conversation message like *"my name is Lan"*
would misclassify to `general` and break slot-filling.

### 4. The supervisor graph builder — `app/agent/supervisor.py` (new)

```python
"""Supervisor graph: classify once, then dispatch to a per-service subgraph."""
from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from app.agent.classify import make_classifier
from app.agent.graph import build_tool_graph
from app.agent.state import State


def build_supervisor_graph(
    llm, services, *, role_chain, assemble,
    extra_tools=None, checkpointer=None, recall_node=None,
):
    extra_tools = extra_tools or []          # e.g. memory tools — available everywhere

    def tools_of(s):   return [t for r in role_chain for t in s.tools.get(r, [])]
    def section_of(s): return "\n".join(s.prompts[r] for r in role_chain if s.prompts.get(r))
    included = [s for s in services if tools_of(s) or section_of(s)]

    builder = StateGraph(State)
    builder.add_node("classify", make_classifier(llm, included))
    if recall_node is not None:
        builder.add_node("recall", recall_node)
        builder.add_edge(START, "recall")
        builder.add_edge("recall", "classify")
    else:
        builder.add_edge(START, "classify")

    route_map = {}
    for s in included:
        # subgraph compiled WITHOUT a checkpointer — the parent owns persistence
        subgraph = build_tool_graph(llm, tools_of(s) + extra_tools, assemble([section_of(s)]))
        node = f"svc_{s.key}"
        builder.add_node(node, subgraph)
        builder.add_edge(node, END)
        route_map[s.key] = node

    # fallback: plain chat, no service tools (memory tools still bound)
    builder.add_node("general", build_tool_graph(llm, extra_tools, assemble([])))
    builder.add_edge("general", END)
    route_map["general"] = "general"

    builder.add_conditional_edges("classify", lambda s: route_map.get(s.get("route", "general"), "general"))
    return builder.compile(checkpointer=checkpointer)
```

`role_chain` is the role plus the roles it `extends` (e.g. `["owner", "requester"]`),
so a subgraph binds that service's tools/sections across the whole chain. `assemble`
is `partial(assemble_prompt, role)` — already bound to the role's header. Each subgraph
sees only its own service's tools + section: no tool-list explosion, no prompt bleed.

### 5. Router builds one graph per role — `app/agent/router.py`

```python
for role in config.roles():
    chain = config.role_chain(role)            # owner → ["owner", "requester"]
    assemble = partial(assemble_prompt, role)
    if config.routing == "supervisor":
        graphs[role] = build_supervisor_graph(
            llm, services, role_chain=chain, assemble=assemble,
            extra_tools=memory_tools, checkpointer=checkpointer, recall_node=recall_node,
        )
    else:  # flat
        tools    = [t for r in chain for t in registry.tools_for(r)]
        sections = [p for r in chain for p in registry.prompts_for(r)]
        graphs[role] = build_tool_graph(
            llm, tools + memory_tools, assemble(sections),
            checkpointer=checkpointer, recall_node=recall_node,
        )
# chat(): role = config.role_for(user_id); graph = graphs[role]
```

## Trade-offs

| Concern | Handling |
|---|---|
| Misroute mid-conversation | sticky routing via persisted `state["route"]` + "keep current" instruction |
| Extra latency/cost (1 classify call/turn) | **DEFERRED** — see below |
| Subgraph + parent checkpointer | subgraphs compiled without checkpointer; parent's checkpointer persists the nested state (documented LangGraph nesting pattern) |
| Roles | data-driven (`roles:` in config.yaml); one graph built per role, resolved by `config.role_for()` in `chat()`. Supervisor is per-role and honors `extends` inheritance. |

## Deferred: fast model for the classifier

The classifier currently reuses the main LLM (`build_llm(settings)`), so `supervisor`
mode adds one full-price call per turn. **Not yet optimized — deferred by choice.**

When picked up: give `build_llm` an optional fast variant (e.g. Haiku via a
`LLM_FAST_MODEL` env var) and pass it to `make_classifier(...)`. Classification is a
short, structured decision, so a small model is plenty and cuts the added cost
substantially. Until then, `flat` (the default) makes no extra calls.
