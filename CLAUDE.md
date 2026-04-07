# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## Commands

```bash
# Dev server (hot reload)
uv run uvicorn backend.api.main:app --reload

# All tests
uv run pytest

# Single test file
uv run pytest tests/test_wiki_manager.py -v

# Single test
uv run pytest tests/test_wiki_manager.py::test_create_job -v

# Docker (production-like)
docker-compose up --build
```

The frontend is a static single-file app at `frontend/dist/index.html` — open directly in browser; no build step.

---

## Architecture

### Request path

```
frontend/dist/index.html
    → FastAPI (backend/api/main.py)
        → routes.py / wiki_routes.py / verification.py / upload.py
            → backend/agents/*.py   ← all business logic lives here
                → Anthropic API
```

`main.py` owns: lifespan (DB init + migrations), CORS middleware, rate limiting (slowapi), and router registration. All agent logic is strictly in `backend/agents/` — route handlers are thin adapters.

### Key design rules

- **`backend/agents/`** — no HTTP imports; no `Request`/`Response` types. Pure async functions.
- **`backend/api/`** — thin HTTP layer only. Validates input, calls agents, returns responses.
- **`backend/config.py`** — single `settings` singleton (pydantic-settings). Read from `.env`. All modules import `from backend.config import settings`.
- **Rate limiter** — `limiter` is instantiated once in `routes.py` and shared. `wiki_routes.py` imports it from there (`from backend.api.routes import limiter`). Do not instantiate a second `Limiter`.
- **Auth** — `verify_api_key` from `routes.py` is applied as a router-level dependency; all new routers must include it via `app.include_router(..., dependencies=[Depends(verify_api_key)])`.

### Async patterns

- Wiki operations are **fire-and-forget**: `asyncio.ensure_future(...)` is called after the HTTP response is built. They must never block or raise into the caller.
- All `wiki_manager.py` public functions are wrapped in `try/except Exception` for this reason.
- Deferred imports inside fire-and-forget helpers (in `routes.py`) prevent circular imports between `routes.py` ↔ `wiki_manager.py`.

### Database

SQLite at `backend/data/takeoffai.db`. Schema created at startup in `main.py::_CREATE_TABLES`. Additive migrations tracked via `PRAGMA user_version` in `main.py::_MIGRATIONS` — append new `ALTER TABLE` statements to that list; never modify existing ones.

### Wiki system (`wiki/`)

Managed exclusively by `backend/agents/wiki_manager.py`. No other file writes to `wiki/`. The directory is git-tracked and intended to be opened as an Obsidian vault.

- All page writes go through `_write_page(path, meta, body)` — writes YAML frontmatter + markdown body.
- All LLM calls go through `_synthesize(context, instruction)` — uses `WIKI_MODEL` env var (default `claude-haiku-4-5`).
- `SCHEMA.md` (in `wiki/`) is injected into every `_synthesize()` system prompt via `_load_schema()`.
- Cascade on won/lost/closed: job page → client page → personality pages (each step in its own try/except — best-effort).

### Testing

- All async tests use `@pytest.mark.anyio` (not `asyncio`).
- The shared `client` fixture in `conftest.py` spins up the full FastAPI app with DB lifespan. Use it for route tests.
- LLM calls and external HTTP are mocked in tests — see `test_wiki_manager.py` and `test_routes.py` for patterns.

---

## Environment Variables

| Variable | Default | Notes |
|---|---|---|
| `ANTHROPIC_API_KEY` | required | |
| `API_KEY` | — | Required in prod; any value enables auth in dev |
| `APP_ENV` | `development` | Non-dev requires `ALLOWED_ORIGINS` |
| `ALLOWED_ORIGINS` | — | Comma-separated; required when `APP_ENV != development` |
| `WIKI_MODEL` | `claude-haiku-4-5` | Model for all wiki LLM synthesis |
| `DEFAULT_OVERHEAD_PCT` | `20.0` | Applied to estimates |
| `DEFAULT_MARGIN_PCT` | `12.0` | Applied to estimates |

Copy `.env.template` → `.env` and add your key before running.

---

## Adding New Endpoints

1. If the endpoint belongs to a new feature area, create `backend/api/<feature>_routes.py` with an `APIRouter`.
2. Import and register in `main.py`: `app.include_router(new_router, prefix="/api", dependencies=[Depends(verify_api_key)])`.
3. Apply rate limits via the shared `limiter` from `routes.py`.
4. All business logic goes in `backend/agents/` — the route handler should be ≤ 20 lines.
