# Contributing to Delega

Thanks for your interest in contributing. Bug fixes, features, docs, and feedback are all welcome.

## Getting Started

### Prerequisites

- Python 3.10+
- Git
- Node.js 18+ (for MCP development)

### Running Locally

```bash
git clone https://github.com/delega-dev/delega.git
cd delega/backend

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

python main.py
```

API runs at [http://localhost:18890](http://localhost:18890). Interactive docs at `/docs`.

### Running with Docker

```bash
cp .env.example .env
docker compose up --build -d
```

## Project Structure

```
delega/
├── backend/
│   ├── main.py           # FastAPI app (routes, middleware, webhooks)
│   ├── models.py         # SQLAlchemy models (Task, Agent, Webhook, etc.)
│   ├── schemas.py        # Pydantic schemas (validation, serialization)
│   ├── database.py       # Database config
│   ├── dedup.py          # Semantic task deduplication (TF-IDF)
│   └── generate_vapid.py # VAPID key generator for push notifications
├── frontend/
│   ├── index.html        # Vue.js SPA (single file)
│   ├── manifest.json     # PWA manifest
│   ├── sw.js             # Service worker
│   └── assets/           # Icons
├── data/                 # SQLite database (gitignored)
└── docker-compose.yml
```

### Tech Stack

- **Backend**: FastAPI + SQLAlchemy + SQLite/Postgres
- **Frontend**: Vue.js 3 (CDN) + Tailwind CSS
- **MCP**: Separate package at [delega-mcp](https://github.com/delega-dev/delega-mcp)

## Submitting Changes

1. Fork the repo
2. Create a branch (`git checkout -b feature/my-feature`)
3. Make your changes
4. Test that the server starts and API endpoints work
5. Commit with a clear message
6. Open a PR

## Guidelines

- Keep it simple. Readable code beats clever code.
- If you add an API endpoint, update the README.
- The frontend is a single `index.html`. That's intentional.

### Authentication Model (Important)

Delega supports two modes:

- **Auth mode** (default): `DELEGA_REQUIRE_AUTH` is unset or `true`. All `/api/*` routes require a valid `X-Agent-Key`. All user-created agents are admin by default. To sandbox an agent, set `restriction_mode: "restricted"` on creation.
- **Open mode**: `DELEGA_REQUIRE_AUTH=false`. All API endpoints work without an `X-Agent-Key` header. This is a deliberate local-dev opt-out, not the default deployment model.

**Every endpoint must work in both modes.** When adding auth checks:

- Use `require_admin_agent()` / `require_authenticated_agent()` — these already respect `REQUIRE_AUTH` and return `None` (not 401) when auth is not required.
- Never add bare `if not agent: raise 401` checks unless the route must stay closed even in explicit open mode.
- When `agent` is `None` in open mode, treat it as full/admin access (the user controls their own server).
- Preserve the loopback-only first-agent bootstrap path when auth is enabled.
- Test your changes with `DELEGA_REQUIRE_AUTH=true`, `DELEGA_REQUIRE_AUTH=false`, and the default env-unset behavior when the default matters.

## Areas for Contribution

- **Framework integrations**: CrewAI, LangGraph, OpenAI Agents SDK adapters
- **Client libraries**: Python, Go, Rust SDK wrappers
- **Documentation**: Tutorials, deployment guides, integration examples
- **Testing**: Unit tests, integration tests, load testing
- **Features**: Check [open issues](https://github.com/delega-dev/delega/issues)

## Reporting Bugs

Open an issue with: what you expected, what happened, and steps to reproduce.

## License

By contributing, you agree that your contributions will be licensed under the [MIT License](LICENSE).
