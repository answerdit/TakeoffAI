import json
import tempfile
from pathlib import Path

import pytest


def _make_profile(client_id: str, tmp_path: Path) -> Path:
    profile = {
        "client_id": client_id,
        "created_at": "2026-01-01T00:00:00",
        "winning_examples": [],
        "agent_elo": {
            a: 1000
            for a in ["conservative", "balanced", "aggressive", "historical_match", "market_beater"]
        },
        "stats": {
            "total_tournaments": 0,
            "win_rate_by_agent": {},
            "avg_winning_bid": 0.0,
            "avg_winning_margin": 0.0,
            "wins_by_agent": {},
        },
    }
    path = tmp_path / f"{client_id}.json"
    path.write_text(json.dumps(profile))
    return path


def test_compute_brier_score_perfect():
    from backend.agents.feedback_loop import _compute_brier_score

    # Predicted 1.0 for wins, 0.0 for losses → score = 0
    score = _compute_brier_score([1.0, 1.0, 0.0], [1, 1, 0])
    assert score == pytest.approx(0.0)


def test_compute_brier_score_worst():
    from backend.agents.feedback_loop import _compute_brier_score

    # Predicted 0.0 for wins → score = 1.0
    score = _compute_brier_score([0.0, 0.0], [1, 1])
    assert score == pytest.approx(1.0)


def test_compute_brier_score_empty():
    from backend.agents.feedback_loop import _compute_brier_score

    score = _compute_brier_score([], [])
    assert score is None


@pytest.mark.anyio
async def test_record_actual_outcome_updates_calibration(tmp_path, monkeypatch):
    """record_actual_outcome writes deviation history and computes Brier score."""
    import aiosqlite

    # Patch PROFILES_DIR to tmp_path
    import backend.agents.feedback_loop as fl

    monkeypatch.setattr(fl, "PROFILES_DIR", tmp_path)
    profile_path = _make_profile("client1", tmp_path)

    # Create an in-memory SQLite DB with tournament data
    db_path = str(tmp_path / "test.db")
    from backend.api.main import _CREATE_TABLES

    async with aiosqlite.connect(db_path) as db:
        await db.executescript(_CREATE_TABLES)
        await db.execute(
            "INSERT INTO bid_tournaments (id, client_id, project_description, zip_code) VALUES (?,?,?,?)",
            (1, "client1", "40x60 metal building", "76801"),
        )
        for agent, bid in [
            ("conservative", 170000),
            ("balanced", 155000),
            ("aggressive", 140000),
            ("historical_match", 160000),
            ("market_beater", 152000),
        ]:
            await db.execute(
                "INSERT INTO tournament_entries (tournament_id, agent_name, total_bid, won) VALUES (?,?,?,?)",
                (1, agent, bid, 1 if agent == "balanced" else 0),
            )
        await db.commit()

    monkeypatch.setattr(fl, "DB_PATH", db_path)

    result = await fl.record_actual_outcome(
        client_id="client1",
        tournament_id=1,
        actual_cost=150000.0,
        won=True,
        win_probability=0.65,
    )

    assert "calibration" in result
    cal = result["calibration"]
    assert "agent_deviation_history" in cal
    assert "balanced" in cal["agent_deviation_history"]
    # balanced bid 155000, actual 150000 → deviation = (155000-150000)/150000*100 = 3.33%
    assert cal["agent_deviation_history"]["balanced"][-1] == pytest.approx(3.33, abs=0.1)
    assert "brier_score" in cal


