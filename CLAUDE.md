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

# Lint / format
uv run black backend tests
uv run isort backend tests
uv run flake8 backend tests

# Docker (production-like)
docker-compose up --build
```

The frontend lives in `frontend/dist/` as three static files — open `index.html` directly in a browser; no build step required.

---

## Architecture

### Request path

```
frontend/dist/index.html  (+ app.css, app.js)
    → FastAPI (backend/api/main.py)
        → routes.py / wiki_routes.py / verification.py / upload.py
            → backend/agents/*.py   ← all business logic lives here
                → Anthropic API
                → gws CLI (Google Workspace, optional)
```

`main.py` owns: lifespan (DB init + migrations), CORS middleware, rate limiting (slowapi), scheduler start/stop, and router registration. All agent logic is strictly in `backend/agents/` — route handlers are thin adapters.

### Key design rules

- **`backend/agents/`** — no HTTP imports; no `Request`/`Response` types. Pure async functions.
- **`backend/api/`** — thin HTTP layer only. Validates input, calls agents, returns responses.
- **`backend/config.py`** — single `settings` singleton (pydantic-settings). Read from `.env`. All modules import `from backend.config import settings`.
- **Rate limiter** — `limiter` is instantiated once in `routes.py` and shared. `wiki_routes.py` imports it from there (`from backend.api.routes import limiter`). Do not instantiate a second `Limiter`.
- **Auth** — `verify_api_key` from `routes.py` is applied as a router-level dependency; all new routers must include it via `app.include_router(..., dependencies=[Depends(verify_api_key)])`.
- **Private modules** — underscore-prefixed modules (e.g. `_wiki_io.py`, `_workspace.py`) are internal implementation details. Callers import from the public facade (`wiki_manager.py`), never from private modules directly.

### Async patterns

- **Fire-and-forget** operations use `asyncio.ensure_future(...)` after the HTTP response is built. They must never block or raise into the caller.
- All `wiki_manager.py` public functions are wrapped in `try/except Exception` for this reason.
- Deferred imports inside fire-and-forget helpers (in `routes.py` and `wiki_routes.py`) prevent circular imports.
- Google Workspace calls follow the same pattern — see `_ws_*` helpers in `routes.py` and `wiki_routes.py`.

### Frontend (`frontend/dist/`)

Three static files — no bundler, no build step:

| File | Lines | Contents |
|---|---|---|
| `index.html` | ~440 | HTML structure, `<link>` and `<script>` references |
| `app.css` | ~680 | All styles (HashiCorp design system variables + component CSS) |
| `app.js` | ~750 | All JavaScript (tab switching, API calls, render functions) |

All three live in the same directory so relative paths (`href="app.css"`, `src="app.js"`) work with both `file://` and HTTP serving. The API base URL is `const API_BASE = 'http://localhost:8000'` at the top of `app.js`.

### Tournament system (`backend/agents/tournament.py`)

The tournament runs a **personality × temperature × sample grid** of parallel LLM calls:

- **5 personalities**: `conservative`, `balanced`, `aggressive`, `historical_match`, `market_beater` — defined as `PERSONALITY_PROMPTS` dict; each is a system-prompt modifier injected into `run_prebid_calc_with_modifier`.
- **3 temperatures**: `[0.3, 0.7, 1.0]` — defined as `TEMPERATURES`.
- **n_samples**: repeats per cell (1–5, default 2). Default grid = 5 × 3 × 2 = **30 parallel API calls**.
- **Consensus**: after gathering, `_collapse_to_consensus()` picks the entry closest to the median bid for each personality. Returns both raw `entries` and collapsed `consensus_entries`.
- Per-client **excluded agents** are loaded from `backend/data/client_profiles/<client_id>.json` before dispatching tasks.

### Feedback loop (`backend/agents/feedback_loop.py`)

Client state lives in `backend/data/client_profiles/<client_id>.json` (not in SQLite). Each profile holds:
- `agent_elo` — ELO scores per personality (start 1000, winner +32, losers -8).
- `stats` — win rates, avg bid, avg margin per agent.
- `winning_examples` — rolling window of last 20 winning estimates (fed to `historical_match` personality).
- `excluded_agents` — agents suppressed for this client.

Profile writes are synchronous (called via `asyncio.to_thread` from async contexts).

### Judge agent (`backend/agents/judge.py`)

Three modes, selected based on what's provided to `POST /api/tournament/judge`:
- **HUMAN** — `winner_agent_name` provided; marks that agent as winner.
- **HISTORICAL** — `actual_winning_bid` provided; winner = entry closest to that amount.
- **AUTO** — neither provided and client has ≥ 20 tournaments; scores by ELO + accuracy.

After judging, triggers `update_client_profile` (feedback loop) and wiki cascade (fire-and-forget). The return dict includes `client_id` so downstream fire-and-forget hooks (e.g. Workspace notification) don't need a second DB lookup.

### Harness Evolver (`backend/agents/harness_evolver.py`)

Self-modifying agent that evolves underperforming `PERSONALITY_PROMPTS` in `tournament.py`:
- Triggers when one agent wins > 60% of tournaments for a client (`DOMINANCE_THRESHOLD`).
- Uses Claude (`HARNESS_EVOLVER_MODEL`, default `claude-sonnet-4-6`) with tool use to navigate trace files and rewrite prompts.
- Surgically replaces the triple-quoted prompt string in `tournament.py` source via regex.
- Protected by `asyncio.Lock` — returns HTTP 423 if already running.
- Triggered manually via `POST /api/tournament/evolve` or automatically post-judge when dominance is detected.

**Dry-run mode** (`POST /api/tournament/evolve` with `"dry_run": true`):
- Runs the full agentic analysis and returns proposed prompt diffs without writing `tournament.py` or committing to git.
- Response: `{"status": "dry_run", "evolved_agents": [...], "proposed_prompts": {...}, "diff": "--- ..."}`.
- Use to preview evolutions before committing. Rollback of live evolutions = `git revert`.

### Nightly price verification (APScheduler)

`backend/scheduler.py` registers a nightly cron at 02:00 (`_run_nightly_verification`) that:
1. Reads all rows from `backend/data/material_costs.csv` (columns: `item`, `unit`, `low_cost`).
2. Calls `verify_line_items()` against web sources for each item.
3. Writes results to the `price_audit` table; flags items deviating from market price.
4. If `GWS_ENABLED=true`, appends flagged rows to the configured Google Sheet.

The same batch logic is exposed as `POST /api/verify/run` for on-demand triggering.

### Database

SQLite at `backend/data/takeoffai.db`. Schema created at startup in `main.py::_CREATE_TABLES`. Additive migrations tracked via `PRAGMA user_version` in `main.py::_MIGRATIONS` — append new `ALTER TABLE` statements to that list; never modify existing ones.

**Tables**: `bid_tournaments`, `tournament_entries`, `price_audit`, `review_queue`.

**Trace files**: `backend/data/traces/<tournament_id>/<agent_name>.json` — written per tournament entry for post-hoc analysis; failures are non-fatal.

### Wiki system (`wiki/`)

Managed exclusively through `backend/agents/wiki_manager.py`. No other file writes to `wiki/`. The directory is git-tracked and intended to be opened as an Obsidian vault.

`wiki_manager.py` is a **thin facade** — it re-exports all public and test-visible symbols from five private sub-modules:

| Sub-module | Responsibility |
|---|---|
| `_wiki_io.py` | Path constants (`JOBS_DIR`, `CLIENTS_DIR`, …), frontmatter parsing, `_write_page`, slug helpers |
| `_wiki_llm.py` | Anthropic client (`_anthropic`), `_synthesize()`, schema cache |
| `_wiki_jobs.py` | Job lifecycle: `create_job`, `enrich_*`, `record_bid_decision`, `cascade_outcome` |
| `_wiki_entities.py` | Client, personality, and material page writers |
| `_wiki_lint.py` | Vault health checks: broken links, orphans, stale jobs, frontmatter validation |

**Key rules:**
- All sub-modules import their siblings via `from backend.agents import _wiki_io as _io` (module-level), never `from backend.agents._wiki_io import JOBS_DIR`. This ensures monkeypatching in tests propagates correctly.
- All LLM calls go through `_synthesize(context, instruction)` in `_wiki_llm.py` — uses `WIKI_MODEL` env var (default `claude-haiku-4-5`).
- `SCHEMA.md` (in `wiki/`) is injected into every `_synthesize()` system prompt via `_load_schema()`.
- Cascade on won/lost/closed: job page → client page → personality pages (each step in its own try/except — best-effort).

### Google Workspace integration (`backend/agents/_workspace.py`)

Optional integration with Google Workspace via the `gws` CLI. All functions are no-ops when `GWS_ENABLED=false` (the default). Enabled per `.env`.

**Prerequisites:**
```bash
brew install googleworkspace-cli
gws auth login -s gmail,calendar,sheets
```

**Hooks wired at lifecycle events:**

| Event | Trigger | Action |
|---|---|---|
| Job created | `wiki_routes.py POST /api/job/create` | Gmail: new prospect notification |
| Bid submitted | `wiki_routes.py POST /api/job/update` (bid-submitted) | Gmail: bid amount notification |
| Won / Lost / Closed | `wiki_routes.py POST /api/job/update` (won/lost/closed) | Gmail: outcome + margin if won |
| Tournament run | `routes.py POST /api/tournament/run` | Sheets: one row per consensus entry |
| Tournament judged | `routes.py POST /api/tournament/judge` | Gmail: winner agent + winning bid |
| Nightly price audit | `scheduler.py` (02:00 UTC) | Sheets: flagged price deviations |

All Workspace calls use `asyncio.ensure_future` (fire-and-forget) with `try/except` wrapping. Failures are logged but never surface to the API caller.

`create_bid_deadline_event()` is available in `_workspace.py` for future use when a due-date field is added to the job schema.

---

## Testing

- All async tests use `@pytest.mark.anyio` (not `asyncio`).
- The shared `client` fixture in `conftest.py` spins up the full FastAPI app with DB lifespan. Use it for route tests.
- LLM calls and external HTTP are mocked in all tests — never hit the real Anthropic API.

### Wiki patching pattern (important)

The wiki sub-modules use module-level attribute access (`_io.JOBS_DIR`), so tests must patch the sub-module directly:

```python
import backend.agents._wiki_io as _io
import backend.agents._wiki_llm as _llm
import backend.agents.wiki_manager as wm

# Unit tests (test_wiki_manager.py) — patch sub-modules only:
monkeypatch.setattr(_io, "JOBS_DIR", tmp_path / "jobs")
monkeypatch.setattr(_llm, "_anthropic", mock_client)

# HTTP-layer tests (test_wiki_routes.py) — MUST patch both:
# wiki_routes.py reads wiki_manager.JOBS_DIR as a module attribute
monkeypatch.setattr(_io, "JOBS_DIR", tmp_path / "jobs")
monkeypatch.setattr(wm, "JOBS_DIR", tmp_path / "jobs")  # ← required for route tests
```

Forgetting the `wm` patch in HTTP-layer tests will cause routes to read/write the real `wiki/` directory.

---

## Environment Variables

| Variable | Default | Notes |
|---|---|---|
| `ANTHROPIC_API_KEY` | required | |
| `API_KEY` | — | Required in prod; any value enables auth in dev |
| `APP_ENV` | `development` | Non-dev requires `ALLOWED_ORIGINS` |
| `ALLOWED_ORIGINS` | — | Comma-separated; required when `APP_ENV != development` |
| `CLAUDE_MODEL` | `claude-sonnet-4-6` | Model for estimate/bid/tournament agents |
| `WIKI_MODEL` | `claude-haiku-4-5` | Model for all wiki LLM synthesis |
| `HARNESS_EVOLVER_MODEL` | `claude-sonnet-4-6` | Model used by harness evolver |
| `HARNESS_EVOLVER_MAX_TOOL_CALLS` | `30` | Max tool calls per evolution run |
| `DEFAULT_OVERHEAD_PCT` | `20.0` | Applied to estimates |
| `DEFAULT_MARGIN_PCT` | `12.0` | Applied to estimates |
| `GWS_ENABLED` | `false` | Enable Google Workspace integration via `gws` CLI |
| `GWS_BIN` | `gws` | Full path to `gws` binary if not on `$PATH` |
| `GWS_NOTIFY_EMAIL` | — | Gmail recipient for job lifecycle notifications |
| `GWS_CALENDAR_ID` | `primary` | Google Calendar for bid deadline events |
| `GWS_TOURNAMENT_SHEET_ID` | — | Google Sheet ID for tournament results log |
| `GWS_PRICE_AUDIT_SHEET_ID` | — | Google Sheet ID for flagged price audit rows |

Copy `.env.template` → `.env` and add your key before running.

---

## Adding New Endpoints

1. If the endpoint belongs to a new feature area, create `backend/api/<feature>_routes.py` with an `APIRouter`.
2. Import and register in `main.py`: `app.include_router(new_router, prefix="/api", dependencies=[Depends(verify_api_key)])`.
3. Apply rate limits via the shared `limiter` from `routes.py`.
4. All business logic goes in `backend/agents/` — the route handler should be ≤ 20 lines.
5. For fire-and-forget side effects (wiki, workspace), add `_ws_*` or `_wiki_*` helper functions and call via `asyncio.ensure_future(...)` after the response is built.
