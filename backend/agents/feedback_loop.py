"""
Feedback Loop — TakeoffAI
Tracks client bid history, agent ELO scores, and win statistics.
"""

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from backend.agents._db import _configure_conn
from backend.config import settings

_profile_locks: dict[str, threading.Lock] = {}
_locks_mu = threading.Lock()


def _profile_lock(client_id: str) -> threading.Lock:
    with _locks_mu:
        if client_id not in _profile_locks:
            _profile_locks[client_id] = threading.Lock()
        return _profile_locks[client_id]


PROFILES_DIR = Path(__file__).parent.parent / "data" / "client_profiles"
DB_PATH = settings.db_path
PROFILES_DIR.mkdir(parents=True, exist_ok=True)

ELO_WIN_DELTA = 32
ELO_LOSE_DELTA = -8
MAX_WINNING_EXAMPLES = 20

ALL_AGENTS = ["conservative", "balanced", "aggressive", "historical_match", "market_beater"]


def _profile_path(client_id: str) -> Path:
    return PROFILES_DIR / f"{client_id}.json"


def _empty_profile(client_id: str) -> dict:
    return {
        "client_id": client_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "winning_examples": [],
        "agent_elo": {agent: 1000 for agent in ALL_AGENTS},
        "stats": {
            "total_tournaments": 0,
            "win_rate_by_agent": {agent: 0.0 for agent in ALL_AGENTS},
            "avg_winning_bid": 0.0,
            "avg_winning_margin": 0.0,
            "wins_by_agent": {agent: 0 for agent in ALL_AGENTS},
        },
    }


