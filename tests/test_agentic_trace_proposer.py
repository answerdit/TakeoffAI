import asyncio
import json
import pytest
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock


# ── Trace persistence tests ────────────────────────────────────────────────────

def test_trace_files_written_after_tournament(tmp_path, monkeypatch):
    """_save_entries writes one trace file per agent under data/traces/{tournament_id}/."""
    import aiosqlite
    from backend.agents.tournament import _save_entries, AgentResult
    from backend.api.main import _CREATE_TABLES
    import backend.agents.tournament as tm_mod

    traces_root = tmp_path / "data" / "traces"
    monkeypatch.setattr(tm_mod, "TRACES_DIR", traces_root)
    monkeypatch.setattr(tm_mod, "DB_PATH", str(tmp_path / "test.db"))

    results = [
        AgentResult(
            agent_name="aggressive",
            estimate={"total_bid": 100000.0, "line_items": [], "confidence": "high"},
            total_bid=100000.0,
            margin_pct=12.0,
            confidence="high",
        ),
        AgentResult(
            agent_name="conservative",
            estimate={"total_bid": 120000.0, "line_items": [], "confidence": "medium"},
            total_bid=120000.0,
            margin_pct=15.0,
            confidence="medium",
        ),
    ]

    async def run():
        async with aiosqlite.connect(str(tmp_path / "test.db")) as db:
            await db.executescript(_CREATE_TABLES)
            await _save_entries(
                db, 42, results,
                client_id="test_client",
                description="Replace roof on warehouse",
                zip_code="76801",
            )

    asyncio.run(run())

    trace_dir = traces_root / "42"
    assert (trace_dir / "aggressive.json").exists()
    assert (trace_dir / "conservative.json").exists()

    data = json.loads((trace_dir / "aggressive.json").read_text())
    assert data["tournament_id"] == 42
    assert data["client_id"] == "test_client"
    assert data["agent_name"] == "aggressive"
    assert data["project_description"] == "Replace roof on warehouse"
    assert data["zip_code"] == "76801"
    assert data["won"] is False
    assert data["score"] is None
    assert data["estimate"]["total_bid"] == 100000.0
    assert "timestamp" in data


def test_trace_files_not_written_when_no_client_id(tmp_path, monkeypatch):
    """No trace files are written when client_id is None."""
    import aiosqlite
    from backend.agents.tournament import _save_entries, AgentResult
    from backend.api.main import _CREATE_TABLES
    import backend.agents.tournament as tm_mod

    traces_root = tmp_path / "data" / "traces"
    monkeypatch.setattr(tm_mod, "TRACES_DIR", traces_root)
    monkeypatch.setattr(tm_mod, "DB_PATH", str(tmp_path / "test.db"))

    results = [
        AgentResult(
            agent_name="aggressive",
            estimate={"total_bid": 100000.0},
            total_bid=100000.0,
            margin_pct=12.0,
            confidence="high",
        )
    ]

    async def run():
        async with aiosqlite.connect(str(tmp_path / "test.db")) as db:
            await db.executescript(_CREATE_TABLES)
            await _save_entries(db, 1, results)  # no client_id

    asyncio.run(run())
    assert not traces_root.exists()


def test_trace_write_failure_does_not_break_tournament(tmp_path, monkeypatch):
    """If trace file write fails, the tournament entry is still saved to DB."""
    import aiosqlite
    from backend.agents.tournament import _save_entries, AgentResult
    from backend.api.main import _CREATE_TABLES
    import backend.agents.tournament as tm_mod

    monkeypatch.setattr(tm_mod, "DB_PATH", str(tmp_path / "test.db"))

    # Make TRACES_DIR a file (not a dir) so mkdir fails
    bad_path = tmp_path / "not_a_dir"
    bad_path.write_text("not a directory")
    monkeypatch.setattr(tm_mod, "TRACES_DIR", bad_path)

    results = [
        AgentResult(
            agent_name="aggressive",
            estimate={"total_bid": 100000.0},
            total_bid=100000.0,
            margin_pct=12.0,
            confidence="high",
        )
    ]

    async def run():
        async with aiosqlite.connect(str(tmp_path / "test.db")) as db:
            await db.executescript(_CREATE_TABLES)
            await _save_entries(
                db, 1, results,
                client_id="c1", description="test", zip_code="76801",
            )
            cursor = await db.execute("SELECT COUNT(*) FROM tournament_entries")
            row = await cursor.fetchone()
            return row[0]

    count = asyncio.run(run())
    assert count == 1  # DB write succeeded despite trace failure


