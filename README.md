# TakeoffAI

**AI-powered construction pre-bid estimation and bid-winning strategy.**
A product by [answerd.it](https://answerd.it)

---

## What It Does

Construction estimating is slow, inconsistent, and expensive to get wrong. A bid that's too high loses the job. A bid that's too low wins it and kills your margin. TakeoffAI gives contractors a sharper number — faster — by running multiple AI bidding strategies on every job, learning from your win history, and continuously improving its own pricing models over time.

You describe the project. TakeoffAI generates a line-item cost estimate, shows you a confidence range on that number, runs five distinct bidding personalities against it in parallel, and recommends a bid price with a win probability. Every tournament result feeds back into the system. The more you use it, the better it gets at pricing work the way you actually win it.

---

## Core Workflow

**1. Pre-Bid Estimate**

Paste a project description and zip code. TakeoffAI parses it into a line-item breakdown — materials, labor, burden, overhead, margin — anchored to RSMeans-style unit cost data and adjusted for your region. It returns a total bid price along with a confidence band (estimated low and high) so you know how much uncertainty you're carrying before you ever open the RFP.

**2. Bid Strategy**

Paste the RFP scope of work. TakeoffAI analyzes how your estimate fits the project requirements, generates three bid scenarios (conservative, balanced, aggressive), assigns win probabilities to each, and writes a full proposal narrative you can paste directly into your submission.

**3. Tournament Mode**

Five distinct bidding personalities — Conservative, Balanced, Aggressive, Historical Match, and Market Beater — each estimate the same job independently and in parallel. The system runs multiple samples per personality to find stable consensus bids, then shows you the full spread as a confidence band. You see exactly where the personalities agree and where they diverge, so you can make a more informed final call.

**4. Bid History Import**

Upload your historical bids as CSV or Excel. TakeoffAI ingests them into a per-client profile — tracking which personalities have won for you, against what competition, at what margins. This history shapes future tournament results for that client.

**5. Self-Improving Harness**

The HarnessEvolver agent reads tournament trace files, identifies which personalities are underperforming against your actual win history, rewrites their prompts, and commits the changes back to git. The system improves itself without manual tuning.

---

## System Components

| Component | What It Does |
|---|---|
| `PreBidCalc` | Parses a project description into a line-item estimate with materials, labor, overhead, margin, and a low/high confidence band |
| `BidToWin` | Analyzes RFP + estimate, generates bid scenarios with win probabilities, writes a proposal narrative |
| `Tournament` | Runs 5 bidding personalities in parallel with multiple samples each; collapses to consensus bids; computes spread band |
| `Judge` | Scores tournament entries by human pick, historical proximity, or auto ELO; triggers feedback and harness evolution |
| `FeedbackLoop` | Maintains per-client ELO profiles, win rates, and winning bid examples that feed future tournaments |
| `PriceVerifier` | Cross-checks LLM unit prices against current web sources; flags deviations above 5%; updates seed CSVs automatically |
| `HarnessEvolver` | Agentic self-improvement loop: reads traces, diagnoses underperformers, rewrites prompts, commits to git |
| `WikiManager` | LLM-maintained Obsidian knowledge base — auto-creates a job page for every tournament, cascades outcome on judge, feeds `HistoricalRetrieval` |
| `HistoricalRetrieval` | Deterministic similarity lookup over `wiki/jobs/*.md`; injects realized-cost comparables into future tournament prompts |

---

## Tech Stack

- **Backend:** Python 3.11+, FastAPI, Anthropic Claude API (`claude-sonnet-4-6`)
- **Frontend:** Vanilla HTML/CSS/JS — single-file, no build step, ships inside Docker
- **Data:** SQLite, RSMeans-style CSV seed costs, per-client JSON profiles
- **Deployment:** Docker Compose for production; `uv` for local dev; USB installer for customer Macs
- **AI:** Claude Sonnet 4.6 for all agents; rate-limited FastAPI endpoints; slowapi middleware

---

## Quick Start

```bash
# Clone
git clone git@github.com:answerdit/TakeoffAI.git
cd TakeoffAI

# Configure
cp .env.template .env
# Add your Anthropic API key: ANTHROPIC_API_KEY=sk-ant-...
# Optionally set API_KEY= for endpoint authentication

# Install and run (dev)
uv sync
uv run uvicorn backend.api.main:app --reload
# Open frontend/dist/index.html in your browser
```

## Docker

```bash
docker-compose up --build
# App runs at http://localhost:3000
```

---

## API Reference

| Method | Endpoint | Description |
|---|---|---|
| POST | `/api/estimate` | Generate a line-item estimate with confidence band |
| POST | `/api/bid/strategy` | Analyze RFP and generate bid scenarios + proposal |
| POST | `/api/tournament/run` | Run a multi-personality tournament in parallel |
| POST | `/api/tournament/judge` | Score and judge a completed tournament |
| GET | `/api/tournament/{id}` | Retrieve tournament and all entries |
| POST | `/api/tournament/evolve` | Trigger harness evolution manually |
| GET | `/api/client/{id}/profile` | Per-client ELO profile and win statistics |
| POST | `/api/client/{id}/exclude-agent` | Exclude a personality from future tournaments |
| POST | `/api/verify/estimate` | On-demand unit price verification |
| GET | `/api/verify/audit` | Full price audit log |
| GET | `/api/verify/queue` | Flagged price deviations pending review |

---

## Project Structure

```
TakeoffAI/
 ├── backend/
 │   ├── agents/
 │   │   ├── pre_bid_calc.py      # Line-item cost estimator
 │   │   ├── bid_to_win.py        # RFP analyzer + bid strategy + proposal writer
 │   │   ├── tournament.py        # Parallel multi-personality bid runner
 │   │   ├── judge.py             # Tournament scorer (HUMAN / HISTORICAL / AUTO)
 │   │   ├── feedback_loop.py     # Per-client ELO profiles + win statistics
 │   │   ├── price_verifier.py    # Unit price auditor + CSV auto-update
 │   │   └── harness_evolver.py   # Self-improving prompt optimizer
 │   ├── api/
 │   │   ├── main.py              # FastAPI app, middleware, DB init
 │   │   ├── routes.py            # Core endpoints (estimate, bid, tournament)
 │   │   ├── verification.py      # Price verification endpoints
 │   │   └── upload.py            # Bid history import
 │   ├── data/
 │   │   ├── takeoffai.db         # SQLite — tournaments, entries, price audit log
 │   │   ├── material_costs.csv   # RSMeans-style unit cost seed data
 │   │   └── client_profiles/     # Per-client profiles (ELO, win stats, bid examples)
 │   ├── config.py                # pydantic-settings
 │   └── scheduler.py             # Nightly price verification batch job
 ├── frontend/dist/               # Single-page app (no build step)
 ├── installer/                   # USB deploy scripts
 ├── tests/                       # pytest suite (179 tests)
 ├── docs/                        # Architecture and design specs
 ├── Dockerfile
 ├── docker-compose.yml
 └── .env.template
```

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | required | Your Anthropic API key |
| `API_KEY` | — | API key for endpoint authentication (recommended in production) |
| `DEFAULT_OVERHEAD_PCT` | `20.0` | Default overhead applied to estimates |
| `DEFAULT_MARGIN_PCT` | `12.0` | Default margin applied to estimates |
| `APP_ENV` | `development` | Environment tag |
| `API_PORT` | `8000` | FastAPI listen port |

---

## Roadmap

- [x] PreBidCalc — scope parser and line-item estimator with confidence bands
- [x] BidToWin — RFP analysis, bid scenarios, proposal writer
- [x] Tournament system — 5 personalities, parallel execution, consensus collapse
- [x] Judge — HUMAN, HISTORICAL, and AUTO scoring modes
- [x] FeedbackLoop — per-client ELO profiles and win statistics
- [x] PriceVerifier — web-sourced unit price auditing with CSV auto-update
- [x] HarnessEvolver — agentic self-improving prompt optimizer
- [x] WikiManager + HistoricalRetrieval — self-filling corpus of closed jobs that feeds comparables into future tournaments
- [x] Frontend UI — estimate form, bid strategy, tournament tab, bid history import
- [x] Docker packaging and USB installer
- [ ] Hosted version at app.takeoffai.ai
- [ ] Subcontractor bid comparison module
- [ ] Direct integration with Procore and Buildertrend

---

Built and maintained by [answerd.it](https://answerd.it).

*Know your number. Win the bid.*
