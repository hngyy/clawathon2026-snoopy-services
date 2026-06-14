# Snoopy Services

An internal AI assistant platform for VNG, built on **GreenNode AgentBase** (LangGraph). It handles multiple internal services through a single chatbot — role-aware, extensible, and deployable as a Custom Agent runtime.

The first service is **Campus Tour Visit**, owned by the **CBC team** — helping internal employees organize corporate campus visits and helping coordinators manage them end-to-end.

---

## Services

| Service | Team | What it does |
|---------|------|--------------|
| `tour_visit` | CBC | Organize corporate campus visits — slot-fill request, notify coordinator, manage follow-up |

> Adding a new service only requires a new folder under `app/services/` and an entry in `config.yaml`. No changes to the agent, graph, or server.

---

## How it works

```
POST /invocations  {message}
        │
        │  role resolved from X-GreenNode-AgentBase-User-Id header
        ├─── requester (default) ──▶  REQUESTER graph
        └─── owner               ──▶  OWNER graph  (extends requester — all tools)
                                │
                         ┌──────┴──────┐
                         ▼             ▼
                   recall node    checkpointer
               (long-term facts)  (short-term turns)
                         │
                         ▼  chatbot
               requester tools              owner tools
            ──────────────────────   ──────────────────────────
            get_tour_process_info    list_tour_requests
            submit_tour_request      get_tour_request
            check_my_request         update_tour_status
                                     notify_team
                                     check_visit_progress
```

**Roles** are data-driven in `config.yaml` — each role lists `identities` (matched user IDs), may `extend` another role, and one is the `default` for unmatched users. Adding a role is config-only.

**Routing topology** (`routing:` in `config.yaml`):
- `flat` — all service tools bound to one LLM per role. Best for 1–3 services.
- `supervisor` — intent classifier routes to a per-service subgraph. Better at 4+ services. See [docs/supervisor-routing.md](docs/supervisor-routing.md).

---

## Campus Tour Visit — service detail

An internal VNG employee (the **requester**) chats with the bot to organize a corporate visit for external guests. The bot collects all required fields, asks the requester to confirm, then:
1. Saves the request to `tour_requests.json`
2. Emails the **owner** (CBC coordinator) with full visit details
3. Emails the **requester** a confirmation with the request ID
4. Later, the requester can check status with their request ID — a high-level overview only (no internal team detail)

The **owner** uses the same bot to list requests, update status (`new → in_review → approved → scheduled → completed`), and loop in supporting teams (BIE, EB, PR, IT, AF) via email or Trello.

**Required fields:** requester name, guest organization, guest profile, visit type, visit date, group size, purpose, contact email.
**Optional:** meeting topic (for `tour + meeting`), partner gift.

---

## Project structure

```
main.py                          # thin entrypoint → app.server.app
chatbot_cli.py                   # local REPL tester (no HTTP)
config.yaml                      # business config: roles, routing, per-service settings
rules/                           # rule markdown files → auto-generated read-only tools
  tour_visit/
app/
  settings.py                    # env vars (LLM, memory, paths) — fail-fast on missing
  config.py                      # config.yaml → AppConfig, role_for(), role_chain()
  container.py                   # shared deps: settings, config, mailer, memory_client
  bootstrap.py                   # builds AgentRouter (shared by server + CLI)
  server.py                      # FastAPI HTTP adapter for AgentBase runtime
  cli.py                         # ChatbotCLI — local REPL
  infrastructure/
    outlook_client.py            # OutlookSender — Graph API email, outbox-stub fallback
  agent/
    state.py                     # State TypedDict (messages + route)
    llm.py                       # LLM client factory
    prompts.py                   # assemble_prompt(role, sections)
    graph.py                     # flat LangGraph builder
    supervisor.py                # supervisor graph builder
    classify.py                  # intent classifier node
    memory.py                    # checkpointer, memory tools, recall node
    router.py                    # role resolution + flat/supervisor toggle
  services/
    registry.py                  # ServiceRegistry
    __init__.py                  # register_all() — add new services to REGISTRARS here
    tour_visit/                  # self-contained CBC tour-visit service
      config.py                  # TourServiceConfig, RequestField, Team
      domain.py                  # TourRequest, TourStatus (pure data, no I/O)
      repository.py              # file-based JSON persistence
      prompts.py                 # build_requester_prompt / build_owner_prompt
      tools.py                   # build_tools() → {role: [tools]}
      __init__.py                # register(container, registry)
```

