"""
Feedback Loop — TakeoffAI
Tracks client bid history, agent ELO scores, and win statistics.
"""

import json
from datetime import datetime
from pathlib import Path

PROFILES_DIR = Path(__file__).parent.parent / "data" / "client_profiles"
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
        "created_at": datetime.utcnow().isoformat(),
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
        "timestamp": datetime.utcnow().isoformat(),
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
    stats = profile.setdefault("stats", {
        "total_tournaments": 0,
        "win_rate_by_agent": {a: 0.0 for a in ALL_AGENTS},
        "avg_winning_bid": 0.0,
        "avg_winning_margin": 0.0,
        "wins_by_agent": {a: 0 for a in ALL_AGENTS},
    })
    stats["total_tournaments"] = stats.get("total_tournaments", 0) + 1

    wins_by_agent = stats.setdefault("wins_by_agent", {a: 0 for a in ALL_AGENTS})
    wins_by_agent[winner_agent] = wins_by_agent.get(winner_agent, 0) + 1

    total = stats["total_tournaments"]
    stats["win_rate_by_agent"] = {
        agent: round(wins_by_agent.get(agent, 0) / total, 4)
        for agent in ALL_AGENTS
    }

    # Rolling averages across winning_examples window
    examples = profile["winning_examples"]
    bids = [e["total_bid"] for e in examples if e.get("total_bid")]
    stats["avg_winning_bid"] = round(sum(bids) / len(bids), 2) if bids else 0.0

    margins = [
        float(e["estimate_snapshot"]["margin_pct"])
        for e in examples
        if isinstance(e.get("estimate_snapshot"), dict) and e["estimate_snapshot"].get("margin_pct") is not None
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
            "timestamp": bid.get("bid_date", datetime.utcnow().isoformat()),
            "source": "upload",
        }
        profile["winning_examples"].append(example)

    if len(profile["winning_examples"]) > MAX_WINNING_EXAMPLES:
        profile["winning_examples"] = profile["winning_examples"][-MAX_WINNING_EXAMPLES:]

    # Update upload-specific counters (separate from tournament stats)
    upload_stats = profile.setdefault("upload_stats", {
        "total_uploaded": 0,
        "total_won_uploaded": 0,
    })
    upload_stats["total_uploaded"] = upload_stats.get("total_uploaded", 0) + len(bids)
    upload_stats["total_won_uploaded"] = upload_stats.get("total_won_uploaded", 0) + len(winning_bids)

    # Recalculate avg_winning_bid across all winning_examples (tournaments + uploads)
    stats = profile.setdefault("stats", {
        "total_tournaments": 0,
        "win_rate_by_agent": {a: 0.0 for a in ALL_AGENTS},
        "avg_winning_bid": 0.0,
        "avg_winning_margin": 0.0,
        "wins_by_agent": {a: 0 for a in ALL_AGENTS},
    })
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