def update_client_profile(client_id: str, winner_entry: dict) -> dict:
    """
    Update client profile after a judged tournament.

    - Appends winner to winning_examples (max 20, rotate oldest out)
    - ELO: winning agent +32, all others -8 (floor 0)
    - Recalculates win_rate_by_agent, avg_winning_bid, avg_winning_margin

    Returns the updated profile dict.
    """
    path = _profile_path(client_id)
    with _profile_lock(client_id):
        profile = json.loads(path.read_text()) if path.exists() else _empty_profile(client_id)

        winner_agent = winner_entry.get("agent_name", "")
        total_bid = float(winner_entry.get("total_bid", 0.0))

        # Decode line_items_json — the DB column stores the full estimate as JSON string
        raw_li = winner_entry.get("line_items_json", "{}")
        if isinstance(raw_li, str):
            try:
                estimate_snapshot = json.loads(raw_li)
            except Exception:
                estimate_snapshot = {}
        else:
            estimate_snapshot = raw_li or {}

        # Append winning example
        example = {
            "agent_name": winner_agent,
            "total_bid": total_bid,
            "estimate_snapshot": estimate_snapshot,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        profile["winning_examples"].append(example)
        if len(profile["winning_examples"]) > MAX_WINNING_EXAMPLES:
            profile["winning_examples"] = profile["winning_examples"][-MAX_WINNING_EXAMPLES:]

        # ELO update
        elo = profile.setdefault("agent_elo", {a: 1000 for a in ALL_AGENTS})
        for agent in ALL_AGENTS:
            current = elo.get(agent, 1000)
            if agent == winner_agent:
                elo[agent] = current + ELO_WIN_DELTA
            else:
                elo[agent] = max(0, current + ELO_LOSE_DELTA)

        # Stats update
        stats = profile.setdefault(
            "stats",
            {
                "total_tournaments": 0,
                "win_rate_by_agent": {a: 0.0 for a in ALL_AGENTS},
                "avg_winning_bid": 0.0,
                "avg_winning_margin": 0.0,
                "wins_by_agent": {a: 0 for a in ALL_AGENTS},
            },
        )
        stats["total_tournaments"] = stats.get("total_tournaments", 0) + 1

        wins_by_agent = stats.setdefault("wins_by_agent", {a: 0 for a in ALL_AGENTS})
        wins_by_agent[winner_agent] = wins_by_agent.get(winner_agent, 0) + 1

        total = stats["total_tournaments"]
        stats["win_rate_by_agent"] = {
            agent: round(wins_by_agent.get(agent, 0) / total, 4) for agent in ALL_AGENTS
        }

        # Rolling averages across winning_examples window
        examples = profile["winning_examples"]
        bids = [e["total_bid"] for e in examples if e.get("total_bid")]
        stats["avg_winning_bid"] = round(sum(bids) / len(bids), 2) if bids else 0.0

        margins = [
            float(e["estimate_snapshot"]["margin_pct"])
            for e in examples
            if isinstance(e.get("estimate_snapshot"), dict)
            and e["estimate_snapshot"].get("margin_pct") is not None
        ]
        stats["avg_winning_margin"] = round(sum(margins) / len(margins), 4) if margins else 0.0

        path.write_text(json.dumps(profile, indent=2))
    return profile


def update_client_profile_from_upload(client_id: str, bids: list[dict]) -> dict:
    """
    Bulk-import historical bid records from a CSV, Excel, or manual upload.

    Each bid dict must contain: project_name, zip_code, bid_date, your_bid_amount,
    won (bool). Optional: description, location, trade_type, winning_bid_amount,
    actual_cost, notes.

    Only bids where won=True are added to winning_examples.
    agent_elo is NOT modified (no agent produced these bids).
    Returns the updated profile.
    """
    path = _profile_path(client_id)
    with _profile_lock(client_id):
        profile = json.loads(path.read_text()) if path.exists() else _empty_profile(client_id)

        winning_bids = [b for b in bids if b.get("won")]

        for bid in winning_bids:
            project_name = bid.get("project_name", "")
            description = bid.get("description", "")
            summary = f"{project_name} — {description}".strip(" —") if description else project_name

            example = {
                "agent_name": "upload",
                "total_bid": float(bid.get("your_bid_amount", 0)),
                "estimate_snapshot": {
                    "project_summary": summary,
                    "location": bid.get("location", ""),
                    "trade_type": bid.get("trade_type", "general"),
                    "winning_bid_amount": bid.get("winning_bid_amount"),
                    "actual_cost": bid.get("actual_cost"),
                    "notes": bid.get("notes", ""),
                },
                "timestamp": bid.get("bid_date", datetime.now(timezone.utc).isoformat()),
                "source": "upload",
            }
            profile["winning_examples"].append(example)

        if len(profile["winning_examples"]) > MAX_WINNING_EXAMPLES:
            profile["winning_examples"] = profile["winning_examples"][-MAX_WINNING_EXAMPLES:]

        # Update upload-specific counters (separate from tournament stats)
        upload_stats = profile.setdefault(
            "upload_stats",
            {
                "total_uploaded": 0,
                "total_won_uploaded": 0,
            },
        )
        upload_stats["total_uploaded"] = upload_stats.get("total_uploaded", 0) + len(bids)
        upload_stats["total_won_uploaded"] = upload_stats.get("total_won_uploaded", 0) + len(
            winning_bids
        )

        # Recalculate avg_winning_bid across all winning_examples (tournaments + uploads)
        stats = profile.setdefault(
            "stats",
            {
                "total_tournaments": 0,
                "win_rate_by_agent": {a: 0.0 for a in ALL_AGENTS},
                "avg_winning_bid": 0.0,
                "avg_winning_margin": 0.0,
                "wins_by_agent": {a: 0 for a in ALL_AGENTS},
            },
        )
        all_bids = [e["total_bid"] for e in profile["winning_examples"] if e.get("total_bid")]
        stats["avg_winning_bid"] = round(sum(all_bids) / len(all_bids), 2) if all_bids else 0.0

        path.write_text(json.dumps(profile, indent=2))
    return profile


def load_client_context(client_id: str) -> str:
    """
    Return a formatted string of the top 5 most-recent winning examples
    plus current ELO scores — injected into the historical_match agent's system prompt.
    """
    path = _profile_path(client_id)
    if not path.exists():
        return "(no client history available)"

    profile = json.loads(path.read_text())
    examples = profile.get("winning_examples", [])
    if not examples:
        return "(no winning bids on record yet)"

    recent = examples[-5:]
    lines = ["### Client's 5 Most Recent Winning Bids\n"]
    for i, ex in enumerate(reversed(recent), 1):
        snap = ex.get("estimate_snapshot", {})
        summary = snap.get("project_summary") or snap.get("notes") or "(no summary)"
        lines.append(
            f"**Win {i}** | Agent: {ex.get('agent_name', 'unknown')} | "
            f"Total: ${ex.get('total_bid', 0):,.2f} | Date: {ex.get('timestamp', '')[:10]}"
        )
        lines.append(f"  Project: {summary[:200]}")
        if snap.get("overhead_pct") is not None:
            lines.append(
                f"  Overhead: {snap['overhead_pct']}% | Margin: {snap.get('margin_pct', '?')}% | "
                f"Confidence: {snap.get('confidence', '?')}"
            )
        lines.append("")

    elo = profile.get("agent_elo", {})
    if elo:
        lines.append("### Agent ELO Standings (higher = historically stronger for this client)")
        for agent, score in sorted(elo.items(), key=lambda x: -x[1]):
            lines.append(f"- {agent}: {score}")

    return "\n".join(lines)


# ── Calibration & Accuracy (appended — no existing functions modified) ────────

RED_FLAG_DEVIATION_THRESHOLD = 5.0  # % average deviation to red-flag an agent
RED_FLAG_LOOKBACK = 5  # number of most recent jobs to consider


def _compute_brier_score(
    predictions: list[float],
    actuals: list[int],
) -> Optional[float]:
    """
    Brier Score = (1/N) * sum((f_i - o_i)^2)
    f_i: predicted win probability (0–1)
    o_i: actual outcome (1=won, 0=lost)
    Lower is better; < 0.25 = well-calibrated.
    Returns None if no data.
    """
    if not predictions or len(predictions) != len(actuals):
        return None
    n = len(predictions)
    return round(sum((p - a) ** 2 for p, a in zip(predictions, actuals)) / n, 4)


async def record_actual_outcome(
    client_id: str,
    tournament_id: int,
    actual_cost: float,
    won: bool,
    win_probability: Optional[float] = None,
) -> dict:
    """
    Record actual job outcome and update calibration data.

    - Loads all agent entries for tournament_id from SQLite
    - Computes per-agent deviation: (agent_bid - actual_cost) / actual_cost * 100
    - Appends to calibration.agent_deviation_history (keeps last 5 per agent)
    - Red-flags agents whose last 5 deviations average > RED_FLAG_DEVIATION_THRESHOLD
    - Appends win_probability prediction + actual outcome; recomputes Brier score
    - Writes updated profile to disk; returns updated profile dict
    """
    import aiosqlite

    async with aiosqlite.connect(DB_PATH) as db:
        await _configure_conn(db)
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT agent_name, total_bid FROM tournament_entries WHERE tournament_id = ?",
            (tournament_id,),
        ) as cur:
            entries = [dict(r) for r in await cur.fetchall()]

    if not entries:
        raise ValueError(f"No entries found for tournament {tournament_id}")

    path = _profile_path(client_id)
    with _profile_lock(client_id):
        profile = json.loads(path.read_text()) if path.exists() else _empty_profile(client_id)

        # Initialise calibration block if absent
        cal = profile.setdefault(
            "calibration",
            {
                "win_prob_predictions": [],
                "win_prob_actuals": [],
                "brier_score": None,
                "confidence_accuracy": {},
                "agent_deviation_history": {agent: [] for agent in ALL_AGENTS},
                "red_flagged_agents": [],
            },
        )
        cal.setdefault("agent_deviation_history", {agent: [] for agent in ALL_AGENTS})
        cal.setdefault("red_flagged_agents", [])

        # Compute per-agent deviation
        for entry in entries:
            agent = entry["agent_name"]
            bid = float(entry.get("total_bid") or 0.0)
            if actual_cost > 0 and bid > 0:
                dev = round((bid - actual_cost) / actual_cost * 100, 4)
            else:
                dev = 0.0
            history = cal["agent_deviation_history"].setdefault(agent, [])
            history.append(dev)
            # Keep only last RED_FLAG_LOOKBACK entries
            cal["agent_deviation_history"][agent] = history[-RED_FLAG_LOOKBACK:]

        # Red-flag agents
        red_flagged = set(cal.get("red_flagged_agents", []))
        for agent in ALL_AGENTS:
            history = cal["agent_deviation_history"].get(agent, [])
            if len(history) >= RED_FLAG_LOOKBACK:
                avg_dev = sum(abs(d) for d in history) / len(history)
                if avg_dev > RED_FLAG_DEVIATION_THRESHOLD:
                    red_flagged.add(agent)
                else:
                    red_flagged.discard(agent)
        cal["red_flagged_agents"] = sorted(red_flagged)

        # Win probability calibration
        if win_probability is not None:
            cal["win_prob_predictions"].append(float(win_probability))
            cal["win_prob_actuals"].append(1 if won else 0)
        cal["brier_score"] = _compute_brier_score(
            cal["win_prob_predictions"],
            cal["win_prob_actuals"],
        )

        path.write_text(json.dumps(profile, indent=2))
    return profile


