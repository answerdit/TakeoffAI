"""
Tournament Agent — TakeoffAI
Runs PreBidCalc N times in parallel, each with a different bidding personality.
"""

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import aiosqlite

from backend.agents.pre_bid_calc import run_prebid_calc_with_modifier

DB_PATH = str(Path(__file__).parent.parent / "data" / "takeoffai.db")
TRACES_DIR = Path(__file__).parent.parent / "data" / "traces"

# ── Personality system-prompt modifiers ───────────────────────────────────────

PERSONALITY_PROMPTS: dict[str, str] = {
    "conservative": """## BIDDING PERSONALITY: CONSERVATIVE
You are pricing this job to protect margin and avoid cost overruns.
- Include every possible cost line item — never omit anything
- Interpolate unit costs toward the HIGH end of any range
- Add 5–10% contingency buffer to your subtotal before overhead
- Labor burden at 1.55x (maximum end of range)
- Assume worst-case quantities for any ambiguous scope
- Your goal: maximize margin protection, zero risk of underbid""",

    "balanced": """## BIDDING PERSONALITY: BALANCED
You are pricing this job at standard market rates.
- Use midpoint of cost ranges for materials
- Labor burden at 1.45x (typical market)
- Quantities reflect the most likely interpretation of scope
- No extra contingency — rely on the provided overhead percentage
- Your goal: competitive, fair-market estimate that reflects true cost""",

    "aggressive": """## BIDDING PERSONALITY: AGGRESSIVE
You are pricing this job lean to win on price.
- Interpolate unit costs toward the LOW end of any range
- Labor burden at 1.35x (minimum viable)
- Parse scope narrowly — include only items explicitly stated
- Quantities are optimistic (assume efficient crew, no waste)
- Your goal: lowest defensible number that still covers true cost""",

    "historical_match": """## BIDDING PERSONALITY: HISTORICAL MATCH
You are pricing this job to replicate the client's past winning bid style.
- Mirror the overhead and margin percentages that have won before
- Match the level of line-item detail used in their winning bids
- Weight unit costs toward ranges that produced winning numbers historically
- Your goal: produce an estimate indistinguishable from their previous wins""",

    "market_beater": """## BIDDING PERSONALITY: MARKET BEATER
You are pricing this job to sit just below estimated competitor range.
- Price materials at low-to-mid range
- Labor at market-competitive rates (1.40x burden)
- Identify 2–3 line items where a sharp contractor can undercut typical bids
- Do NOT go so low you sacrifice quality signal — stay credible
- Your goal: be the lowest qualified bid, not the absolute floor""",
}


# ── Result types ──────────────────────────────────────────────────────────────

@dataclass
class AgentResult:
    agent_name: str
    estimate: dict
    total_bid: float
    margin_pct: float
    confidence: str
    error: Optional[str] = None


@dataclass
class TournamentResult:
    tournament_id: int
    entries: list[AgentResult] = field(default_factory=list)


# ── Internal helpers ──────────────────────────────────────────────────────────

async def _run_single_agent(
    agent_name: str,
    description: str,
    zip_code: str,
    trade_type: str,
    overhead_pct: float,
    margin_pct: float,
    system_prompt_modifier: str,
) -> AgentResult:
    """Execute one PreBidCalc call in a thread pool (sync → async)."""
    try:
        estimate = await asyncio.to_thread(
            run_prebid_calc_with_modifier,
            description,
            zip_code,
            trade_type,
            overhead_pct,
            margin_pct,
            system_prompt_modifier,
        )
        return AgentResult(
            agent_name=agent_name,
            estimate=estimate,
            total_bid=float(estimate.get("total_bid", 0.0)),
            margin_pct=float(estimate.get("margin_pct", margin_pct)),
            confidence=estimate.get("confidence", "medium"),
        )
    except Exception as exc:
        return AgentResult(
            agent_name=agent_name,
            estimate={},
            total_bid=0.0,
            margin_pct=0.0,
            confidence="low",
            error=str(exc),
        )


