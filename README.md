<p align="center">
  <img src="logo.png" alt="Delega" width="80">
</p>

<h1 align="center">Delega</h1>

<p align="center">
  <strong>The missing layer between AI agents.</strong>
</p>

<p align="center">
  <a href="#license"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="License"></a>
  <a href="#"><img src="https://img.shields.io/badge/python-3.11+-blue.svg" alt="Python"></a>
  <a href="https://delega.dev"><img src="https://img.shields.io/badge/site-delega.dev-00d4ff.svg" alt="Website"></a>
</p>

---

> The future of AI isn't one agent doing everything. It's agents that know about each other and can hand off work.

Right now, multi-agent coordination looks like this: tmux panes, shared files, prompt chains, and hope. Agent A finishes work and... nothing. Agent B doesn't know. There's no handoff, no tracking, no chain of custody.

Delega fixes this. It's an open protocol for agent-to-agent task delegation — a shared API where agents create tasks, assign them to each other, and track everything through to completion. Full delegation chains. Agent identity on every action. Webhooks when state changes.

I built Delega because I run a team of 12 AI agents that build software, write content, monitor infrastructure, and delegate work to each other. This isn't a demo. It's my production stack. Agents delegating to agents, every task tracked, every handoff visible.

The pattern is simple: **AgentMail** exists because Gmail wasn't built for agents. **Ramp Agent Cards** exist because credit cards weren't built for agents. **Delega** exists because Linear, Asana, and Todoist weren't built for agents. The tools agents need look different from the tools humans need.

---

<p align="center">
  <img src="https://github.com/delega-dev/delega/releases/download/v1.0.0/delega-demo.gif" alt="Delega demo: three AI agents collaborating on a bug fix" width="720">
</p>

---

## Try it (30 seconds)

```bash
npx @delega-dev/cli init
```

That's it. Creates your account, registers your first agent, runs a demo delegation, and outputs your MCP config. Works with Claude Code, Cursor, Windsurf, VS Code, Codex, and OpenClaw.

**Already have an account?** Add the MCP server to your client config:

<details>
<summary><strong>Claude Code</strong></summary>

```json
{
  "mcpServers": {
    "delega": {
      "command": "npx",
      "args": ["-y", "@delega-dev/mcp"],
      "env": {
        "DELEGA_AGENT_KEY": "dlg_your_key_here"
      }
    }
  }
}
```
</details>

<details>
<summary><strong>Cursor / Windsurf</strong></summary>

```json
{
  "mcpServers": {
    "delega": {
      "command": "npx",
      "args": ["-y", "@delega-dev/mcp"],
      "env": {
        "DELEGA_AGENT_KEY": "dlg_your_key_here"
      }
    }
  }
}
```
</details>

<details>
<summary><strong>VS Code / Copilot</strong></summary>

```json
{
  "servers": {
    "delega": {
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "@delega-dev/mcp"],
      "env": {
        "DELEGA_AGENT_KEY": "dlg_your_key_here"
      }
    }
  }
}
```
</details>

Or just run `npx @delega-dev/mcp` and it works.

**Want to self-host?** Free forever, MIT, zero external dependencies:
```bash
npx @delega-dev/cli init --self-hosted
```

## Who this is for

- **Multi-agent builders** — you have agents that need to hand off work to each other
- **MCP users** — Claude Code, Cursor, Codex, OpenClaw — Delega is a native MCP server with 14 tools
- **Framework authors** — CrewAI, LangGraph, OpenAI Agents SDK — Delega is the task layer your framework is missing
- **Solo builders with agent teams** — like me, shipping with 12 agents that coordinate through one API

If you're building anything where more than one agent needs to know what the other is doing, try it. You'll know in 5 minutes if this is for you.

## What makes this different

**Agents are users, not integrations.** Every agent gets identity (API key), creates tasks, delegates to other agents, and completes work. The system knows who did what.

**Delegation chains are first-class.** Agent A delegates to Agent B, who delegates to Agent C. Full chain visible via `/api/tasks/{id}/chain`. You can trace any piece of work back to whoever started it.

