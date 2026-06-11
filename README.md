# vng-campus-tour-bot

A multi-service LangGraph chatbot on **GreenNode AgentBase**. The first service is
**campus tour visits**: visitors chat to request a tour (the bot collects the
requirements and emails the owner), and the owner uses the same bot to follow up
on requests with other teams.

> Built for the `clawathon2026-snoopy-services` project. Designed to grow — new
> services plug into `services/` without touching `main.py`.

## How it works

```
                       context.user_id in config owner_identities?
POST /invocations ──────────────┬───────────────┬──────────────────
   {message, history?}          │ no            │ yes
                                ▼               ▼
                          VISITOR graph     OWNER graph
                          (visitor tools)   (visitor + owner tools)
                                │               │
                                ▼               ▼
                    get_tour_process_info   list_tour_requests
                    submit_tour_request     get_tour_request
                          │                 update_tour_status
                          ▼                 notify_team
                    emails owner            (emails teams)
```

- **Slot-filling** is handled by the LLM: it keeps asking for missing fields and
  only calls `submit_tour_request` once everything is collected and confirmed.
- **Role gating**: owner-only tools are exposed only when the caller's user id
  matches `owner_identities` in `config.yaml`.

## Project structure

| File | Purpose |
|------|---------|
| `main.py` | AgentBase HTTP server + LangGraph wiring + role routing |
| `config.yaml` | **The config**: owner, teams, sender, service settings |
| `config.py` | Loads `config.yaml`, resolves role/owner/teams |
| `prompts.py` | Visitor vs owner system prompts |
| `email_client.py` | Email sender — **stubbed (logs to `outbox.log`); real send wired later** |
| `store.py` | Tour request persistence (file-based JSON; swap for a DB later) |
| `services/registry.py` | Service registry that makes the bot multi-service |
| `services/tour_visit.py` | Tour service tools (submit / list / status / notify) |

## Setup (local)

```bash
python3 -m venv venv && source venv/bin/activate   # Python 3.10+ recommended
pip install -r requirements.txt
cp .env.example .env                                # fill in IAM + LLM values
```

Configure the LLM in `.env` (GreenNode AIP recommended — use `/agentbase-llm`):

```
LLM_API_KEY=...
LLM_BASE_URL=https://maas-llm-aiplatform-hcm.api.vngcloud.vn/v1
LLM_MODEL=...
```

## Run locally

```bash
python3 main.py        # serves on http://0.0.0.0:8080
```

**As a visitor:**
```bash
curl -X POST http://127.0.0.1:8080/invocations \
  -H "Content-Type: application/json" \
  -H "X-GreenNode-AgentBase-User-Id: visitor-1" \
  -d '{"message": "I want to book a campus tour"}'
```

**As the owner** (user id must be in `config.yaml` `owner_identities`):
```bash
curl -X POST http://127.0.0.1:8080/invocations \
  -H "Content-Type: application/json" \
  -H "X-GreenNode-AgentBase-User-Id: owner" \
  -d '{"message": "list the new tour requests"}'
```

**Multi-turn before memory is added** — pass prior turns in `history`:
```bash
curl -X POST http://127.0.0.1:8080/invocations \
  -H "Content-Type: application/json" \
  -H "X-GreenNode-AgentBase-User-Id: visitor-1" \
  -d '{"message": "my name is Lan", "history": [{"role":"user","content":"I want a tour"},{"role":"assistant","content":"Sure! What is your name?"}]}'
```

Health check: `curl http://127.0.0.1:8080/health`

Stubbed emails are appended to `outbox.log`; tour requests are stored in `tour_requests.json`.

## What's intentionally left for later

- **Email delivery** — `email_client.send_email` currently logs only. Implement the
  SMTP / API / MCP branch there; nothing else changes.
- **Memory** — currently stateless (client passes `history`). To enable AgentBase
  short-term memory, create a memory with `/agentbase-memory`, set `MEMORY_ID`, and
  compile the graphs with an `AgentBaseMemoryEvents` checkpointer (see the NOTE in
  `main.py`).
- **More services** — add `services/<name>.py`, register it, import it in
  `services/__init__.py`.

## Deploy

Use `/agentbase-deploy` to build, push to the managed Container Registry, and create
the runtime. Then `/agentbase-monitor` for logs and metrics.
