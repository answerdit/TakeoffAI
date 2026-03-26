"""
TakeoffAI — API route definitions.
Thin HTTP layer; delegates all logic to agent modules.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.agents.pre_bid_calc import run_prebid_calc
from backend.agents.bid_to_win import run_bid_to_win
from backend.config import settings

router = APIRouter()


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
        result = run_prebid_calc(
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
        result = run_bid_to_win(
            estimate=req.estimate,
            rfp_text=req.rfp_text,
            project_type=req.project_type,
            known_competitors=req.known_competitors,
        )
        return result
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
