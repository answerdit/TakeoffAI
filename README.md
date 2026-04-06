# TakeoffAI 🏗️

> AI-powered construction pre-bid estimation and bid-winning strategy system.  
> A product by **[answerd.it](https://answerd.it)**

---

## What It Does

**TakeoffAI** runs multiple AI bidding agents in parallel on each project, judges which strategy would win, and continuously improves itself using market feedback.

| Component | Job |
|---|---|
| `PreBidCalc` | Parses a project description → generates a line-item cost estimate with materials, labor, overhead, and margin |
| `BidToWin` | Analyzes an RFP + your estimate → recommends a bid price, win probability, and drafts your proposal narrative |
| `Tournament` | Runs 5 bidding personalities (conservative, balanced, aggressive, historical_match, market_beater) in parallel on the same job |
| `Judge` | Scores tournament entries by human pick, historical bid proximity, or auto ELO — triggers feedback and harness evolution |
| `FeedbackLoop` | Tracks per-client agent ELO scores, win rates, and winning bid examples; builds a client profile that improves over time |
| `PriceVerifier` | Cross-checks LLM-generated unit prices against web sources; flags deviations >5% and updates seed CSVs |
| `HarnessEvolver` | Agentic Claude loop that reads trace files, diagnoses underperforming personalities, rewrites their prompts, and commits to git |

## Tech Stack

- **Backend:** Python 3.11+, FastAPI, Anthropic Claude API (`claude-sonnet-4-5` / `claude-sonnet-4-6`)
- **Frontend:** React + Vite + Tailwind CSS
- **Data:** SQLite (`takeoffai.db`), CSV seed files (RSMeans-style unit costs), per-client JSON profiles
- **Deployment:** Docker Desktop → USB installer for customer Macs
- **Built with:** Claude Code

## Quick Start (Dev)

```bash
# 1. Clone
git clone git@github.com:answerdit/TakeoffAI.git
cd TakeoffAI

# 2. Set your API key
cp .env.template .env
# Edit .env and add: ANTHROPIC_API_KEY=sk-ant-...

# 3. Install deps
uv sync

# 4. Run
uv run uvicorn backend.api.main:app --reload
# Frontend: cd frontend && npm install && npm run dev
```

## Docker (Production / Customer Deploy)

```bash
docker-compose up --build
# App runs at http://localhost:3000
```

## Project Structure

```
TakeoffAI/
 ├── backend/
 │   ├── agents/
 │   │   ├── pre_bid_calc.py      # Line-item cost estimator
 │   │   ├── bid_to_win.py        # RFP analyzer + bid strategy + proposal writer
 │   │   ├── tournament.py        # Parallel multi-personality bid runner
 │   │   ├── judge.py             # Tournament scorer (HUMAN / HISTORICAL / AUTO modes)
 │   │   ├── feedback_loop.py     # Per-client ELO profiles + win statistics
 │   │   ├── price_verifier.py    # Unit price auditor (web scrape + CSV update)
 │   │   └── harness_evolver.py   # Agentic prompt optimizer (self-modifying harness)
 │   ├── api/
 │   │   ├── main.py              # FastAPI app entry point
 │   │   ├── routes.py            # Core estimate + tournament endpoints
 │   │   ├── verification.py      # Price audit + review queue endpoints
 │   │   └── upload.py            # File upload handling
 │   ├── data/
 │   │   ├── takeoffai.db         # SQLite — tournaments, entries, price_audit, review_queue
 │   │   ├── material_costs.csv   # RSMeans-style unit cost seed data
 │   │   └── client_profiles/     # Per-client JSON profiles (ELO, win stats, examples)
 │   ├── config.py                # pydantic-settings (env vars + .env)
 │   └── scheduler.py             # Nightly price verification batch job
 ├── frontend/                    # React UI
 ├── installer/                   # USB deploy: install.sh + .env.template
 ├── docs/                        # Architecture, design docs
 ├── tests/                       # pytest suite
 ├── Dockerfile
 ├── docker-compose.yml
 └── .env.template
```

## API Endpoints

| Method | Path | Description |
|---|---|---|
| POST | `/api/estimate` | Run PreBidCalc — single line-item estimate |
| POST | `/api/bid/strategy` | Run BidToWin — bid scenarios + proposal |
| POST | `/api/tournament/run` | Run N-agent tournament in parallel |
| POST | `/api/tournament/judge` | Score + judge a completed tournament |
| GET | `/api/tournament/{id}` | Retrieve tournament and entries |
| POST | `/api/tournament/evolve` | Manually trigger harness evolution |
| GET | `/api/client/{id}/profile` | Client ELO profile + win statistics |
| POST | `/api/client/{id}/exclude-agent` | Exclude a personality from future tournaments |
| DELETE | `/api/client/{id}/agent-history/{agent}` | Reset agent deviation history |
| POST | `/api/verify/estimate` | On-demand price verification |
| GET | `/api/verify/audit` | Price audit log |
| GET | `/api/verify/queue` | Review queue (flagged deviations) |

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | *(required)* | Anthropic API key |
| `DEFAULT_OVERHEAD_PCT` | `20.0` | Default overhead % applied to estimates |
| `DEFAULT_MARGIN_PCT` | `12.0` | Default margin % applied to estimates |
| `APP_ENV` | `development` | Environment tag |
| `API_PORT` | `8000` | FastAPI listen port |
| `HARNESS_EVOLVER_MODEL` | `claude-sonnet-4-6` | Model used by harness evolver |
| `HARNESS_EVOLVER_MAX_TOOL_CALLS` | `30` | Max tool calls per evolution run |

## Roadmap

- [x] Project scaffold
- [x] PreBidCalc agent — scope parser + line-item estimator
- [x] BidToWin agent — RFP analyzer + bid strategy + proposal writer
- [x] Tournament system — 5 bidding personalities in parallel
- [x] Judge — HUMAN / HISTORICAL / AUTO scoring modes
- [x] FeedbackLoop — per-client ELO profiles + win statistics
- [x] PriceVerifier — web-sourced unit price auditing + CSV auto-update
- [x] HarnessEvolver — agentic self-improving prompt optimizer
- [ ] Frontend UI — tournament dashboard, estimate form, proposal editor
- [ ] Docker packaging
- [ ] USB installer
- [ ] SaaS upgrade (hosted at app.takeoffai.ai)

## Parent Company

Built and maintained by **[answerd.it](https://answerd.it)** — an AI pipeline company that discovers real-world problems and deploys intelligent solutions.

---

*TakeoffAI — Know your number. Win the bid.*
