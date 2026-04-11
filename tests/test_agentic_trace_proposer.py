import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── Trace persistence tests ────────────────────────────────────────────────────


def test_trace_files_written_after_tournament(tmp_path, monkeypatch):
    """_save_entries writes one trace file per agent under data/traces/{tournament_id}/."""
    import aiosqlite

    import backend.agents.tournament as tm_mod
    from backend.agents.tournament import AgentResult, _save_entries
    from backend.api.main import _CREATE_TABLES

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
                db,
                42,
                results,
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

    import backend.agents.tournament as tm_mod
    from backend.agents.tournament import AgentResult, _save_entries
    from backend.api.main import _CREATE_TABLES

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

    import backend.agents.tournament as tm_mod
    from backend.agents.tournament import AgentResult, _save_entries
    from backend.api.main import _CREATE_TABLES

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
                db,
                1,
                results,
                client_id="c1",
                description="test",
                zip_code="76801",
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

    for tid, agent, cid in [
        (1, "aggressive", "client_a"),
        (1, "conservative", "client_a"),
        (2, "balanced", "client_b"),
    ]:
        d = tmp_path / str(tid)
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{agent}.json").write_text(
            json.dumps(
                {
                    "tournament_id": tid,
                    "agent_name": agent,
                    "client_id": cid,
                    "timestamp": "2026-03-31T10:00:00+00:00",
                    "estimate": {"total_bid": 100000.0},
                }
            )
        )

    results = asyncio.run(ev._handle_list_traces(tmp_path, client_id="client_a"))
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
        (d / f"{agent}.json").write_text(
            json.dumps(
                {
                    "tournament_id": 5,
                    "agent_name": agent,
                    "client_id": "c1",
                    "timestamp": "2026-03-31T10:00:00+00:00",
                    "estimate": {"total_bid": 50000.0},
                }
            )
        )

    results = asyncio.run(ev._handle_list_traces(tmp_path, client_id="c1", agent_name="aggressive"))
    assert len(results) == 1
    assert results[0]["agent_name"] == "aggressive"


def test_list_traces_respects_limit(tmp_path):
    """_handle_list_traces returns at most `limit` results."""
    import backend.agents.harness_evolver as ev

    for i in range(10):
        d = tmp_path / str(i)
        d.mkdir(parents=True, exist_ok=True)
        (d / "aggressive.json").write_text(
            json.dumps(
                {
                    "tournament_id": i,
                    "agent_name": "aggressive",
                    "client_id": "c1",
                    "timestamp": "2026-03-31T10:00:00+00:00",
                    "estimate": {"total_bid": float(i * 1000)},
                }
            )
        )

    results = asyncio.run(ev._handle_list_traces(tmp_path, client_id="c1", limit=3))
    assert len(results) == 3


def test_read_file_returns_content_within_data_dir(tmp_path):
    """_handle_read_file returns parsed JSON for files inside data_dir."""
    import backend.agents.harness_evolver as ev

    f = tmp_path / "test.json"
    f.write_text(json.dumps({"key": "value"}))

    result = asyncio.run(ev._handle_read_file(tmp_path, str(f)))
    assert result == {"key": "value"}


def test_read_file_blocks_paths_outside_data_dir(tmp_path):
    """_handle_read_file returns error dict for paths outside data_dir."""
    import backend.agents.harness_evolver as ev

    outside = tmp_path.parent / "secrets.txt"
    outside.write_text("secret")

    result = asyncio.run(ev._handle_read_file(tmp_path, str(outside)))
    assert "error" in result
    assert "Access denied" in result["error"]


def test_read_file_returns_error_for_missing_file(tmp_path):
    """_handle_read_file returns error dict when file does not exist."""
    import backend.agents.harness_evolver as ev

    result = asyncio.run(ev._handle_read_file(tmp_path, str(tmp_path / "nonexistent.json")))
    assert "error" in result