def test_record_actual_outcome_red_flags_high_deviation_agent(tmp_path, monkeypatch):
    """Agent with >5% avg deviation over last 5 jobs gets red-flagged."""
    import asyncio

    import aiosqlite

    import backend.agents.feedback_loop as fl

    monkeypatch.setattr(fl, "PROFILES_DIR", tmp_path)

    # Pre-seed profile with 4 existing deviations >5% for 'aggressive'
    profile = json.loads(_make_profile("client2", tmp_path).read_text())
    profile["calibration"] = {
        "win_prob_predictions": [],
        "win_prob_actuals": [],
        "brier_score": None,
        "confidence_accuracy": {},
        "agent_deviation_history": {
            "conservative": [1.0, 1.5, 0.8, 1.2],
            "balanced": [0.5, 1.0, 0.3, 0.8],
            "aggressive": [7.0, 8.0, 6.5, 9.0],  # already 4 high deviations
            "historical_match": [0.5, 0.3, 0.8, 0.2],
            "market_beater": [2.0, 1.5, 1.8, 2.2],
        },
        "red_flagged_agents": [],
    }
    (tmp_path / "client2.json").write_text(json.dumps(profile))

    db_path = str(tmp_path / "test2.db")
    from backend.api.main import _CREATE_TABLES

    async def setup_db():
        async with aiosqlite.connect(db_path) as db:
            await db.executescript(_CREATE_TABLES)
            await db.execute(
                "INSERT INTO bid_tournaments (id, client_id, project_description, zip_code) VALUES (?,?,?,?)",
                (2, "client2", "office buildout", "76801"),
            )
            for agent, bid in [
                ("conservative", 105000),
                ("balanced", 100500),
                ("aggressive", 115000),
                ("historical_match", 101000),
                ("market_beater", 102000),
            ]:
                await db.execute(
                    "INSERT INTO tournament_entries (tournament_id, agent_name, total_bid, won) VALUES (?,?,?,?)",
                    (2, agent, bid, 1 if agent == "balanced" else 0),
                )
            await db.commit()

    asyncio.run(setup_db())
    monkeypatch.setattr(fl, "DB_PATH", db_path)

    # aggressive bid 115000, actual 100000 → deviation = 15% → 5th high deviation
    result = asyncio.run(
        fl.record_actual_outcome(
            client_id="client2",
            tournament_id=2,
            actual_cost=100000.0,
            won=True,
            win_probability=0.70,
        )
    )

    assert "aggressive" in result["calibration"]["red_flagged_agents"]


def test_get_agent_accuracy_report(tmp_path, monkeypatch):
    """get_agent_accuracy_report returns per-agent avg deviation and flag status."""
    import backend.agents.feedback_loop as fl

    monkeypatch.setattr(fl, "PROFILES_DIR", tmp_path)

    profile = json.loads(_make_profile("client3", tmp_path).read_text())
    profile["calibration"] = {
        "win_prob_predictions": [0.7, 0.6],
        "win_prob_actuals": [1, 0],
        "brier_score": 0.185,
        "confidence_accuracy": {},
        "agent_deviation_history": {
            "conservative": [1.0, 2.0, 1.5],
            "balanced": [0.5, 0.3, 0.4],
            "aggressive": [8.0, 7.5, 9.0],
            "historical_match": [0.2, 0.1, 0.3],
            "market_beater": [3.0, 2.5, 2.8],
        },
        "red_flagged_agents": ["aggressive"],
    }
    (tmp_path / "client3.json").write_text(json.dumps(profile))

    report = fl.get_agent_accuracy_report("client3")
    assert report["aggressive"]["avg_deviation_pct"] == pytest.approx(8.167, abs=0.01)
    assert report["aggressive"]["red_flagged"] is True
    assert report["balanced"]["red_flagged"] is False
    assert report["brier_score"] == pytest.approx(0.185)
    assert "recommended_agent" in report


def test_get_accuracy_annotations_missing_profile(tmp_path, monkeypatch):
    """Missing profile → empty dict + None recommended agent, never raises."""
    import backend.agents.feedback_loop as fl

    monkeypatch.setattr(fl, "PROFILES_DIR", tmp_path)
    out = fl.get_accuracy_annotations("nonexistent-client")
    assert out == {"per_agent": {}, "recommended_agent": None}


def test_get_accuracy_annotations_empty_calibration(tmp_path, monkeypatch):
    """Profile exists but no calibration data → every agent annotated with None/0/False."""
    import backend.agents.feedback_loop as fl

    monkeypatch.setattr(fl, "PROFILES_DIR", tmp_path)
    _make_profile("empty-client", tmp_path)

    out = fl.get_accuracy_annotations("empty-client")
    assert out["recommended_agent"] is None
    assert set(out["per_agent"].keys()) == set(fl.ALL_AGENTS)
    for agent in fl.ALL_AGENTS:
        entry = out["per_agent"][agent]
        assert entry["avg_deviation_pct"] is None
        assert entry["closed_job_count"] == 0
        assert entry["is_accuracy_flagged"] is False