def get_agent_accuracy_report(client_id: str) -> dict:
    """
    Return per-agent accuracy statistics and calibration data for a client.

    Returns dict with:
    - Per-agent: avg_deviation_pct (last 5 jobs), red_flagged bool, deviation_history
    - brier_score: overall win probability calibration score
    - recommended_agent: agent with lowest avg deviation (not red-flagged)
    """
    path = _profile_path(client_id)
    if not path.exists():
        raise ValueError(f"Client profile '{client_id}' not found")

    profile = json.loads(path.read_text())
    cal = profile.get("calibration", {})
    deviation_history = cal.get("agent_deviation_history", {})
    red_flagged = set(cal.get("red_flagged_agents", []))

    report: dict = {}
    for agent in ALL_AGENTS:
        history = deviation_history.get(agent, [])
        recent = history[-RED_FLAG_LOOKBACK:] if history else []
        avg_dev = round(sum(abs(d) for d in recent) / len(recent), 4) if recent else None
        report[agent] = {
            "avg_deviation_pct": avg_dev,
            "deviation_history": recent,
            "red_flagged": agent in red_flagged,
        }

    # Recommended: lowest avg deviation, not red-flagged
    ranked = [
        (a, report[a]["avg_deviation_pct"])
        for a in ALL_AGENTS
        if report[a]["avg_deviation_pct"] is not None and not report[a]["red_flagged"]
    ]
    ranked.sort(key=lambda x: x[1])
    report["recommended_agent"] = ranked[0][0] if ranked else None
    report["brier_score"] = cal.get("brier_score")
    report["win_prob_predictions_count"] = len(cal.get("win_prob_predictions", []))

    return report


