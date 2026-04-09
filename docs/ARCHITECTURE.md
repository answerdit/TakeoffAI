# TakeoffAI Architecture

## Overview

```
TakeoffAI
├── PreBidCalc Agent      ← scope → line-item estimate
├── BidToWin Agent        ← estimate + RFP → bid strategy + proposal
├── Tournament            ← N personalities bid same job in parallel
├── Judge                 ← scores entries, picks winner
├── FeedbackLoop          ← per-client ELO + win history
├── PriceVerifier         ← audits unit prices against web sources
├── HarnessEvolver        ← agentic loop rewrites losing personalities
└── WikiManager           ← LLM-maintained Obsidian knowledge base
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

## Data Layer

### SQLite — `takeoffai.db`

| Table | Purpose |
|---|---|
| `bid_tournaments` | One row per tournament run |
| `tournament_entries` | One row per agent per tournament |
| `price_audit` | Historical price verification results |
| `review_queue` | Flagged line items awaiting review |

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