async def _save_tournament(
    db: aiosqlite.Connection,
    client_id: Optional[str],
    description: str,
    zip_code: str,
) -> int:
    cursor = await db.execute(
        """INSERT INTO bid_tournaments (client_id, project_description, zip_code, status)
           VALUES (?, ?, ?, 'pending')""",
        (client_id, description, zip_code),
    )
    await db.commit()
    return cursor.lastrowid


async def _save_entries(
    db: aiosqlite.Connection,
    tournament_id: int,
    results: list[AgentResult],
    client_id: Optional[str] = None,
    description: str = "",
    zip_code: str = "",
) -> None:
    import logging
    from datetime import datetime, timezone

    for result in results:
        await db.execute(
            """INSERT INTO tournament_entries
               (tournament_id, agent_name, total_bid, line_items_json, won, score)
               VALUES (?, ?, ?, ?, 0, NULL)""",
            (
                tournament_id,
                result.agent_name,
                result.total_bid,
                json.dumps(result.estimate),
            ),
        )
    await db.commit()

    # Write trace files — best-effort, must not break tournament
    if client_id:
        logger = logging.getLogger(__name__)
        trace_dir = TRACES_DIR / str(tournament_id)
        try:
            trace_dir.mkdir(parents=True, exist_ok=True)
            for result in results:
                trace = {
                    "tournament_id": tournament_id,
                    "agent_name": result.agent_name,
                    "client_id": client_id,
                    "project_description": description,
                    "zip_code": zip_code,
                    "won": False,
                    "score": None,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "estimate": result.estimate,
                }
                (trace_dir / f"{result.agent_name}.json").write_text(
                    json.dumps(trace, indent=2)
                )
        except Exception as exc:
            logger.warning(
                "Failed to write trace files for tournament %s: %s", tournament_id, exc
            )


# ── Public entry point ────────────────────────────────────────────────────────

async def run_tournament(
    description: str,
    zip_code: str,
    trade_type: str = "general",
    overhead_pct: float = 20.0,
    margin_pct: float = 12.0,
    client_id: Optional[str] = None,
    n_agents: int = 5,
) -> TournamentResult:
    """
    Run PreBidCalc N times in parallel with different bidding personalities.

    Returns a TournamentResult containing all AgentResult entries and the
    persisted tournament_id for subsequent judging.
    """
    personalities = list(PERSONALITY_PROMPTS.keys())[:n_agents]

    # Optionally enrich historical_match with client win history
    client_context = ""
    if client_id and "historical_match" in personalities:
        try:
            from backend.agents.feedback_loop import load_client_context
            client_context = await asyncio.to_thread(load_client_context, client_id)
        except Exception:
            pass

    # Load excluded agents for this client
    excluded_agents: list[str] = []
    if client_id:
        from backend.agents.feedback_loop import _profile_path
        _prof_path = _profile_path(client_id)
        if _prof_path.exists():
            import json as _json
            _prof = _json.loads(_prof_path.read_text())
            excluded_agents = _prof.get("excluded_agents", [])

    agents_to_run = [name for name in personalities if name not in excluded_agents]
    tasks = []
    for name in agents_to_run:
        modifier = PERSONALITY_PROMPTS[name]
        if name == "historical_match" and client_context:
            modifier = modifier + f"\n\n{client_context}"
        tasks.append(
            _run_single_agent(
                name, description, zip_code, trade_type, overhead_pct, margin_pct, modifier
            )
        )

    results: list[AgentResult] = list(await asyncio.gather(*tasks))

    async with aiosqlite.connect(DB_PATH) as db:
        tournament_id = await _save_tournament(db, client_id, description, zip_code)
        await _save_entries(db, tournament_id, results, client_id, description, zip_code)

    return TournamentResult(tournament_id=tournament_id, entries=results)
