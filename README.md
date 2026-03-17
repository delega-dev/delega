<p align="center">
  <img src="logo.png" alt="Delega" width="80">
</p>

<h1 align="center">Delega</h1>

<p align="center">
  <strong>Task infrastructure for AI agents.</strong><br>
  API + MCP + CLI. Agent-to-agent delegation. Open source.
</p>

<p align="center">
  <a href="#license"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="License"></a>
  <a href="#"><img src="https://img.shields.io/badge/python-3.10+-blue.svg" alt="Python"></a>
  <a href="https://delega.dev"><img src="https://img.shields.io/badge/site-delega.dev-00d4ff.svg" alt="Website"></a>
</p>

---

<p align="center">
  <img src="https://github.com/delega-dev/delega/releases/download/v1.0.0/delega-demo.gif" alt="Delega demo: three AI agents collaborating on a bug fix" width="720">
</p>

---

## Try it

**MCP (Claude Code, Cursor, Codex, OpenClaw):**
```
npx @delega-dev/mcp
```

**Hosted API (free, 1,000 tasks/month):** [delega.dev](https://delega.dev)

**Self-hosted (MIT, SQLite):**
```bash
git clone https://github.com/delega-dev/delega && cd delega/backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt && python main.py
```

## What is Delega?

Delega is the task backend your AI agents are missing. Instead of bolting task management onto your agent framework, Delega gives agents a shared API for creating tasks, delegating work to each other, and tracking everything through to completion.

It works with any agent framework (CrewAI, LangGraph, OpenAI Agents SDK) via REST, from your terminal with the [CLI](https://github.com/delega-dev/delega-cli), or natively with Claude Code, Cursor, OpenClaw, and other MCP clients via the [delega-mcp](https://github.com/delega-dev/delega-mcp) package.

**Self-hosted** (free, forever) or **hosted** at [api.delega.dev](https://delega.dev).

## Why Delega?

AI agents can write code, draft emails, and analyze data. But they can't coordinate.

When Agent A needs Agent B to do something, there's no standard way to hand off that task, track whether it got done, or pass context along. Most teams hack this with message queues, shared databases, or prompt chains. Delega makes it a first-class primitive.

This is the same pattern playing out across agent infrastructure. AgentMail exists because Gmail wasn't built for agents — they raised $6M to build email infrastructure purpose-built for AI. Ramp launched Agent Cards because human credit cards weren't built for autonomous spending. Delega exists because Todoist, Linear, and Asana weren't built for agents. The tools agents need look different from the tools humans need.

- **Agent identity**: Each agent gets an API key. Tasks track who created, assigned, and completed them.
- **Delegation chains**: Agent A delegates to Agent B, who delegates to Agent C. Full chain visible.
- **Persistent context**: Attach structured context to tasks that survives across agent sessions.
- **Lifecycle webhooks**: Get notified when tasks are created, completed, delegated, or assigned.
- **Semantic dedup**: Catch duplicate tasks before they're created (TF-IDF, zero API cost).

## Features

| Category | What you get |
|----------|-------------|
| **Core** | Tasks, projects, subtasks, comments, labels, priorities, due dates, recurring tasks |
| **Agents** | Agent registration, API key auth, per-agent task tracking, identity on every action |
| **Delegation** | Parent/child task chains, root task tracking, delegation depth, chain visualization |
| **Context** | JSON context blobs on tasks, PATCH merge for incremental updates |
| **Webhooks** | 7 lifecycle events, HMAC signatures, delivery logging, auto-disable after failures |
| **Dedup** | Semantic similarity detection via TF-IDF, configurable threshold, `/api/tasks/dedup`, optional `X-Dedup-Check` header |
| **Security** | API key auth enabled by default (`DELEGA_REQUIRE_AUTH`), rate limiting, configurable CORS |
| **Database** | SQLite (one file, zero ops) or Postgres |

## Quick Start

```bash
git clone https://github.com/delega-dev/delega.git
cd delega/backend

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

python main.py
```

API is live at `http://localhost:18890`. Interactive docs at `/docs`.

### Docker

```bash
cp .env.example .env
docker compose up --build -d
```

#### Bootstrapping your first agent (Docker)

When `DELEGA_REQUIRE_AUTH=true`, the very first agent must be created from inside the
container (localhost-only restriction). This first agent automatically becomes admin:

```bash
docker exec delega curl -s -X POST http://localhost:18890/api/agents \
  -H "Content-Type: application/json" \
  -d '{"name": "admin"}' | python3 -m json.tool
```

Save the `api_key` from the response — it's shown only once. All subsequent agent
creation requires this admin key via the `X-Agent-Key` header.

> **Without auth** (`DELEGA_REQUIRE_AUTH=false`, the default): no bootstrap needed.
> All endpoints work without an API key.

### CLI

```bash
npm install -g @delega-dev/cli
delega login
delega tasks create "Research competitor pricing" --priority 3
delega tasks list
delega agents list
```

See [delega-cli](https://github.com/delega-dev/delega-cli) for all commands.

### MCP (Claude Code, Cursor, OpenClaw, etc.)

```bash
npm install -g @delega-dev/mcp
```

Add to your MCP client config:

```json
{
  "mcpServers": {
    "delega": {
      "command": "npx",
      "args": ["-y", "@delega-dev/mcp"],
      "env": {
        "DELEGA_API_URL": "http://127.0.0.1:18890"
      }
    }
  }
}
```

See [delega-mcp](https://github.com/delega-dev/delega-mcp) for all 14 MCP tools.

## API Reference

### Agents

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/agents` | List registered agents |
| `POST` | `/api/agents` | Register a new agent (returns API key) |
| `GET` | `/api/agents/{id}` | Get agent details |
| `PUT` | `/api/agents/{id}` | Update agent |
| `DELETE` | `/api/agents/{id}` | Remove agent |
| `POST` | `/api/agents/{id}/rotate-key` | Rotate API key |

### Tasks

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/tasks` | List tasks (filter by project, label, due date, completion) |
| `POST` | `/api/tasks` | Create a task |
| `GET` | `/api/tasks/{id}` | Get task with subtasks |
| `PUT` | `/api/tasks/{id}` | Update a task |
| `DELETE` | `/api/tasks/{id}` | Delete a task |
| `POST` | `/api/tasks/{id}/complete` | Mark complete (tracks which agent completed it) |
| `POST` | `/api/tasks/{id}/delegate` | Delegate: create a child task assigned to another agent |
| `GET` | `/api/tasks/{id}/chain` | View full delegation chain |
| `PATCH` | `/api/tasks/{id}/context` | Merge keys into task context blob |
| `GET` | `/api/tasks/{id}/context` | Get task context |

**Query filters:** `?due=today`, `?due=overdue`, `?label=@agent`, `?completed=true`, `?project_id=1`

**Dedup:** Use `POST /api/tasks/dedup` for an explicit similarity check, or add `X-Dedup-Check: true` to `POST /api/tasks` to reject near-duplicates before creation.

### Projects, Subtasks, Comments

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET/POST` | `/api/projects` | List / create projects |
| `PUT/DELETE` | `/api/projects/{id}` | Update / delete project |
| `GET/POST` | `/api/tasks/{id}/subtasks` | List / add subtasks |
| `GET/POST` | `/api/tasks/{id}/comments` | List / add comments |
| `GET` | `/api/stats` | Dashboard stats |

### Webhooks

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/webhooks` | List webhooks |
| `POST` | `/api/webhooks` | Register a webhook |
| `PUT` | `/api/webhooks/{id}` | Update webhook |
| `DELETE` | `/api/webhooks/{id}` | Remove webhook |
| `GET` | `/api/webhooks/{id}/deliveries` | View delivery history |

**Events:** `task.created`, `task.updated`, `task.completed`, `task.deleted`, `task.assigned`, `task.delegated`, `task.commented`

All webhook payloads include HMAC signatures for verification.

## Agent Delegation Example

The core use case: agents coordinating work through Delega.

```python
import requests

API = "http://localhost:18890"
HEADERS = {"X-Agent-Key": "dlg_your_key_here"}

# Coordinator creates a task and delegates to researcher
task = requests.post(f"{API}/api/tasks", json={
    "content": "Research competitor pricing",
    "labels": ["@researcher"],
    "priority": 3
}, headers=HEADERS).json()

# Delegate to the researcher agent (creates a child task)
child = requests.post(f"{API}/api/tasks/{task['id']}/delegate", json={
    "content": "Pull pricing pages for top 5 competitors",
    "labels": ["@researcher"]
}, headers=HEADERS).json()

# Researcher picks it up, attaches context as they work
requests.patch(f"{API}/api/tasks/{child['id']}/context", json={
    "competitors_found": 5,
    "status": "scraping"
}, headers={"X-Agent-Key": "dlg_researcher_key_here"})

# Researcher completes - coordinator gets a webhook notification
requests.post(f"{API}/api/tasks/{child['id']}/complete",
    headers={"X-Agent-Key": "dlg_researcher_key_here"})

# View the full delegation chain
chain = requests.get(f"{API}/api/tasks/{task['id']}/chain").json()
```

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `DELEGA_HOST` | `0.0.0.0` | Bind address |
| `DELEGA_PORT` | `18890` | API port |
| `DELEGA_DB_PATH` | `./data/delega.db` | SQLite database path |
| `DELEGA_REQUIRE_AUTH` | `true` | Require `X-Agent-Key` on all API routes |
| `DELEGA_CORS_ORIGINS` | `*` | Comma-separated allowed origins |
| `DELEGA_DATABASE_URL` | _(auto)_ | Full SQLAlchemy URL (for Postgres, etc.) |
| `DELEGA_ALLOW_PRIVATE_WEBHOOKS` | `false` | Allow webhook URLs pointing to private/localhost IPs |

### Security

By default, Delega requires authentication on all API routes. Bootstrap the first admin agent from the same machine:

```bash
curl -X POST http://localhost:18890/api/agents \
  -H "Content-Type: application/json" \
  -d '{"name": "coordinator", "display_name": "Task Coordinator"}'
# Returns: { "api_key": "dlg_...", ... }
```

That unauthenticated bootstrap path is allowed only when there are no agents yet and the request comes from loopback.

Then pass the key on every request: `X-Agent-Key: dlg_...`

For local single-user development, you can opt out with `DELEGA_REQUIRE_AUTH=false`. Not recommended for production.

Additional hardening:

- Write requests larger than `64 KiB` are rejected early.
- Migration `005_harden_agent_auth.py` backfills existing plaintext agent keys into a split storage model (`key_lookup` + salted PBKDF2 verifier) and replaces the stored bearer token with a non-secret placeholder.
- The first registered agent is the admin agent. Agent, webhook, and project management routes require an admin key. Non-admin agents can rotate their own key; rotating another agent's key requires an admin key.
- Non-admin agents now see only tasks they created, were assigned, or completed; they no longer share the whole task workspace by default.
- Webhook URLs are validated to reject localhost, link-local, and other obvious internal targets.
- Webhook secrets are accepted on create/update, but they are not echoed back in normal API responses.
- Docker startup now runs migrations `001` through `005`, including the new auth-storage hardening migration.

If you're upgrading an existing non-Docker instance that predates split key storage, run `python backend/migrations/005_harden_agent_auth.py` against your live DB before restarting on the stricter auth build.

### Deployment

Delega is a single Python process with a SQLite file. Deploy it however you want:

- **Bare metal / VM**: `python main.py` behind Caddy/nginx
- **Docker**: `docker compose up -d`
- **systemd**: Service file included (`delega.service`)
- **launchd** (macOS): Plist template in `contrib/`

**Always run behind a reverse proxy in production.** Delega trusts the network perimeter for unauthenticated mode.

## Hosted Tier

Don't want to self-host? Use [api.delega.dev](https://delega.dev):

| Plan | Tasks/month | Price |
|------|------------|-------|
| Free | 1,000 | $0 (2 agents) |
| Pro | 50,000 | $20/mo (25 agents) |
| Scale | 500,000 | $99/mo (unlimited agents) |

Same API, same MCP tools. Just point `DELEGA_API_URL` at `https://api.delega.dev`.

## Why Delega

Most task APIs (Todoist, Linear, Asana) were built for humans. Delega was built for agents from day one:

- **Agent identity** is a first-class concept, not a bolt-on
- **Delegation chains** let agents hand off work to other agents with full traceability
- **Per-task pricing** instead of per-seat (agents aren't employees)
- **Self-hostable** with zero external dependencies (SQLite, no Redis, no queue)
- **MCP + REST** so it works with any agent framework

## Tech Stack

- **Backend**: [FastAPI](https://fastapi.tiangolo.com/) (Python)
- **Database**: [SQLite](https://sqlite.org/) via SQLAlchemy (Postgres supported)
- **CLI**: [delega-cli](https://github.com/delega-dev/delega-cli) (TypeScript)
- **MCP**: [delega-mcp](https://github.com/delega-dev/delega-mcp) (TypeScript)
- **Dedup**: scikit-learn TF-IDF (local, zero API cost)

## Contributing

PRs welcome. See [CONTRIBUTING.md](CONTRIBUTING.md).

## Name

From Latin *delegare*: to entrust, to send as a representative. Task infrastructure should delegate, not just track.

## License

[MIT](LICENSE)
