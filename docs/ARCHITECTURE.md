# TakeoffAI Architecture

## Overview

```
answerd.it
    └── TakeoffAI
            ├── PreBidCalc Agent      ← scope → line-item estimate
            ├── BidToWin Agent        ← estimate + RFP → bid strategy + proposal
            ├── Tournament            ← N personalities bid same job in parallel
            ├── Judge                 ← scores entries, picks winner
            ├── FeedbackLoop          ← per-client ELO + win history
            ├── PriceVerifier         ← audits unit prices against web sources
            └── HarnessEvolver        ← agentic loop rewrites losing personalities
```

## System Architecture

```
┌─────────────────────────────────────┐
│  Browser (localhost:3000)           │
│  React Frontend                     │
│  - Project intake form              │
│  - Tournament results view          │
│  - Bid strategy dashboard           │
│  - Proposal draft editor            │
└───────────────┬─────────────────────┘
                │ HTTP/REST
                ▼
┌─────────────────────────────────────┐
│  FastAPI Backend (localhost:8000)   │
│  routes.py    — estimate, tournament│
│  verification.py — price audit      │
└───────────────┬─────────────────────┘
                │
    ┌───────────┼────────────┐
    ▼           ▼            ▼
┌──────────┐ ┌──────────┐ ┌──────────────┐
│PreBidCalc│ │ BidToWin │ │  Tournament  │
│  Agent   │ │  Agent   │ │  (5 agents)  │
└──────────┘ └──────────┘ └──────┬───────┘
                                  │
                         ┌────────▼────────┐
                         │     Judge       │
                         │  HUMAN/HIST/AUTO│
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
                    (claude-sonnet-4-5 / claude-sonnet-4-6)
```

## Agent Details

### PreBidCalc
- **Model:** `claude-sonnet-4-5`, max_tokens 8192
- **Input:** project description, zip code, trade type, overhead%, margin%
- **Process:** scope parsing → quantity takeoff → unit cost lookup → CCI adjustment → markup
- **Output:** line-item JSON estimate (`{line_items, subtotal, overhead, margin, total_bid}`)

### BidToWin
- **Input:** PreBidCalc estimate, RFP text, project type, known competitors
- **Process:** RFP analysis → scope gap detection → Friedman model → proposal writing
- **Output:** bid scenarios (low/mid/high), win probability, proposal narrative

### Tournament
- **Personalities (5):** `conservative`, `balanced`, `aggressive`, `historical_match`, `market_beater`
- **Process:** runs PreBidCalc in parallel with each personality's system-prompt modifier
- **Writes:** `bid_tournaments` + `tournament_entries` rows; per-agent JSON trace files to `backend/data/traces/{tournament_id}/`
- **Config:** `n_agents` 1–5, optional `client_id` for profile-aware bidding

### Judge
- **Modes:**
  - `HUMAN` — caller names winning agent explicitly
  - `HISTORICAL` — closest entry to `actual_winning_bid` wins (proximity scoring)
  - `AUTO` — uses client ELO win-rates if ≥20 tournaments; otherwise lowest bid
- **Post-judge triggers:** FeedbackLoop update, background PriceVerifier, HarnessEvolver if dominance detected

### FeedbackLoop
- **Storage:** `backend/data/client_profiles/{client_id}.json`
- **ELO:** winner +32, each loser −8 (floor 0)
- **Stats:** `win_rate_by_agent`, `avg_winning_bid`, `avg_winning_margin`, `wins_by_agent`
- **History:** keeps last 20 winning estimate examples per client
- **Additional methods:** `record_actual_outcome`, `get_agent_accuracy_report`, `exclude_agent`, `reset_agent_history`

### PriceVerifier
- **Triggers:** background (post-judge), on-demand (`POST /api/verify/estimate`), nightly batch (scheduler)
- **Process:** scrapes supplier sites for unit prices, compares to LLM-generated values
- **Thresholds:** flag if deviation >5%; sources "agree" if within 10% of each other
- **Auto-update:** updates `material_costs.csv` if ≥3 sources agree on a new price
- **DB tables:** `price_audit`, `review_queue`

### HarnessEvolver
- **Model:** `claude-sonnet-4-6` (configurable via `HARNESS_EVOLVER_MODEL`)
- **Trigger:** auto-triggered by Judge when one agent wins >60% of a client's tournaments (min 10 played); also callable via `POST /api/tournament/evolve`
- **Tools available to Claude:** `list_traces`, `read_file` (sandboxed to `backend/data/`)
- **Max tool calls:** 30 (configurable via `HARNESS_EVOLVER_MAX_TOOL_CALLS`)
- **Process:** agentic loop reads trace files and client profile to diagnose losers → proposes new personality prompts → surgically rewrites `tournament.py` → `git commit`
- **Concurrency:** asyncio lock prevents simultaneous evolutions
- **Rollback:** native `git revert` on any evolution commit

## Data Layer

### SQLite — `takeoffai.db`

| Table | Purpose |
|---|---|
| `bid_tournaments` | One row per tournament run (client_id, status, timestamps) |
| `tournament_entries` | One row per agent per tournament (total_bid, won, score, line_items_json) |
| `price_audit` | Historical price verification results |
| `review_queue` | Flagged line items awaiting manual review |

### File-Based Storage

| Path | Content |
|---|---|
| `backend/data/client_profiles/{id}.json` | Per-client ELO, win stats, winning examples |
| `backend/data/traces/{tournament_id}/{agent}.json` | Full estimate trace per agent per tournament |
| `backend/data/material_costs.csv` | RSMeans-style unit cost seed data (auto-updated by PriceVerifier) |

## Deployment

| Environment | How |
|---|---|
| Dev (your Mac) | `uv run uvicorn backend.api.main:app --reload` |
| Customer Mac | Docker Compose via USB installer |
| Production (Phase 2) | Railway.app / Render.com at `app.takeoffai.ai` |
