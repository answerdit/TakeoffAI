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