**Layer rule:** `app/` core is service-agnostic. Each service is self-contained under `app/services/<name>/` and never imports from other services.

---

## Setup (local)

```bash
python3 -m venv venv && source venv/bin/activate   # Python 3.13 recommended
pip install -r requirements.txt
cp .env.example .env                                # core app config
```

### Credentials layout

`.env` holds only **core app config** (LLM, paths, memory). Each external integration
keeps its secrets in its own `*.credentials.json` file under `app/credentials/` (all
git/Docker-ignored). Env vars still override the files, so a deployed runtime can inject
the same values as secrets.

| Concern | File | Contents |
|---------|------|----------|
| LLM + paths + memory | `.env` | `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL`, `CONFIG_PATH`, `MEMORY_*` |
| GreenNode IAM | `.greennode.json` | SDK-managed (`client_id`, `client_secret`) |
| Outlook / Graph | `app/credentials/o365.credentials.json` | `tenant_id`, `client_id` |
| Trello (BIE) | `app/credentials/trello.credentials.json` | `api_key`, `token` |
| Google (sheet + calendar) | `app/credentials/google.credentials.json` | `service_account`, `sheet_id`, `calendar_id` |

OAuth token caches are written to `app/credentials/cache/`. Edit the
`app/credentials/*.credentials.json` files directly to fill in each integration's
secrets (they are git/Docker-ignored). Use `/agentbase-llm` to get a GreenNode AIP
key and model list.

**Email delivery via Outlook** (optional — falls back to `outbox.log` stub):
fill `o365.credentials.json` (contact sonnh3 for VNG M365 tenant/client IDs), then run
the one-time device login:
```bash
python -m app.infrastructure.outlook_client login
```
Token is cached in `.o365_token_cache.json` and auto-refreshed on every send. Re-login
only needed if the token expires (90 days of inactivity) or is revoked.

**Trello / Google** (optional — fall back to email when unset): fill the respective
credential files; for Google, share the sheet + calendar with the service-account email.

**Memory** (optional — stateless if unset): set `MEMORY_ID` + `MEMORY_STRATEGY_ID` in
`.env` (create a store with `/agentbase-memory`) to enable short- and long-term memory.

---

## Run locally

```bash
python3 main.py        # HTTP server on http://0.0.0.0:8080
```

**As requester** (default — any unmatched user ID):
```bash
curl -X POST http://127.0.0.1:8080/invocations \
  -H "Content-Type: application/json" \
  -H "X-GreenNode-AgentBase-User-Id: hieunx" \
  -H "X-GreenNode-AgentBase-Session-Id: session-1" \
  -d '{"message": "I want to organize a campus tour for a partner visit"}'
```

**As owner** (user ID must match an `owner` identity in `config.yaml`):
```bash
curl -X POST http://127.0.0.1:8080/invocations \
  -H "Content-Type: application/json" \
  -H "X-GreenNode-AgentBase-User-Id: tour-coordinator@vng.com.vn" \
  -H "X-GreenNode-AgentBase-Session-Id: session-1" \
  -d '{"message": "list new tour requests"}'
```

Keep the same `session_id` across requests for multi-turn conversation.

Health check: `curl http://127.0.0.1:8080/health`

## CLI tester (no HTTP)

```bash
python chatbot_cli.py                              # default role (requester)
python chatbot_cli.py --user tour-coordinator@vng.com.vn   # as owner
python chatbot_cli.py --user hieunx --session demo-1
```

In-REPL commands: `/whoami`, `/user <id>`, `/new [id]`, `/quit`.

---

## Add a new service

1. Create `app/services/<name>/` with `config.py`, `domain.py`, `repository.py`, `prompts.py`, `tools.py`, `__init__.py` (see `tour_visit/` as reference).
2. Add `from app.services import <name>` to `REGISTRARS` in `app/services/__init__.py`.
3. Add its config block under `services:` in `config.yaml`.

No changes to the agent, graph, router, or server.

## Add a new role

1. Add an entry under `roles:` in `config.yaml`.
2. Bind tools/prompts for that role name in the relevant services.

`config.role_for()` and the graph builder pick it up automatically.

---

## Deploy

```bash
# Build + push Docker image, create AgentBase runtime
/agentbase-deploy

# Monitor logs and metrics
/agentbase-monitor
```
