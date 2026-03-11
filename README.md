<p align="center">
  <img src="logo.png" alt="Delega" width="80">
</p>

<h1 align="center">Delega</h1>

<p align="center">
  <strong>Task infrastructure for AI agents.</strong><br>
  MCP + REST API. Agent-to-agent delegation. Open source.
</p>

<p align="center">
  <a href="#license"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="License"></a>
  <a href="#"><img src="https://img.shields.io/badge/python-3.10+-blue.svg" alt="Python"></a>
  <a href="https://delega.dev"><img src="https://img.shields.io/badge/site-delega.dev-00d4ff.svg" alt="Website"></a>
</p>

---

## What is Delega?

Delega is the task backend your AI agents are missing. Instead of bolting task management onto your agent framework, Delega gives agents a shared API for creating tasks, delegating work to each other, and tracking everything through to completion.

It works with any agent framework (CrewAI, LangGraph, OpenAI Agents SDK) via REST, or natively with Claude Desktop, Cursor, and other MCP clients via the [delega-mcp](https://github.com/delega-dev/delega-mcp) package.

**Self-hosted** (free, forever) or **hosted** at [api.delega.dev](https://delega.dev).

## Why Delega?

AI agents can write code, draft emails, and analyze data. But they can't coordinate.

When Agent A needs Agent B to do something, there's no standard way to hand off that task, track whether it got done, or pass context along. Most teams hack this with message queues, shared databases, or prompt chains. Delega makes it a first-class primitive.

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
| **Webhooks** | 6 lifecycle events, HMAC signatures, delivery logging, auto-disable after failures |
| **Dedup** | Semantic similarity detection via TF-IDF, configurable threshold, `X-Dedup-Check` header |
| **Security** | Optional API key auth (`DELEGA_REQUIRE_AUTH`), rate limiting, configurable CORS |
| **UI** | PWA with dark theme, push notifications, mobile-friendly |
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

### MCP (Claude Desktop, Cursor, etc.)

```bash
npm install -g delega-mcp
```

Add to your MCP client config:

```json
{
  "mcpServers": {
    "delega": {
      "command": "delega-mcp",
      "env": {
        "DELEGA_API_URL": "http://127.0.0.1:18890"
      }
    }
  }
}
```

See [delega-mcp](https://github.com/delega-dev/delega-mcp) for all 11 MCP tools.

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

**Dedup header:** Add `X-Dedup-Check: true` to `POST /api/tasks` to check for similar existing tasks before creating.

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

**Events:** `task.created`, `task.updated`, `task.completed`, `task.deleted`, `task.assigned`, `task.delegated`

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
| `DELEGA_REQUIRE_AUTH` | `false` | Require `X-Agent-Key` on all API routes |
| `DELEGA_CORS_ORIGINS` | `*` | Comma-separated allowed origins |
| `DELEGA_DATABASE_URL` | _(auto)_ | Full SQLAlchemy URL (for Postgres, etc.) |

### Security

By default, Delega runs in **open mode** (no auth required). This is fine for single-user homelab setups behind a reverse proxy.

For production or multi-agent deployments, set `DELEGA_REQUIRE_AUTH=true` and register agents to get API keys:

```bash
curl -X POST http://localhost:18890/api/agents \
  -H "Content-Type: application/json" \
  -d '{"name": "coordinator", "display_name": "Task Coordinator"}'
# Returns: { "api_key": "dlg_...", ... }
```

Then pass the key on every request: `X-Agent-Key: dlg_...`

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
| Free | 1,000 | $0 |
| Pro | 50,000 | $20/mo |
| Scale | 500,000 | $99/mo |
| Usage | Unlimited | $0.001/task |

Same API, same MCP tools. Just point `DELEGA_API_URL` at `https://api.delega.dev`.

## Comparison

| Feature | Delega | AgentTask.io | Todoist API | Linear API |
|---------|--------|-------------|-------------|------------|
| Built for agents | ✅ | ✅ | ❌ | ❌ |
| Agent identity | ✅ | ❌ | ❌ | ❌ |
| Delegation chains | ✅ | ❌ | ❌ | ❌ |
| MCP support | ✅ | ✅ | ❌ | ❌ |
| REST API | ✅ | ❌ | ✅ | ✅ |
| Self-hostable | ✅ | ❌ | ❌ | ❌ |
| Open source | ✅ | ❌ | ❌ | ❌ |
| Webhooks | ✅ | ❌ | ✅ | ✅ |
| Per-task pricing | ✅ | ❌ (per-seat) | ❌ (per-seat) | ❌ (per-seat) |

## Tech Stack

- **Backend**: [FastAPI](https://fastapi.tiangolo.com/) (Python)
- **Database**: [SQLite](https://sqlite.org/) via SQLAlchemy (Postgres supported)
- **Frontend**: Vue.js 3 + Tailwind CSS (PWA)
- **MCP**: [delega-mcp](https://github.com/delega-dev/delega-mcp) (TypeScript)
- **Dedup**: scikit-learn TF-IDF (local, zero API cost)

## Contributing

PRs welcome. See [CONTRIBUTING.md](CONTRIBUTING.md).

## Name

From Latin *delegare*: to entrust, to send as a representative. Task infrastructure should delegate, not just track.

## License

[MIT](LICENSE)