def test_get_accuracy_annotations_populated(tmp_path, monkeypatch):
    """Populated deviation history → avg + counts + recommended = lowest non-flagged."""
    import backend.agents.feedback_loop as fl

    monkeypatch.setattr(fl, "PROFILES_DIR", tmp_path)

    profile = json.loads(_make_profile("client-hist", tmp_path).read_text())
    profile["calibration"] = {
        "win_prob_predictions": [],
        "win_prob_actuals": [],
        "brier_score": None,
        "confidence_accuracy": {},
        "agent_deviation_history": {
            "conservative": [2.0, 3.0, 2.5],
            "balanced": [0.5, 0.8, 0.3],
            "aggressive": [10.0, 9.0, 11.0],
            "historical_match": [1.0, 1.2, 0.9],
            "market_beater": [4.0, 3.5, 4.2],
        },
        "red_flagged_agents": ["aggressive"],
    }
    (tmp_path / "client-hist.json").write_text(json.dumps(profile))

    out = fl.get_accuracy_annotations("client-hist")

    per = out["per_agent"]
    assert per["balanced"]["avg_deviation_pct"] == pytest.approx(0.5333, abs=0.01)
    assert per["balanced"]["closed_job_count"] == 3
    assert per["balanced"]["is_accuracy_flagged"] is False
    assert per["aggressive"]["is_accuracy_flagged"] is True

    # Balanced has lowest non-flagged deviation
    assert out["recommended_agent"] == "balanced"


def test_get_accuracy_annotations_all_flagged_returns_none(tmp_path, monkeypatch):
    """When every agent with data is flagged, recommended_agent is None."""
    import backend.agents.feedback_loop as fl

    monkeypatch.setattr(fl, "PROFILES_DIR", tmp_path)
    profile = json.loads(_make_profile("all-bad", tmp_path).read_text())
    profile["calibration"] = {
        "agent_deviation_history": {a: [10.0, 12.0, 15.0] for a in fl.ALL_AGENTS},
        "red_flagged_agents": list(fl.ALL_AGENTS),
    }
    (tmp_path / "all-bad.json").write_text(json.dumps(profile))

    out = fl.get_accuracy_annotations("all-bad")
    assert out["recommended_agent"] is None
    for agent in fl.ALL_AGENTS:
        assert out["per_agent"][agent]["is_accuracy_flagged"] is True


def test_get_accuracy_annotations_corrupt_json(tmp_path, monkeypatch):
    """Corrupt JSON → graceful empty return, never raises."""
    import backend.agents.feedback_loop as fl

    monkeypatch.setattr(fl, "PROFILES_DIR", tmp_path)
    (tmp_path / "broken.json").write_text("{this is not: valid json")

    out = fl.get_accuracy_annotations("broken")
    assert out == {"per_agent": {}, "recommended_agent": None}


def test_annotations_and_report_agree_on_legacy_long_history(tmp_path, monkeypatch):
    """P4 regression: legacy profiles written before update_calibration
    started truncating agent_deviation_history at write time could carry
    >RED_FLAG_LOOKBACK entries. The annotation path (used by /api/tournament/run
    rerank) and the report path (/api/verify/accuracy/{client}) must window
    to the same tail so rerank and report don't disagree on the same profile."""
    import backend.agents.feedback_loop as fl

    monkeypatch.setattr(fl, "PROFILES_DIR", tmp_path)
    profile = json.loads(_make_profile("legacy", tmp_path).read_text())
    # Ancient terrible deviations that predate the truncation fix. If either
    # function averaged over the full history the two would disagree by ~5x.
    profile["calibration"] = {
        "win_prob_predictions": [],
        "win_prob_actuals": [],
        "brier_score": None,
        "confidence_accuracy": {},
        "agent_deviation_history": {
            "conservative": [50.0, 50.0, 50.0, 50.0, 50.0, 1.0, 1.0, 1.0, 1.0, 1.0],
            "balanced": [1.0, 1.0, 1.0, 1.0, 1.0],
            "aggressive": [1.0, 1.0, 1.0, 1.0, 1.0],
            "historical_match": [1.0, 1.0, 1.0, 1.0, 1.0],
            "market_beater": [1.0, 1.0, 1.0, 1.0, 1.0],
        },
        "red_flagged_agents": [],
    }
    (tmp_path / "legacy.json").write_text(json.dumps(profile))

    ann = fl.get_accuracy_annotations("legacy")
    report = fl.get_agent_accuracy_report("legacy")

    for agent in fl.ALL_AGENTS:
        ann_val = ann["per_agent"][agent]["avg_deviation_pct"]
        rep_val = report[agent]["avg_deviation_pct"]
        assert ann_val == rep_val, (
            f"{agent}: annotation={ann_val} but report={rep_val} — paths must agree"
        )

    # And the windowed value for conservative must be 1.0, not the ~25.5
    # you'd get by averaging the full 10-entry history.
    assert ann["per_agent"]["conservative"]["avg_deviation_pct"] == pytest.approx(1.0)
    assert ann["per_agent"]["conservative"]["closed_job_count"] == 10
