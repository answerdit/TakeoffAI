"""
Judge Agent — TakeoffAI
Scores tournament entries and determines the winner.
Three modes: HUMAN (explicit pick), HISTORICAL (closest to actual winning bid), AUTO (ELO/stats).
"""

import asyncio
import json
from enum import Enum
from pathlib import Path
from typing import Optional

import aiosqlite

from backend.agents.price_verifier import verify_line_items

DB_PATH = str(Path(__file__).parent.parent / "data" / "takeoffai.db")
AUTO_MODE_MIN_TOURNAMENTS = 20


class JudgeMode(str, Enum):
    HUMAN = "human"
    HISTORICAL = "historical"
    AUTO = "auto"


def _load_profile_sync(client_id: str) -> dict:
    """Load client profile synchronously (called via asyncio.to_thread)."""
    from backend.agents.feedback_loop import _profile_path
    path = _profile_path(client_id)
    if path.exists():
        return json.loads(path.read_text())
    return {}


def _score_by_proximity(entries: list[dict], actual_winning_bid: float) -> tuple[int, dict[int, float]]:
    """
    Score each entry by proximity to actual_winning_bid.
    Winner = closest; scores normalized 0–100 across the entry range.
    Returns (winner_id, scores_dict).
    """
    distances = {e["id"]: abs((e["total_bid"] or 0.0) - actual_winning_bid) for e in entries}
    winner_id = min(distances, key=distances.get)

    min_d = min(distances.values())
    max_d = max(distances.values())
    span = max_d - min_d or 1.0

    scores = {
        eid: round(100.0 * (1.0 - (d - min_d) / span), 2)
        for eid, d in distances.items()
    }
    return winner_id, scores


def _score_by_win_rate(entries: list[dict], win_rates: dict[str, float]) -> tuple[int, dict[int, float]]:
    """
    Score each entry using historical win-rate for its agent personality.
    Winner = highest historical win rate.
    Returns (winner_id, scores_dict).
    """
    agent_scores = {e["id"]: win_rates.get(e["agent_name"], 0.0) * 100 for e in entries}
    winner_id = max(agent_scores, key=agent_scores.get)
    return winner_id, agent_scores


async def judge_tournament(
    tournament_id: int,
    winner_agent_name: Optional[str] = None,
    actual_winning_bid: Optional[float] = None,
    human_notes: Optional[str] = None,
) -> dict:
    """
    Score and judge a completed tournament.

    Mode selection:
    - HUMAN      — winner_agent_name provided → that agent wins
    - HISTORICAL — actual_winning_bid provided → closest entry wins
    - AUTO       — client has ≥20 tournaments → win_rate stats decide

    Updates tournament_entries (won, score) and bid_tournaments (status).
    Triggers feedback_loop.update_client_profile for the winning entry.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        async with db.execute(
            "SELECT * FROM bid_tournaments WHERE id = ?", (tournament_id,)
        ) as cur:
            tournament = await cur.fetchone()
        if not tournament:
            raise ValueError(f"Tournament {tournament_id} not found")

        async with db.execute(
            "SELECT * FROM tournament_entries WHERE tournament_id = ?", (tournament_id,)
        ) as cur:
            rows = await cur.fetchall()

        if not rows:
            raise ValueError(f"No entries for tournament {tournament_id}")

        entries = [dict(r) for r in rows]
        client_id = tournament["client_id"]

        # ── Determine mode ────────────────────────────────────────────────────
        if winner_agent_name is not None:
            mode = JudgeMode.HUMAN
        elif actual_winning_bid is not None:
            mode = JudgeMode.HISTORICAL
        else:
            mode = JudgeMode.AUTO

        winner_id: Optional[int] = None
        scores: dict[int, float] = {}

        if mode == JudgeMode.HUMAN:
            matched = [e for e in entries if e["agent_name"] == winner_agent_name]
            if not matched:
                raise ValueError(f"Agent '{winner_agent_name}' not found in tournament {tournament_id}")
            winner_id = matched[0]["id"]
            scores = {e["id"]: (100.0 if e["id"] == winner_id else 0.0) for e in entries}

        elif mode == JudgeMode.HISTORICAL:
            winner_id, scores = _score_by_proximity(entries, actual_winning_bid)

        elif mode == JudgeMode.AUTO:
            profile: dict = {}
            if client_id:
                profile = await asyncio.to_thread(_load_profile_sync, client_id)

            total_tournaments = profile.get("stats", {}).get("total_tournaments", 0)
            win_rates = profile.get("stats", {}).get("win_rate_by_agent", {})

            if total_tournaments >= AUTO_MODE_MIN_TOURNAMENTS and any(win_rates.values()):
                winner_id, scores = _score_by_win_rate(entries, win_rates)
            else:
                # Fallback: lowest bid is most likely to win on price
                best = min(entries, key=lambda e: e["total_bid"] or float("inf"))
                winner_id = best["id"]
                scores = {e["id"]: (100.0 if e["id"] == winner_id else 50.0) for e in entries}

        # ── Persist results ───────────────────────────────────────────────────
        for entry in entries:
            await db.execute(
                "UPDATE tournament_entries SET won = ?, score = ? WHERE id = ?",
                (1 if entry["id"] == winner_id else 0, scores.get(entry["id"], 0.0), entry["id"]),
            )
        await db.execute(
            "UPDATE bid_tournaments SET status = 'judged' WHERE id = ?",
            (tournament_id,),
        )
        await db.commit()

        # Reload winner for feedback loop (need fresh row with updated won/score)
        winner_entry = next((e for e in entries if e["id"] == winner_id), None)
        if winner_entry:
            winner_entry = dict(winner_entry)
            winner_entry["won"] = 1
            winner_entry["score"] = scores.get(winner_id, 0.0)

    # ── Trigger feedback loop outside DB context ──────────────────────────────
    if client_id and winner_entry:
        from backend.agents.feedback_loop import update_client_profile
        await asyncio.to_thread(update_client_profile, client_id, winner_entry)

    # ── Auto-evolve harness if one agent is dominating ────────────────────────
    if client_id and winner_entry:
        from backend.agents.harness_evolver import check_dominance, evolve_harness
        if check_dominance(client_id):
            asyncio.create_task(evolve_harness(client_id))

    # ── Background price verification of winning estimate ─────────────────────
    if winner_entry:
        raw_estimate = winner_entry.get("line_items_json", "{}")
        try:
            estimate = json.loads(raw_estimate) if isinstance(raw_estimate, str) else raw_estimate
        except Exception:
            estimate = {}
        line_items = estimate.get("line_items", [])
        if line_items:
            asyncio.create_task(
                verify_line_items(
                    line_items=line_items,
                    triggered_by="background",
                    tournament_id=tournament_id,
                )
            )

    return {
        "tournament_id": tournament_id,
        "mode": mode,
        "winner_agent": winner_entry["agent_name"] if winner_entry else None,
        "winner_total_bid": winner_entry["total_bid"] if winner_entry else None,
        "scores": {
            next(e["agent_name"] for e in entries if e["id"] == eid): score
            for eid, score in scores.items()
        },
        "human_notes": human_notes,
    }
