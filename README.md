# vng-campus-tour-bot

A multi-service LangGraph chatbot on **GreenNode AgentBase** with short-term and long-term memory. The first service is **campus tour visits**: an internal *requester* chats to organize a tour (the bot collects requirements and emails the owner), and the *owner* (coordinator) uses the same bot to manage and follow up on requests.

> Built for the `clawathon2026-snoopy-services` project. Designed to grow — new services plug into `services/` and new roles into `config.yaml`, without touching `main.py`.

## How it works

```
POST /invocations  {message}
        │
        │  config.role_for(context.user_id)   (data-driven; default = requester)
        ├─── requester ──▶  REQUESTER graph  (requester tools + memory)
        └─── owner     ──▶  OWNER graph      (owner extends requester: all tools + memory)
                           │
                    ┌──────┴──────┐
                    ▼             ▼
              recall node    checkpointer
          (long-term facts)  (short-term turns)
                    │
                    ▼  chatbot
            requester tools         owner tools
         ─────────────────────  ──────────────────────
         get_tour_process_info  list_tour_requests
         submit_tour_request    get_tour_request
                                update_tour_status
                                notify_team
```

- **Slot-filling** — the LLM keeps asking for missing fields and only calls `submit_tour_request` once everything is collected and confirmed.
- **Roles** (`roles:` in `config.yaml`) — data-driven. Each role lists `identities`, may `extend` another (owner extends requester → inherits its tools + prompts), and one is the `default` for unmatched users. One graph is built per role; `config.role_for(user_id)` selects it. Adding a role is config + a service binding — no agent change.
- **Routing topology** (`routing:` in `config.yaml`) — `flat` (default; the diagram above) binds all service tools to one LLM per role. `supervisor` adds a classifier that routes intent to a per-service subgraph — better at 4+ services. See [docs/supervisor-routing.md](docs/supervisor-routing.md).
- **Short-term memory** — `AgentBaseMemoryEvents` checkpointer persists conversation turns per `(actor_id, session_id)`. No need to pass `history` in the payload.
- **Long-term memory** — `remember` / `recall` tools backed by `MemoryClient`. A `recall` node automatically surfaces relevant facts before the chatbot responds.

## Project structure

```
main.py                          # thin entrypoint → app.server.app (HTTP)
chatbot_cli.py                   # entry shim → app.cli (local REPL tester)
config.yaml                      # ◀ business config: roles, routing, per-service settings
rules/                           # ◀ per-service rule files (frontmatter → auto tools)
  tour_visit/*.md|*.yaml
app/                             # ── GENERAL: nothing here knows about tours ──
  settings.py                    # env settings (LLM, memory, data_dir), fail-fast
  config.py                      # config.yaml → AppConfig, role_for(), role_chain(), service(key)
  container.py                   # general deps only: settings, config, mailer, memory_client
  bootstrap.py                   # composition root: builds the router (shared by server + CLI)
  server.py                      # HTTP adapter: AgentBase entrypoint, maps context → chat()
  cli.py                         # ChatbotCLI — local REPL tester (no HTTP)
  infrastructure/
    mailer.py                    # EmailSender — general, STUBBED (logs to outbox.log)
  agent/
    state.py                     # State TypedDict (messages + route)
    llm.py                       # LLM client factory
    prompts.py                   # assemble_prompt(role, sections) + role presentation map
    graph.py                     # flat LangGraph builder (state + nodes + checkpointer)
    classify.py                  # supervisor: intent classifier node
    supervisor.py                # supervisor: classify → per-service subgraph builder
    memory.py                    # memory factories: checkpointer, tools, recall node
    router.py                    # role resolution + flat/supervisor toggle + chat()
  services/
    registry.py                  # Service, ServiceRegistry
    config.py                    # ServiceConfig base, Rule, load_rules()
    __init__.py                  # register_all() — add new services to REGISTRARS here
    tour_visit/                  # ── tour-specific: everything tour lives here ──
      __init__.py                # register() — builds repo + tools + prompts
      config.py                  # TourServiceConfig(ServiceConfig), RequestField, Team
      domain.py                  # TourRequest, TourStatus (pure data)
      repository.py              # file-based persistence (swap for DB later)
      prompts.py                 # build_requester_prompt / build_owner_prompt
      tools.py                   # build_tools() → {role: [tools]}
```

