"""
Tournament Agent — TakeoffAI
Runs PreBidCalc N times in parallel, each with a different bidding personality.
"""

import asyncio
import json
from dataclasses import dataclass, field
from typing import Optional

import aiosqlite

from backend.agents.pre_bid_calc import run_prebid_calc_with_modifier
from backend.config import settings

DB_PATH = settings.db_path

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
    temperature: float = 0.7
    sample_index: int = 0
    error: Optional[str] = None


@dataclass
class TournamentResult:
    tournament_id: int
    entries: list[AgentResult] = field(default_factory=list)
    consensus_entries: list[AgentResult] = field(default_factory=list)


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
    """Execute one PreBidCalc call directly (async)."""
    try:
        estimate = await run_prebid_calc_with_modifier(
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


def _collapse_to_consensus(results: list[AgentResult]) -> list[AgentResult]:
    """
    Collapse a flat list of AgentResults (from the personality×temperature×sample grid)
    into one consensus AgentResult per personality.

    Strategy: for each personality, take the entry whose total_bid is closest to
    the group median. Entries with errors or zero bids are excluded before collapsing.
    Personalities where all entries are invalid are omitted from the output.
    """
    from collections import defaultdict

    groups: dict[str, list[AgentResult]] = defaultdict(list)
    for r in results:
        if not r.error and r.total_bid > 0:
            groups[r.agent_name].append(r)

    consensus: list[AgentResult] = []
    for name, group in groups.items():
        bids = sorted(r.total_bid for r in group)
        n = len(bids)
        if n == 0:
            continue
        median_bid = bids[n // 2] if n % 2 == 1 else (bids[n // 2 - 1] + bids[n // 2]) / 2
        closest = min(group, key=lambda r: abs(r.total_bid - median_bid))
        consensus.append(closest)

    return consensus


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
) -> None:
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

    tasks = []
    for name in personalities:
        modifier = PERSONALITY_PROMPTS[name]
        if name == "historical_match" and client_context:
            modifier = modifier + f"\n\n{client_context}"
        tasks.append(
            _run_single_agent(
                name, description, zip_code, trade_type, overhead_pct, margin_pct, modifier
            )
        )

    results: list[AgentResult] = list(await asyncio.gather(*tasks))

    # Drop failed / zero-bid entries if at least one valid result remains
    valid_results = [e for e in results if e.total_bid and e.total_bid > 0]
    if valid_results:
        results = valid_results

    async with aiosqlite.connect(DB_PATH) as db:
        tournament_id = await _save_tournament(db, client_id, description, zip_code)
        await _save_entries(db, tournament_id, results)

    return TournamentResult(tournament_id=tournament_id, entries=results)