# ── Tool handler tests ─────────────────────────────────────────────────────────

def test_list_traces_returns_files_for_client(tmp_path):
    """_handle_list_traces returns trace metadata for matching client_id."""
    import backend.agents.harness_evolver as ev

    for tid, agent, cid in [(1, "aggressive", "client_a"), (1, "conservative", "client_a"), (2, "balanced", "client_b")]:
        d = tmp_path / str(tid)
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{agent}.json").write_text(json.dumps({
            "tournament_id": tid,
            "agent_name": agent,
            "client_id": cid,
            "timestamp": "2026-03-31T10:00:00+00:00",
            "estimate": {"total_bid": 100000.0},
        }))

    results = ev._handle_list_traces(tmp_path, client_id="client_a")
    assert len(results) == 2
    agent_names = {r["agent_name"] for r in results}
    assert agent_names == {"aggressive", "conservative"}
    for r in results:
        assert "path" in r
        assert "tournament_id" in r
        assert "total_bid" in r
        assert "timestamp" in r


def test_list_traces_filters_by_agent_name(tmp_path):
    """_handle_list_traces filters to a specific agent when agent_name is given."""
    import backend.agents.harness_evolver as ev

    for agent in ["aggressive", "conservative", "balanced"]:
        d = tmp_path / "5"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{agent}.json").write_text(json.dumps({
            "tournament_id": 5,
            "agent_name": agent,
            "client_id": "c1",
            "timestamp": "2026-03-31T10:00:00+00:00",
            "estimate": {"total_bid": 50000.0},
        }))

    results = ev._handle_list_traces(tmp_path, client_id="c1", agent_name="aggressive")
    assert len(results) == 1
    assert results[0]["agent_name"] == "aggressive"


def test_list_traces_respects_limit(tmp_path):
    """_handle_list_traces returns at most `limit` results."""
    import backend.agents.harness_evolver as ev

    for i in range(10):
        d = tmp_path / str(i)
        d.mkdir(parents=True, exist_ok=True)
        (d / "aggressive.json").write_text(json.dumps({
            "tournament_id": i,
            "agent_name": "aggressive",
            "client_id": "c1",
            "timestamp": "2026-03-31T10:00:00+00:00",
            "estimate": {"total_bid": float(i * 1000)},
        }))

    results = ev._handle_list_traces(tmp_path, client_id="c1", limit=3)
    assert len(results) == 3


def test_read_file_returns_content_within_data_dir(tmp_path):
    """_handle_read_file returns parsed JSON for files inside data_dir."""
    import backend.agents.harness_evolver as ev

    f = tmp_path / "test.json"
    f.write_text(json.dumps({"key": "value"}))

    result = ev._handle_read_file(tmp_path, str(f))
    assert result == {"key": "value"}


def test_read_file_blocks_paths_outside_data_dir(tmp_path):
    """_handle_read_file returns error dict for paths outside data_dir."""
    import backend.agents.harness_evolver as ev

    outside = tmp_path.parent / "secrets.txt"
    outside.write_text("secret")

    result = ev._handle_read_file(tmp_path, str(outside))
    assert "error" in result
    assert "Access denied" in result["error"]


def test_read_file_returns_error_for_missing_file(tmp_path):
    """_handle_read_file returns error dict when file does not exist."""
    import backend.agents.harness_evolver as ev

    result = ev._handle_read_file(tmp_path, str(tmp_path / "nonexistent.json"))
    assert "error" in result