def get_accuracy_annotations(client_id: str) -> dict:
    """
    Return per-agent accuracy annotations for injection into tournament responses.

    Safe to call from any context: missing profile, missing calibration block,
    or empty deviation history all return sensible empty defaults — never raises.

    Shape:
    {
      "per_agent": {
        "<agent_name>": {
          "avg_deviation_pct": float | None,   # mean |deviation| over closed jobs (last 5)
          "closed_job_count": int,             # number of recorded deviations
          "is_accuracy_flagged": bool,         # True if avg_deviation > RED_FLAG_DEVIATION_THRESHOLD
        },
        ...
      },
      "recommended_agent": str | None,  # lowest-deviation non-flagged agent, or None
    }
    """
    path = _profile_path(client_id)
    if not path.exists():
        return {"per_agent": {}, "recommended_agent": None}

    try:
        profile = json.loads(path.read_text())
    except Exception:
        return {"per_agent": {}, "recommended_agent": None}

    cal = profile.get("calibration") or {}
    deviation_history = cal.get("agent_deviation_history") or {}
    red_flagged = set(cal.get("red_flagged_agents") or [])

    per_agent: dict = {}
    for agent in ALL_AGENTS:
        # Window to the same RED_FLAG_LOOKBACK the report path uses so the
        # rerank annotation and `/api/verify/accuracy/{client}` report agree
        # on legacy profiles that were written before update_calibration
        # started truncating agent_deviation_history at write time.
        full_history = deviation_history.get(agent) or []
        history = full_history[-RED_FLAG_LOOKBACK:]
        count = len(history)
        if count > 0:
            avg_dev = round(sum(abs(d) for d in history) / count, 4)
        else:
            avg_dev = None
        per_agent[agent] = {
            "avg_deviation_pct": avg_dev,
            "closed_job_count": count,
            "is_accuracy_flagged": agent in red_flagged,
        }

    ranked = [
        (a, per_agent[a]["avg_deviation_pct"])
        for a in ALL_AGENTS
        if per_agent[a]["avg_deviation_pct"] is not None and not per_agent[a]["is_accuracy_flagged"]
    ]
    ranked.sort(key=lambda x: x[1])
    recommended = ranked[0][0] if ranked else None

    return {"per_agent": per_agent, "recommended_agent": recommended}


def exclude_agent(client_id: str, agent_name: str) -> dict:
    """
    Add agent_name to excluded_agents list in client profile.
    Tournament engine skips excluded agents when running for this client.
    Returns updated profile.
    """
    path = _profile_path(client_id)
    with _profile_lock(client_id):
        profile = json.loads(path.read_text()) if path.exists() else _empty_profile(client_id)
        excluded = profile.setdefault("excluded_agents", [])
        if agent_name not in excluded:
            excluded.append(agent_name)
        path.write_text(json.dumps(profile, indent=2))
    return profile


def reset_agent_history(client_id: str, agent_name: str) -> dict:
    """
    Clear deviation history for agent_name and remove from red_flagged_agents.
    Used when an estimator decides to give a flagged agent a clean slate.
    Returns updated calibration block.
    """
    path = _profile_path(client_id)
    with _profile_lock(client_id):
        profile = json.loads(path.read_text()) if path.exists() else _empty_profile(client_id)

        cal = profile.setdefault(
            "calibration",
            {
                "win_prob_predictions": [],
                "win_prob_actuals": [],
                "brier_score": None,
                "confidence_accuracy": {},
                "agent_deviation_history": {a: [] for a in ALL_AGENTS},
                "red_flagged_agents": [],
            },
        )
        cal.setdefault("agent_deviation_history", {})[agent_name] = []
        red_flagged = cal.setdefault("red_flagged_agents", [])
        if agent_name in red_flagged:
            red_flagged.remove(agent_name)

        path.write_text(json.dumps(profile, indent=2))
    return profile.get("calibration", {})