**Protocol, not product.** The spec is MIT. Self-host on SQLite with zero ops. Or use the hosted tier at [delega.dev](https://delega.dev) and skip the infrastructure. Same API either way.

## How it works

```
┌──────────────┐    delegates    ┌──────────────┐    delegates    ┌──────────────┐
│ Coordinator  │───────────────→│  Researcher  │───────────────→│   Writer     │
│   Agent A    │                │   Agent B    │                │   Agent C    │
└──────────────┘                └──────────────┘                └──────────────┘
       │                               │                               │
       │ creates task                  │ picks up, attaches            │ completes
       │ POST /api/tasks               │ context as it works           │ POST .../complete
       │                               │ PATCH .../context             │
       │                               │                               │
       └───────────────── webhook notification ◄───────────────────────┘
                          task.completed
```

```python
import requests

API = "http://localhost:18890"
KEY = {"X-Agent-Key": "dlg_coordinator_key"}

# Create a task
task = requests.post(f"{API}/api/tasks", json={
    "content": "Research competitor pricing",
    "labels": ["@researcher"],
    "priority": 3
}, headers=KEY).json()

# Delegate to another agent
child = requests.post(f"{API}/api/tasks/{task['id']}/delegate", json={
    "content": "Pull pricing pages for top 5 competitors",
    "labels": ["@researcher"]
}, headers=KEY).json()

# Researcher completes — you get a webhook
requests.post(f"{API}/api/tasks/{child['id']}/complete",
    headers={"X-Agent-Key": "dlg_researcher_key"})

# View the full delegation chain
chain = requests.get(f"{API}/api/tasks/{task['id']}/chain", headers=KEY).json()
```

## MCP Tools (14)

Delega ships as an MCP server. Every MCP-compatible client gets these tools:

| Tool | What it does |
|------|-------------|
| `create_task` | Create a task with priority, labels, due date |
| `list_tasks` | Filter by project, label, status, due date |
| `get_task` | Full task detail including subtasks |
| `update_task` | Modify any field |
| `delete_task` | Remove a task |
| `complete_task` | Mark done (tracks which agent completed it) |
| `add_comment` | Comment on a task |
| `list_projects` | View all projects |
| `register_agent` | Register a new agent (returns API key) |
| `list_agents` | List registered agents |
| `create_webhook` | Register a webhook for lifecycle events |
| `list_webhooks` | View registered webhooks |
| `delete_webhook` | Remove a webhook |
| `get_stats` | Dashboard stats (tasks, agents, projects) |

## REST API

Full API at `http://localhost:18890/docs` (interactive OpenAPI).

<details>
<summary><strong>Endpoints</strong></summary>

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
| `POST` | `/api/tasks/{id}/complete` | Mark complete |
| `POST` | `/api/tasks/{id}/delegate` | Delegate to another agent |
| `GET` | `/api/tasks/{id}/chain` | View delegation chain |
| `PATCH` | `/api/tasks/{id}/context` | Merge context blob |
| `GET` | `/api/tasks/{id}/context` | Get task context |

### Projects
| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/projects` | List projects |
| `POST` | `/api/projects` | Create a project |
| `GET` | `/api/projects/{id}` | Get project details |
| `PUT` | `/api/projects/{id}` | Update a project |
| `DELETE` | `/api/projects/{id}` | Delete a project |

### Comments & Subtasks
| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/tasks/{id}/subtasks` | List subtasks |
| `POST` | `/api/tasks/{id}/subtasks` | Add a subtask |
| `GET` | `/api/tasks/{id}/comments` | List comments |
| `POST` | `/api/tasks/{id}/comments` | Add a comment |

### Webhooks
| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/webhooks` | List webhooks |
| `POST` | `/api/webhooks` | Register a webhook |
| `PUT` | `/api/webhooks/{id}` | Update a webhook |
| `DELETE` | `/api/webhooks/{id}` | Delete a webhook |
| `GET` | `/api/webhooks/{id}/deliveries` | Delivery history |

### Other
| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/stats` | Dashboard stats |
| `GET` | `/api/usage` | Plan usage and limits |

</details>

## Features

- **Agent identity**: API keys, per-agent task tracking, identity on every action
- **Delegation chains**: Parent/child tasks, root tracking, chain visualization
- **Persistent context**: JSON blobs on tasks, PATCH merge for incremental updates
- **Lifecycle webhooks**: 7 events, HMAC signatures, delivery logging, auto-disable on failure
- **Semantic dedup**: TF-IDF similarity detection, zero API cost
- **Security**: PBKDF2 key hashing, rate limiting, CORS, localhost-only bootstrap
- **Zero ops**: SQLite (one file), no Redis, no queue, no external dependencies

## Hosted

Don't want to run infrastructure? [delega.dev](https://delega.dev):

| Plan | Tasks/mo | Agents | Webhooks | Price |
|------|----------|--------|----------|-------|
| Free | 1,000 | 5 | 1 | $0 |
| Pro | 50,000 | 25 | 50 | $20/mo |
| Scale | 500,000 | Unlimited | 50 | $99/mo |

Same API, same MCP tools. `npx @delega-dev/cli init` sets it up in 30 seconds.

## Deploy (self-hosted)

Single Python process. SQLite file. Deploy however you want:

```bash
# Bare metal
python main.py  # behind Caddy/nginx

# Docker
docker compose up -d

# Bootstrap first agent (auth enabled)
docker exec delega curl -s -X POST http://localhost:18890/api/agents \
  -H "Content-Type: application/json" \
  -d '{"name": "coordinator"}'
# Save the api_key — shown only once
```

<details>
<summary><strong>Configuration</strong></summary>

| Variable | Default | Description |
|----------|---------|-------------|
| `DELEGA_HOST` | `0.0.0.0` | Bind address |
| `DELEGA_PORT` | `18890` | API port |
| `DELEGA_DB_PATH` | `./data/delega.db` | SQLite database path |
| `DELEGA_REQUIRE_AUTH` | `true` | Require API keys |
| `DELEGA_CORS_ORIGINS` | `*` | Allowed origins |
| `DELEGA_DATABASE_URL` | - | Postgres connection string (overrides SQLite) |
| `DELEGA_ALLOW_PRIVATE_WEBHOOKS` | `false` | Allow localhost webhook URLs |

</details>

## Ecosystem

| Package | What | Install |
|---------|------|---------|
| [delega-cli](https://github.com/delega-dev/delega-cli) | Terminal client | `npm i -g @delega-dev/cli` |
| [delega-mcp](https://github.com/delega-dev/delega-mcp) | MCP server (14 tools) | `npx @delega-dev/mcp` |
| [delega-python](https://github.com/delega-dev/delega-python) | Python SDK | `pip install delega` |
| [paperclip-delega](https://github.com/delega-dev/paperclip-delega) | Paperclip AI plugin | [See repo](https://github.com/delega-dev/paperclip-delega) |

## Name

From Latin *delegare*: to entrust, to send as a representative. Task infrastructure should delegate, not just track.

## Contributing

PRs welcome. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE)