# ── Agentic loop tests ─────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_agentic_proposer_multi_turn_tool_loop(tmp_path, monkeypatch):
    """_run_agentic_proposer processes multi-turn tool calls and returns proposed JSON."""
    import backend.agents.harness_evolver as ev

    # Build mock responses: list_traces → read_file → end_turn with JSON
    list_traces_block = MagicMock()
    list_traces_block.type = "tool_use"
    list_traces_block.id = "tu1"
    list_traces_block.name = "list_traces"
    list_traces_block.input = {"client_id": "c1"}

    read_file_block = MagicMock()
    read_file_block.type = "tool_use"
    read_file_block.id = "tu2"
    read_file_block.name = "read_file"
    read_file_block.input = {"path": str(tmp_path / "1" / "aggressive.json")}

    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = '{"conservative": "## BIDDING PERSONALITY: CONSERVATIVE\\nNEW CONTENT\\n"}'

    r1 = MagicMock(stop_reason="tool_use", content=[list_traces_block])
    r2 = MagicMock(stop_reason="tool_use", content=[read_file_block])
    r3 = MagicMock(stop_reason="end_turn", content=[text_block])

    call_count = [0]

    async def mock_create(**kwargs):
        resp = [r1, r2, r3][min(call_count[0], 2)]
        call_count[0] += 1
        return resp

    async def _mock_list_traces(data_dir, **kw):
        return []

    async def _mock_read_file(data_dir, path):
        return {"agent_name": "aggressive"}

    monkeypatch.setattr(ev, "_handle_list_traces", _mock_list_traces)
    monkeypatch.setattr(ev, "_handle_read_file", _mock_read_file)

    with patch("anthropic.AsyncAnthropic") as mock_cls:
        mock_cls.return_value.messages.create = AsyncMock(side_effect=mock_create)
        result = await ev._run_agentic_proposer(
            data_dir=tmp_path,
            client_id="c1",
            underperforming=["conservative", "balanced"],
            dominant_agent="aggressive",
            dominant_rate=0.72,
            profile_path=tmp_path / "c1.json",
        )

    assert "NEW CONTENT" in result
    assert call_count[0] == 3


@pytest.mark.anyio
async def test_agentic_proposer_soft_cap_forces_proposal(tmp_path, monkeypatch):
    """When tool calls hit MAX_TOOL_CALLS, a forcing message is injected."""
    import backend.agents.harness_evolver as ev

    monkeypatch.setattr(ev, "HARNESS_EVOLVER_MAX_TOOL_CALLS", 2)

    tool_block = MagicMock()
    tool_block.type = "tool_use"
    tool_block.id = "tu"
    tool_block.name = "list_traces"
    tool_block.input = {"client_id": "c1"}

    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = '{"balanced": "## BIDDING PERSONALITY: BALANCED\\nFORCED\\n"}'

    tool_response = MagicMock(stop_reason="tool_use", content=[tool_block])
    final_response = MagicMock(stop_reason="end_turn", content=[text_block])

    call_count = [0]
    captured_messages = []

    async def mock_create(**kwargs):
        captured_messages.append(kwargs.get("messages", []))
        call_count[0] += 1
        if call_count[0] <= 2:
            return tool_response
        return final_response

    async def _mock_list_traces(data_dir, **kw):
        return []

    monkeypatch.setattr(ev, "_handle_list_traces", _mock_list_traces)

    with patch("anthropic.AsyncAnthropic") as mock_cls:
        mock_cls.return_value.messages.create = AsyncMock(side_effect=mock_create)
        result = await ev._run_agentic_proposer(
            data_dir=tmp_path,
            client_id="c1",
            underperforming=["balanced"],
            dominant_agent="aggressive",
            dominant_rate=0.72,
            profile_path=tmp_path / "c1.json",
        )

    assert "FORCED" in result
    # The forcing message must appear in one of the calls
    all_messages = [m for msgs in captured_messages for m in msgs]
    assert any(
        isinstance(m.get("content"), str) and "enough context" in m["content"].lower()
        for m in all_messages
        if isinstance(m, dict)
    )


def test_evolve_harness_uses_agentic_proposer(tmp_path, monkeypatch):
    """evolve_harness calls _run_agentic_proposer and rewrites tournament.py."""
    import shutil

    import backend.agents.feedback_loop as fl
    import backend.agents.harness_evolver as ev

    monkeypatch.setattr(fl, "PROFILES_DIR", tmp_path)
    fake_tourn = tmp_path / "tournament.py"
    shutil.copy(ev.TOURNAMENT_PY, fake_tourn)
    monkeypatch.setattr(ev, "TOURNAMENT_PY", fake_tourn)

    profile = {
        "client_id": "evo_client",
        "winning_examples": [],
        "stats": {
            "total_tournaments": 15,
            "win_rate_by_agent": {
                "conservative": 0.07,
                "balanced": 0.07,
                "aggressive": 0.72,
                "historical_match": 0.07,
                "market_beater": 0.07,
            },
            "avg_winning_bid": 0.0,
            "avg_winning_margin": 0.0,
            "wins_by_agent": {},
        },
    }
    (tmp_path / "evo_client.json").write_text(json.dumps(profile))

    async def _mock_proposer_agentic(**kw):
        return '{"conservative": "## BIDDING PERSONALITY: CONSERVATIVE\\nAGENTIC CONTENT\\n"}'

    monkeypatch.setattr(ev, "_run_agentic_proposer", _mock_proposer_agentic)
    monkeypatch.setattr(ev, "_get_generation_number", lambda: 0)
    with patch("backend.agents.harness_evolver._git_commit") as mock_git:
        mock_git.return_value = "abc1234"
        result = asyncio.run(ev.evolve_harness("evo_client"))

    assert result["status"] == "evolved"
    assert "AGENTIC CONTENT" in fake_tourn.read_text()
