# TakeoffAI 🏗️

> AI-powered construction pre-bid estimation and bid-winning strategy system.  
> A product by **[answerd.it](https://answerd.it)**

---

## What It Does

**TakeoffAI** gives general contractors and subcontractors two AI agents:

| Agent | Job |
|---|---|
| `PreBidCalc` | Parses a project description → generates a line-item cost estimate with materials, labor, overhead, and margin |
| `BidToWin` | Analyzes an RFP + your estimate → recommends a bid price, win probability, and drafts your proposal narrative |

## Tech Stack

- **Backend:** Python 3.11+, FastAPI, Anthropic Claude API
- **Frontend:** React + Vite + Tailwind CSS
- **Data:** SQLite (local), CSV seed files (RSMeans-style unit costs)
- **Deployment:** Docker Desktop → USB installer for customer Macs
- **Built with:** Claude Code

## Quick Start (Dev)

```bash
# 1. Clone
git clone git@github.com:answerd-it/TakeoffAI.git
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
 │   ├── agents/          # PreBidCalc + BidToWin agent logic
 │   ├── tools/           # material_lookup, labor_rates, rfp_parser
 │   ├── data/            # Seed CSVs (unit costs, labor rates, CCI)
 │   └── api/             # FastAPI routes
 ├── frontend/            # React UI
 ├── installer/           # USB deploy: install.sh + .env.template
 ├── docs/                # Architecture, API reference
 ├── Dockerfile
 ├── docker-compose.yml
 └── .env.template
```

## Roadmap

- [x] Project scaffold
- [ ] PreBidCalc agent — scope parser + line-item estimator
- [ ] BidToWin agent — RFP analyzer + bid strategy + proposal writer
- [ ] Frontend UI — estimate form, bid dashboard, proposal editor
- [ ] Docker packaging
- [ ] USB installer
- [ ] SaaS upgrade (hosted at app.takeoffai.ai)

## Parent Company

Built and maintained by **[answerd.it](https://answerd.it)** — an AI pipeline company that discovers real-world problems and deploys intelligent solutions.

---

*TakeoffAI — Know your number. Win the bid.*
