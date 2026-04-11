"""
Tournament Agent — TakeoffAI
Runs PreBidCalc across a personality × temperature × sample grid in parallel,
collapsing results to a median-consensus entry per personality.
"""

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import aiosqlite

from backend.agents._db import _configure_conn
from backend.agents.historical_retrieval import format_comparables_for_prompt, get_comparable_jobs
from backend.agents.pre_bid_calc import run_prebid_calc_with_modifier
from backend.config import settings

DB_PATH = settings.db_path
TRACES_DIR = Path(__file__).parent.parent / "data" / "traces"

# Limit concurrent Anthropic API calls to avoid rate limit 429s.
# 10 is safe for Anthropic Tier-1 (50 RPM limit shared across all requests).
_LLM_SEM = asyncio.Semaphore(10)

# ── Personality system-prompt modifiers ───────────────────────────────────────

TEMPERATURES: list[float] = [0.3, 0.7, 1.0]

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
    accuracy_annotations: dict = field(default_factory=dict)
    accuracy_recommended_agent: Optional[str] = None
    rerank_active: bool = False


# ── Internal helpers ──────────────────────────────────────────────────────────


async def _run_single_agent(
    agent_name: str,
    description: str,
    zip_code: str,
    trade_type: str,
    overhead_pct: float,
    margin_pct: float,
    system_prompt_modifier: str,
    temperature: float = 0.7,
    sample_index: int = 0,
    historical_comparables: str = "",
) -> AgentResult:
    """Execute one PreBidCalc call directly (async)."""
    async with _LLM_SEM:
        try:
            estimate = await run_prebid_calc_with_modifier(
                description,
                zip_code,
                trade_type,
                overhead_pct,
                margin_pct,
                system_prompt_modifier,
                historical_comparables=historical_comparables or None,
                temperature=temperature,
            )
            return AgentResult(
                agent_name=agent_name,
                estimate=estimate,
                total_bid=float(estimate.get("total_bid", 0.0)),
                margin_pct=float(estimate.get("margin_pct", margin_pct)),
                confidence=estimate.get("confidence", "medium"),
                temperature=temperature,
                sample_index=sample_index,
            )
        except Exception as exc:
            return AgentResult(
                agent_name=agent_name,
                estimate={},
                total_bid=0.0,
                margin_pct=0.0,
                confidence="low",
                temperature=temperature,
                sample_index=sample_index,
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


def _maybe_rerank_by_accuracy(
    consensus: list[AgentResult],
    annotations: dict,
    recommended_agent: Optional[str],
) -> tuple[list[AgentResult], bool]:
    """
    Optionally re-rank consensus entries by historical accuracy (hybrid rollout phase 2).

    Guarded by settings.tournament_accuracy_rerank_enabled. Only kicks in when the
    recommended agent has at least `tournament_accuracy_rerank_min_jobs` closed jobs.

    Returns (consensus, rerank_active). rerank_active is True only when all gates
    passed and the order was actually sorted — the frontend uses it to badge the
    result as "sorted by accuracy" vs "default order".

    Sort order (stable within each group):
      1. Non-flagged agents with data, ascending by avg_deviation_pct
      2. Agents with no deviation data
      3. Red-flagged agents
    """
    if not settings.tournament_accuracy_rerank_enabled:
        return consensus, False
    if not recommended_agent or not annotations:
        return consensus, False

    rec_ann = annotations.get(recommended_agent) or {}
    if rec_ann.get("closed_job_count", 0) < settings.tournament_accuracy_rerank_min_jobs:
        return consensus, False

    def sort_key(entry_with_index):
        idx, entry = entry_with_index
        ann = annotations.get(entry.agent_name) or {}
        flagged = bool(ann.get("is_accuracy_flagged"))
        dev = ann.get("avg_deviation_pct")
        # Tier: 0 = non-flagged with data, 1 = no data, 2 = flagged
        if flagged:
            tier = 2
        elif dev is None:
            tier = 1
        else:
            tier = 0
        # Sort by tier, then deviation (None → 0 placeholder; only used when tier>0), then original idx for stability
        return (tier, dev if dev is not None else 0.0, idx)

    indexed = list(enumerate(consensus))
    indexed.sort(key=sort_key)
    return [e for _, e in indexed], True


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
    consensus_keys: set[tuple] = frozenset(),
    client_id: Optional[str] = None,
    description: str = "",
    zip_code: str = "",
) -> None:
    import logging
    from datetime import datetime, timezone

    for result in results:
        is_consensus = (
            1
            if (result.agent_name, result.total_bid, result.temperature, result.sample_index)
            in consensus_keys
            else 0
        )
        await db.execute(
            """INSERT INTO tournament_entries
               (tournament_id, agent_name, total_bid, line_items_json, won, score, temperature, is_consensus)
               VALUES (?, ?, ?, ?, 0, NULL, ?, ?)""",
            (
                tournament_id,
                result.agent_name,
                result.total_bid,
                json.dumps(result.estimate),
                result.temperature,
                is_consensus,
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
                (trace_dir / f"{result.agent_name}.json").write_text(json.dumps(trace, indent=2))
        except Exception as exc:
            logger.warning("Failed to write trace files for tournament %s: %s", tournament_id, exc)


# ── Public entry point ────────────────────────────────────────────────────────


async def run_tournament(
    description: str,
    zip_code: str,
    trade_type: str = "general",
    overhead_pct: float = 20.0,
    margin_pct: float = 12.0,
    client_id: Optional[str] = None,
    n_agents: int = 5,
    n_samples: int = 2,
) -> TournamentResult:
    """
    Run PreBidCalc across a personality × temperature × sample grid in parallel.

    Grid: n_agents personalities × 3 temperature tiers × n_samples repeats.
    Default (n_agents=5, n_samples=2): 30 parallel API calls.

    Returns a TournamentResult with:
    - entries: all raw results from the grid
    - consensus_entries: one median-collapsed entry per personality
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

    historical_comparables_block = ""
    if client_id:
        try:
            comparables = get_comparable_jobs(
                client_id=client_id,
                trade_type=trade_type,
                description=description,
                zip_code=zip_code,
                limit=5,
            )
            historical_comparables_block = format_comparables_for_prompt(comparables)
        except Exception:
            import logging

            logging.exception("historical retrieval failed (non-fatal)")
            historical_comparables_block = ""

    # Build coroutines grouped by personality so we can fire one warmup per
    # personality first (to populate the Anthropic prompt cache) before
    # releasing the rest in parallel.
    from collections import defaultdict

    tasks_by_personality: dict[str, list] = defaultdict(list)
    for name in agents_to_run:
        modifier = PERSONALITY_PROMPTS[name]
        if name == "historical_match" and client_context:
            modifier = modifier + f"\n\n{client_context}"
        for temp in TEMPERATURES:
            for sample_idx in range(n_samples):
                tasks_by_personality[name].append(
                    _run_single_agent(
                        name,
                        description,
                        zip_code,
                        trade_type,
                        overhead_pct,
                        margin_pct,
                        modifier,
                        temperature=temp,
                        sample_index=sample_idx,
                        historical_comparables=historical_comparables_block,
                    )
                )

    # Fire one warmup per personality serially (populates prompt cache).
    warmup_results: list[AgentResult] = []
    for _group in tasks_by_personality.values():
        warmup_results.append(await _group[0])

    # Fire remaining tasks in parallel; return_exceptions prevents one 429
    # from cancelling all other in-flight tasks.
    remaining = [t for _group in tasks_by_personality.values() for t in _group[1:]]
    rest_raw = list(await asyncio.gather(*remaining, return_exceptions=True))
    rest_results: list[AgentResult] = [
        (
            r
            if isinstance(r, AgentResult)
            else AgentResult(
                agent_name="unknown",
                estimate={},
                total_bid=0.0,
                margin_pct=0.0,
                confidence="low",
                error=str(r),
            )
        )
        for r in rest_raw
    ]

    results: list[AgentResult] = warmup_results + rest_results

    consensus = _collapse_to_consensus(results)

    accuracy_annotations: dict = {}
    accuracy_recommended_agent: Optional[str] = None
    if client_id:
        try:
            from backend.agents.feedback_loop import get_accuracy_annotations

            annotations = await asyncio.to_thread(get_accuracy_annotations, client_id)
            accuracy_annotations = annotations.get("per_agent") or {}
            accuracy_recommended_agent = annotations.get("recommended_agent")
        except Exception:
            import logging

            logging.exception("accuracy annotation load failed (non-fatal)")

    consensus, rerank_active = _maybe_rerank_by_accuracy(
        consensus, accuracy_annotations, accuracy_recommended_agent
    )

    # Build a key set for marking consensus entries in the DB
    consensus_keys = {(e.agent_name, e.total_bid, e.temperature, e.sample_index) for e in consensus}

    async with aiosqlite.connect(DB_PATH) as db:
        await _configure_conn(db)
        tournament_id = await _save_tournament(db, client_id, description, zip_code)
        await _save_entries(
            db, tournament_id, results, consensus_keys, client_id, description, zip_code
        )

    return TournamentResult(
        tournament_id=tournament_id,
        entries=results,
        consensus_entries=consensus,
        accuracy_annotations=accuracy_annotations,
        accuracy_recommended_agent=accuracy_recommended_agent,
        rerank_active=rerank_active,
    )
