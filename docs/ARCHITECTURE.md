# TakeoffAI Architecture

## Overview

```
answerd.it
    └── TakeoffAI
            ├── PreBidCalc Agent   ← scope → line-item estimate
            └── BidToWin Agent     ← estimate + RFP → bid strategy + proposal
```

## System Architecture

```
┌─────────────────────────────────────┐
│  Browser (localhost:3000)           │
│  React Frontend                     │
│  - Project intake form              │
│  - Estimate results view            │
│  - Bid strategy dashboard           │
│  - Proposal draft editor            │
└───────────────┬─────────────────────┘
                │ HTTP/REST
                ▼
┌─────────────────────────────────────┐
│  FastAPI Backend (localhost:8000)   │
│  POST /api/estimate                 │
│  POST /api/bid/strategy             │
│  POST /api/bid/write-proposal       │
└───────────┬─────────────────────────┘
            │
    ┌───────┴────────┐
    ▼                ▼
┌──────────┐  ┌──────────────┐
│PreBidCalc│  │  BidToWin    │
│  Agent   │  │    Agent     │
└────┬─────┘  └──────┬───────┘
     │               │
     └───────┬────────┘
             ▼
    Anthropic Claude API
    (claude-sonnet-4-5)
```

## Agent Details

### PreBidCalc
- Input: project description, zip code, trade type, OH%, margin%
- Process: scope parsing → quantity takeoff → unit cost lookup → CCI adjustment → markup
- Output: line-item JSON estimate

### BidToWin
- Input: PreBidCalc estimate, RFP text, project type, known competitors
- Process: RFP analysis → scope gap detection → Friedman model → proposal writing
- Output: bid scenarios (low/mid/high), win probability, proposal narrative

## Deployment

| Environment | How |
|---|---|
| Dev (your Mac) | `uv run uvicorn backend.api.main:app --reload` |
| Customer Mac | Docker Compose via USB installer |
| Production (Phase 2) | Railway.app / Render.com at `app.takeoffai.ai` |
