# TakeoffAI Architecture

## Overview

```
TakeoffAI
├── PreBidCalc Agent         ← scope → line-item estimate
├── BidToWin Agent           ← estimate + RFP → bid strategy + proposal
├── Tournament               ← N personalities bid same job in parallel
├── Judge                    ← scores entries, picks winner, cascades outcome
├── FeedbackLoop             ← per-client ELO + win history
├── PriceVerifier            ← audits unit prices against web sources
├── HarnessEvolver           ← agentic loop rewrites losing personalities
├── WikiManager              ← LLM-maintained Obsidian knowledge base
└── HistoricalRetrieval      ← reads closed wiki jobs → injects comparables into Tournament
```

## System Architecture

```
┌─────────────────────────────────────┐
│  Browser (file:// or localhost)     │
│  Static HTML/CSS/JS Frontend        │
│  frontend/dist/index.html           │
│  - Pre-Bid Estimate form            │
│  - Bid Strategy dashboard           │
│  - Import Bid History (CSV/Excel)   │
│  - Tournament runner + results      │
└───────────────┬─────────────────────┘
                │ HTTP/REST → localhost:8000
                ▼
┌─────────────────────────────────────┐
│  FastAPI Backend                    │
│  routes.py      — estimate, bid,   │
│                   tournament, client│
│  upload.py      — CSV/Excel/manual │
│  verification.py— price audit, cal │
│  wiki_routes.py — job CRUD, lint   │
│  validators.py  — shared guards    │
└───────────────┬─────────────────────┘
                │
    ┌───────────┼────────────┬──────────────┐
    ▼           ▼            ▼              ▼
┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────┐
│PreBidCalc│ │ BidToWin │ │ Tournament│ │ WikiManager  │
│  Agent   │ │  Agent   │ │ (5 agents)│ │ (Obsidian)   │
└──────────┘ └──────────┘ └──────┬────┘ └──────────────┘
                                  │
                         ┌────────▼────────┐
                         │     Judge       │
                         │ HUMAN/HIST/AUTO │
                         └────────┬────────┘
                                  │
               ┌──────────────────┼──────────────────┐
               ▼                  ▼                   ▼
      ┌──────────────┐  ┌──────────────────┐  ┌─────────────────┐
      │ FeedbackLoop │  │  PriceVerifier   │  │ HarnessEvolver  │
      │ ELO profiles │  │ web price audit  │  │ agentic rewrite │
      └──────┬───────┘  └───────┬──────────┘  └────────┬────────┘
             │                  │                       │
             ▼                  ▼                       ▼
     client_profiles/     price_audit DB          git commit
     (JSON, per-client)   review_queue            tournament.py
                          material_costs.csv
                                  │
                         Anthropic Claude API
                    (claude-sonnet-4-6 / claude-haiku-4-5)
```

## Agent Details

### PreBidCalc
- **Model:** `claude-sonnet-4-6` (via `settings.claude_model`), max_tokens 8192
- **Input:** project description, zip code, trade type, overhead%, margin%
- **Process:** scope parsing → quantity takeoff → unit cost lookup → CCI adjustment → markup
- **Output:** line-item JSON estimate (`{line_items, subtotal, overhead, margin, total_bid, estimate_low, estimate_high}`)
- **Blueprint mode:** accepts PDF blueprints, extracts scope via Claude vision

### BidToWin
- **Model:** `claude-sonnet-4-6` (via `settings.claude_model`), max_tokens 4096
- **Input:** PreBidCalc estimate, RFP text, project type, known competitors
- **Process:** RFP analysis → scope gap detection → Friedman model → proposal writing
- **Output:** bid scenarios (conservative/balanced/aggressive), win probability, proposal narrative

### Tournament
- **Personalities (5):** `conservative`, `balanced`, `aggressive`, `historical_match`, `market_beater`
- **Grid:** n_agents × 3 temperatures (0.3, 0.7, 1.0) × n_samples repeats
- **Consensus:** median-collapse per personality across all temperature×sample cells
- **Writes:** `bid_tournaments` + `tournament_entries` rows; trace files to `backend/data/traces/`

### Judge
- **Modes:**
  - `HUMAN` — caller names winning agent explicitly
  - `HISTORICAL` — closest entry to `actual_winning_bid` wins
  - `AUTO` — uses client ELO win-rates if ≥20 tournaments; otherwise lowest bid
