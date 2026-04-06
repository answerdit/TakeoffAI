"""
TakeoffAI — API route definitions.
Thin HTTP layer; delegates all logic to agent modules.
"""

import asyncio
import json
import os
import re
from pathlib import Path
from typing import Optional

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Security
from fastapi.security.api_key import APIKeyHeader
from pydantic import BaseModel, Field

from backend.agents.feedback_loop import exclude_agent as _exclude_agent, reset_agent_history as _reset_agent_history
from backend.agents.harness_evolver import evolve_harness as _evolve_harness, _get_lock
from backend.agents.pre_bid_calc import run_prebid_calc
from backend.agents.bid_to_win import run_bid_to_win
from backend.agents.tournament import run_tournament
from backend.agents.judge import judge_tournament
from backend.config import settings

DB_PATH = settings.db_path

# ── API key authentication ────────────────────────────────────────────────────

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def verify_api_key(key: str = Security(_api_key_header)):
    configured_key = os.environ.get("API_KEY", settings.api_key)
    if not configured_key:
        raise HTTPException(status_code=403, detail="API key not configured")
    if key != configured_key:
        raise HTTPException(status_code=403, detail="Invalid or missing API key")


# ── Path traversal guard for client profiles ─────────────────────────────────

_PROFILES_BASE = (Path(__file__).parent.parent / "data" / "client_profiles").resolve()

router = APIRouter(dependencies=[Depends(verify_api_key)])


# ── Request / Response models ────────────────────────────────────────────────

class EstimateRequest(BaseModel):
    description: str = Field(..., min_length=10, description="Plain-English project description")
    zip_code: str = Field(..., min_length=5, max_length=10, description="Project zip code for regional cost index")
    trade_type: str = Field(default="general", description="Primary trade (general, electrical, plumbing, etc.)")
    overhead_pct: float = Field(default=None, ge=0, le=100, description="Overhead % to apply")
    margin_pct: float = Field(default=None, ge=0, le=100, description="Target margin %")

    def resolved_overhead(self) -> float:
        return self.overhead_pct if self.overhead_pct is not None else settings.default_overhead_pct

    def resolved_margin(self) -> float:
        return self.margin_pct if self.margin_pct is not None else settings.default_margin_pct


class BidStrategyRequest(BaseModel):
    estimate: dict = Field(..., description="Estimate JSON from /api/estimate")
    rfp_text: str = Field(..., min_length=20, description="Raw RFP / scope-of-work text")
    project_type: str = Field(default="commercial", description="commercial | residential | government")
    known_competitors: list[str] | None = Field(default=None, description="Known bidders (optional)")


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.post("/estimate")
async def estimate(req: EstimateRequest):
    """Run PreBidCalc agent — returns a line-item cost estimate."""
    try:
        result = await run_prebid_calc(
            description=req.description,
            zip_code=req.zip_code,
            trade_type=req.trade_type,
            overhead_pct=req.resolved_overhead(),
            margin_pct=req.resolved_margin(),
        )
        return result
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/bid/strategy")
async def bid_strategy(req: BidStrategyRequest):
    """Run BidToWin agent — returns bid scenarios and win strategy."""
    try:
        result = await run_bid_to_win(
            estimate=req.estimate,
            rfp_text=req.rfp_text,
            project_type=req.project_type,
            known_competitors=req.known_competitors,
        )
        return result
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ── Tournament endpoints ──────────────────────────────────────────────────────

class TournamentRunRequest(BaseModel):
    description: str = Field(..., min_length=10, description="Plain-English project description")
    zip_code: str = Field(..., min_length=5, max_length=10, description="Project zip code")
    trade_type: str = Field(default="general", description="Primary trade type")
    overhead_pct: float = Field(default=None, ge=0, le=100, description="Overhead %")
    margin_pct: float = Field(default=None, ge=0, le=100, description="Target margin %")
    client_id: Optional[str] = Field(default=None, description="Client ID for profile-aware bidding")
    n_agents: int = Field(default=5, ge=1, le=5, description="Number of agent personalities to run")
    n_samples: int = Field(default=2, ge=1, le=5, description="Samples per personality×temperature cell (1–5)")

    def resolved_overhead(self) -> float:
        return self.overhead_pct if self.overhead_pct is not None else settings.default_overhead_pct

    def resolved_margin(self) -> float:
        return self.margin_pct if self.margin_pct is not None else settings.default_margin_pct