**Layer responsibilities** — the `app/` core is service-agnostic; each service is self-contained under `app/services/<name>/`.
| Layer | Knows about | Never imports |
|-------|-------------|---------------|
| `app/infrastructure` | nothing service-specific | agent, services, server |
| `app/agent` | registry, settings, config | any specific service |
| `app/services/<name>` | its own domain/repo/config + general container | other services, server |
| `app/server` | everything (composition root) | — |

## Setup (local)

```bash
python3 -m venv venv && source venv/bin/activate   # Python 3.13 recommended
pip install -r requirements.txt
cp .env.example .env                                # fill in values
```

**Required env vars:**

```env
# IAM service account
GREENNODE_CLIENT_ID=
GREENNODE_CLIENT_SECRET=
GREENNODE_AGENT_IDENTITY=

# LLM (any OpenAI-compatible provider)
LLM_API_KEY=
LLM_BASE_URL=https://maas-llm-aiplatform-hcm.api.vngcloud.vn/v1
LLM_MODEL=
```

Use `/agentbase-llm` to get a GreenNode AIP key and browse available models.

**Enable memory (optional but recommended):**

Create a memory store with `/agentbase-memory`, then add to `.env`:

```env
MEMORY_ID=mem_...
MEMORY_STRATEGY_ID=<strategy-id-from-memory-creation>
```

When both are set, the agent automatically gains short-term and long-term memory. When either is unset, it runs stateless.

## Run locally

```bash
python3 main.py        # serves on http://0.0.0.0:8080
```

When using memory, both user and session headers are required:

**As a requester** (any user id not matched to another role):
```bash
curl -X POST http://127.0.0.1:8080/invocations \
  -H "Content-Type: application/json" \
  -H "X-GreenNode-AgentBase-User-Id: requester-1" \
  -H "X-GreenNode-AgentBase-Session-Id: session-1" \
  -d '{"message": "I want to organize a campus tour"}'
```

**As the owner** (user id must match an `owner` identity under `roles:` in `config.yaml`):
```bash
curl -X POST http://127.0.0.1:8080/invocations \
  -H "Content-Type: application/json" \
  -H "X-GreenNode-AgentBase-User-Id: owner" \
  -H "X-GreenNode-AgentBase-Session-Id: session-1" \
  -d '{"message": "list the new tour requests"}'
```

Multi-turn conversation is handled automatically by the checkpointer — just keep the same `session_id` header across requests.

Health check: `curl http://127.0.0.1:8080/health`

## Test from the CLI (no HTTP)

`ChatbotCLI` drives the agent directly through the same wiring as the server — handy for quickly exercising services and roles without curl:

```bash
python chatbot_cli.py                 # default user → default role (requester)
python chatbot_cli.py --user owner     # test as an owner identity
python chatbot_cli.py --user alice --session demo-1
```

In-REPL commands: `/whoami` (show user/role/session), `/user <id>` (switch role — starts a new session), `/new [id]` (fresh conversation), `/quit`. Same `.env` (LLM, optional memory) as the server.

Stubbed emails are appended to `outbox.log`; tour requests are stored in `tour_requests.json`.

## Add a new service

1. Create `app/services/<name>/` with:
   - `config.py` — dataclass + `load(raw: dict)` that parses `config.yaml → services.<name>`
   - `prompts.py` — functions returning a prompt section string per role
   - `tools.py` — `build_tools(...)` returning role-keyed tools `{role: [tools]}`
   - `__init__.py` — `register(container, registry)` building `Service(tools={...}, prompts={...})`
2. Add the package to `REGISTRARS` in `app/services/__init__.py`.
3. Add its config block under `services:` in `config.yaml`.

The role names you bind (`tools`/`prompts` keys) must match roles in `config.yaml`.
The agent, graph, router, and server require no changes.

## Add a new role

1. Add an entry under `roles:` in `config.yaml` (`identities:`, optional `extends:`).
2. Bind `tools`/`prompts` for that role name in the services that should serve it.

`config.role_for()` and the per-role graph builder pick it up automatically.

No changes to the agent, graph, or server are needed.

## What's intentionally left for later

- **Email delivery** — `EmailSender.send_email` currently logs to `outbox.log` only. Implement the SMTP / API branch there; nothing else changes.

## Deploy

Use `/agentbase-deploy` to build, push to the managed Container Registry, and create the runtime. Then `/agentbase-monitor` for logs and metrics.