- **Post-judge triggers:** FeedbackLoop update, background PriceVerifier, HarnessEvolver if dominance detected
- **Wiki cascade (HISTORICAL only):** when `bid_tournaments.wiki_job_slug` is set, fires `cascade_outcome(status="closed", actual_cost=actual_winning_bid)` in the background. This is the write-side that populates the retrieval corpus.

### FeedbackLoop
- **Storage:** `backend/data/client_profiles/{client_id}.json`
- **ELO:** winner +32, each loser −8 (floor 0)
- **Calibration:** Brier score on win probability predictions, per-agent deviation tracking, red-flagging

### PriceVerifier
- **Triggers:** background (post-judge), on-demand (`POST /api/verify/estimate`), nightly batch (scheduler)
- **Process:** fetches supplier sites (Home Depot, Lowe's) + DuckDuckGo; Claude extracts prices from HTML
- **Thresholds:** flag if deviation >5%; sources "agree" if within 10%
- **Auto-update:** updates `material_costs.csv` if ≥3 sources agree

### HarnessEvolver
- **Model:** `claude-sonnet-4-6` (configurable via `HARNESS_EVOLVER_MODEL`)
- **Trigger:** auto when one agent wins >60% of tournaments (min 10); also manual `POST /api/tournament/evolve`
- **Tools:** `list_traces`, `read_file` (sandboxed to `backend/data/`)
- **Process:** reads traces → diagnoses losers → proposes new prompts → rewrites `tournament.py` → `git commit`

### WikiManager
- **Model:** `claude-haiku-4-5` (via `WIKI_MODEL`)
- **Storage:** `wiki/` directory (Obsidian vault, git-tracked)
- **Pages:** jobs, clients, personalities, materials — all with YAML frontmatter
- **Features:** LLM synthesis, fire-and-forget enrichment, lint, cascade on outcome
- **Auto-capture:** every `POST /api/tournament/run` fires `_wiki_capture_tournament` (in `routes.py`), which writes a no-LLM stub via `create_job_stub`, persists the slug onto `bid_tournaments.wiki_job_slug`, then calls `enrich_tournament` to append the narrative section.

### HistoricalRetrieval (`backend/agents/historical_retrieval.py`)
- **Read path:** `wiki/jobs/*.md` filtered by `status ∈ {won, lost, closed}` and matching `trade_type`
- **Scoring:** `10.0` same-client bonus + `5.0` trade match + `2.0` zip3 prefix match + `3.0 × jaccard(query, body)` — deterministic, no LLM
- **Consumer:** `tournament.py` dispatches `get_comparable_jobs()` via `asyncio.to_thread` (sync file I/O must stay off the event loop as the corpus grows) before dispatching the personality grid; `format_comparables_for_prompt()` injects the result into every personality's system prompt.
- **Tenancy (important):** TakeoffAI is **single-tenant by deployment** — one contractor per Docker install. `client_id` is the contractor's *customer* (homeowner, GC, property manager), not a multi-tenant isolation boundary. Retrieval intentionally returns other customers' closed jobs with a `+10` same-customer relevance bump, because pricing a kitchen remodel for customer A should learn from kitchen remodels the same contractor did for customers B, C, D. A hosted multi-contractor `app.takeoffai.ai` deployment would need a hard filter at the *contractor* level — not the customer level.
- **Self-feeding:** the corpus grows every time a tournament is judged in HISTORICAL mode via the WikiManager cascade above.

## Data Layer

### SQLite — `takeoffai.db`

| Table | Purpose |
|---|---|
| `bid_tournaments` | One row per tournament run; `wiki_job_slug` column (migration 5) links to `wiki/jobs/<slug>.md` |
| `tournament_entries` | One row per agent per tournament |
| `price_audit` | Historical price verification results |
| `review_queue` | Flagged line items awaiting review |

Additive migrations live in `backend/api/main.py::_MIGRATIONS`, tracked via `PRAGMA user_version`. Never modify an existing migration — append new `ALTER TABLE` statements.

### File-Based Storage

| Path | Content |
|---|---|
| `backend/data/client_profiles/{id}.json` | Per-client ELO, win stats, examples |
| `backend/data/traces/{tournament_id}/{agent}.json` | Full trace per agent per tournament |
| `backend/data/material_costs.csv` | Unit cost seed data (auto-updated) |
| `wiki/` | Obsidian vault — jobs, clients, personalities, materials |

## Deployment

| Environment | How |
|---|---|
| Dev (your Mac) | `uv run uvicorn backend.api.main:app --reload` |
| Customer Mac | Docker Compose via USB installer |
| Production (Phase 2) | Railway.app / Render.com |