class TournamentJudgeRequest(BaseModel):
    tournament_id: int = Field(..., description="ID of the tournament to judge")
    winner_agent_name: Optional[str] = Field(default=None, description="HUMAN mode: name the winning agent")
    actual_winning_bid: Optional[float] = Field(default=None, ge=0, description="HISTORICAL mode: actual market-winning bid amount")
    human_notes: Optional[str] = Field(default=None, description="Optional free-text notes")


@router.post("/tournament/run")
async def tournament_run(req: TournamentRunRequest):
    """Run a bid tournament — N agents estimate the same project in parallel."""
    try:
        result = await run_tournament(
            description=req.description,
            zip_code=req.zip_code,
            trade_type=req.trade_type,
            overhead_pct=req.resolved_overhead(),
            margin_pct=req.resolved_margin(),
            client_id=req.client_id,
            n_agents=req.n_agents,
            n_samples=req.n_samples,
        )

        def _serialize_entry(e):
            return {
                "agent_name": e.agent_name,
                "total_bid": e.total_bid,
                "margin_pct": e.margin_pct,
                "confidence": e.confidence,
                "temperature": e.temperature,
                "sample_index": e.sample_index,
                "estimate": e.estimate,
                "error": e.error,
            }

        return {
            "tournament_id": result.tournament_id,
            "entries": [_serialize_entry(e) for e in result.entries],
            "consensus_entries": [_serialize_entry(e) for e in result.consensus_entries],
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/tournament/judge")
async def tournament_judge(req: TournamentJudgeRequest):
    """Judge a tournament — determine the winner and trigger feedback loop."""
    try:
        result = await judge_tournament(
            tournament_id=req.tournament_id,
            winner_agent_name=req.winner_agent_name,
            actual_winning_bid=req.actual_winning_bid,
            human_notes=req.human_notes,
        )
        return result
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/tournament/{tournament_id}")
async def tournament_get(tournament_id: int):
    """Retrieve a tournament and all its agent entries."""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM bid_tournaments WHERE id = ?", (tournament_id,)
            ) as cur:
                row = await cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail=f"Tournament {tournament_id} not found")
            tournament = dict(row)

            async with db.execute(
                "SELECT id, agent_name, total_bid, won, score, temperature, is_consensus, created_at FROM tournament_entries WHERE tournament_id = ?",
                (tournament_id,),
            ) as cur:
                entries = [dict(r) for r in await cur.fetchall()]

        return {"tournament": tournament, "entries": entries}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/client/{client_id}/profile")
async def client_profile(client_id: str):
    """Return client profile including ELO scores and win statistics."""
    try:
        if not re.match(r'^[a-zA-Z0-9_\-]+$', client_id):
            raise HTTPException(status_code=400, detail="Invalid client_id format")
        profile_path = (_PROFILES_BASE / f"{client_id}.json").resolve()
        if not str(profile_path).startswith(str(_PROFILES_BASE)):
            raise HTTPException(status_code=400, detail="Invalid client_id")
        if not profile_path.exists():
            raise HTTPException(status_code=404, detail=f"Client profile '{client_id}' not found")
        return json.loads(profile_path.read_text())
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


class ExcludeAgentRequest(BaseModel):
    agent_name: str = Field(..., description="Agent personality to exclude from tournaments")


@router.post("/client/{client_id}/exclude-agent")
async def exclude_agent_endpoint(client_id: str, req: ExcludeAgentRequest):
    """Add an agent to the client's excluded list — it will be skipped in future tournaments."""
    try:
        profile = await asyncio.to_thread(_exclude_agent, client_id, req.agent_name)
        return {"client_id": client_id, "excluded_agents": profile.get("excluded_agents", [])}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.delete("/client/{client_id}/agent-history/{agent_name}")
async def reset_agent_history_endpoint(client_id: str, agent_name: str):
    """Clear deviation history for an agent and remove its red-flag status."""
    try:
        calibration = await asyncio.to_thread(_reset_agent_history, client_id, agent_name)
        return {"client_id": client_id, "agent_name": agent_name, "calibration": calibration}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


class EvolveRequest(BaseModel):
    client_id: str = Field(default="default", description="Client ID to use as diagnostic context")


@router.post("/tournament/evolve")
async def evolve_harness_endpoint(req: EvolveRequest):
    """
    Manually trigger harness evolution. Analyzes client tournament history,
    evolves underperforming agent prompts via Claude, and commits to git.
    Returns 423 if evolution is already in progress.
    """
    if _get_lock().locked():
        raise HTTPException(status_code=423, detail="Evolution already in progress")
    try:
        result = await _evolve_harness(req.client_id)
        if result.get("status") == "locked":
            raise HTTPException(status_code=423, detail="Evolution already in progress")
        return result
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
