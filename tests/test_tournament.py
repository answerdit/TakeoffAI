"""Tests for tournament engine — data structures, collapse logic, grid expansion."""

import pytest
from unittest.mock import AsyncMock, patch
from backend.agents.tournament import AgentResult, TournamentResult, _collapse_to_consensus


def make_agent_result(name, total_bid, temperature=0.7, sample_index=0, error=None):
    return AgentResult(
        agent_name=name,
        estimate={"total_bid": total_bid},
        total_bid=total_bid,
        margin_pct=12.0,
        confidence="medium",
        temperature=temperature,
        sample_index=sample_index,
        error=error,
    )


def test_agent_result_has_temperature_field():
    r = make_agent_result("conservative", 100_000.0, temperature=0.3)
    assert r.temperature == 0.3


def test_agent_result_has_sample_index_field():
    r = make_agent_result("conservative", 100_000.0, sample_index=1)
    assert r.sample_index == 1


def test_tournament_result_has_consensus_entries():
    tr = TournamentResult(
        tournament_id=1,
        entries=[],
        consensus_entries=[],
    )
    assert tr.consensus_entries == []


def test_collapse_picks_median_entry():
    """Should return the entry closest to the median total_bid per personality."""
    entries = [
        make_agent_result("conservative", 90_000.0, temperature=0.3, sample_index=0),
        make_agent_result("conservative", 100_000.0, temperature=0.7, sample_index=0),
        make_agent_result("conservative", 110_000.0, temperature=1.0, sample_index=0),
    ]
    result = _collapse_to_consensus(entries)
    assert len(result) == 1
    assert result[0].agent_name == "conservative"
    assert result[0].total_bid == 100_000.0  # closest to median of [90k, 100k, 110k]


def test_collapse_handles_multiple_personalities():
    entries = [
        make_agent_result("conservative", 100_000.0),
        make_agent_result("conservative", 110_000.0),
        make_agent_result("aggressive", 80_000.0),
        make_agent_result("aggressive", 85_000.0),
    ]
    result = _collapse_to_consensus(entries)
    assert len(result) == 2
    names = {r.agent_name for r in result}
    assert names == {"conservative", "aggressive"}


def test_collapse_drops_errored_entries():
    entries = [
        make_agent_result("conservative", 0.0, error="API error"),
        make_agent_result("conservative", 100_000.0),
        make_agent_result("conservative", 110_000.0),
    ]
    result = _collapse_to_consensus(entries)
    assert len(result) == 1
    assert result[0].total_bid > 0


def test_collapse_drops_zero_bid_entries():
    entries = [
        make_agent_result("balanced", 0.0),
        make_agent_result("balanced", 95_000.0),
        make_agent_result("balanced", 105_000.0),
    ]
    result = _collapse_to_consensus(entries)
    assert result[0].total_bid > 0


def test_collapse_personality_with_all_errors_excluded():
    """A personality group where all entries are invalid produces no consensus entry."""
    entries = [
        make_agent_result("conservative", 0.0, error="failed"),
        make_agent_result("balanced", 100_000.0),
    ]
    result = _collapse_to_consensus(entries)
    assert len(result) == 1
    assert result[0].agent_name == "balanced"


FAKE_ESTIMATE = {
    "project_summary": "test",
    "location": "75001",
    "line_items": [],
    "subtotal": 100_000.0,
    "overhead_pct": 20,
    "overhead_amount": 20_000.0,
    "margin_pct": 12,
    "margin_amount": 14_400.0,
    "total_bid": 134_400.0,
    "confidence": "medium",
    "notes": "",
}


@pytest.mark.anyio
async def test_run_tournament_grid_shape(tmp_path, monkeypatch):
    """With n_samples=1, run_tournament should produce 5×3 = 15 raw entries."""
    monkeypatch.setattr(
        "backend.agents.tournament.DB_PATH",
        str(tmp_path / "test.db"),
    )

    import aiosqlite
    from backend.api.main import _CREATE_TABLES
    async with aiosqlite.connect(str(tmp_path / "test.db")) as db:
        await db.executescript(_CREATE_TABLES)
        for sql in [
            "ALTER TABLE tournament_entries ADD COLUMN temperature REAL DEFAULT 0.7",
            "ALTER TABLE tournament_entries ADD COLUMN is_consensus INTEGER DEFAULT 0",
        ]:
            try:
                await db.execute(sql)
            except Exception:
                pass
        await db.commit()

    with patch(
        "backend.agents.tournament.run_prebid_calc_with_modifier",
        new=AsyncMock(return_value=FAKE_ESTIMATE),
    ):
        from backend.agents.tournament import run_tournament
        result = await run_tournament(
            description="Build a 10,000 sqft office building in Dallas TX",
            zip_code="75001",
            n_samples=1,
        )

    assert len(result.entries) == 15  # 5 personalities × 3 temps × 1 sample
    assert len(result.consensus_entries) == 5  # one per personality


@pytest.mark.anyio
async def test_run_tournament_n_samples_2(tmp_path, monkeypatch):
    """Default n_samples=2 produces 5×3×2 = 30 raw entries."""
    monkeypatch.setattr(
        "backend.agents.tournament.DB_PATH",
        str(tmp_path / "test.db"),
    )

    import aiosqlite
    from backend.api.main import _CREATE_TABLES
    async with aiosqlite.connect(str(tmp_path / "test.db")) as db:
        await db.executescript(_CREATE_TABLES)
        for sql in [
            "ALTER TABLE tournament_entries ADD COLUMN temperature REAL DEFAULT 0.7",
            "ALTER TABLE tournament_entries ADD COLUMN is_consensus INTEGER DEFAULT 0",
        ]:
            try:
                await db.execute(sql)
            except Exception:
                pass
        await db.commit()

    with patch(
        "backend.agents.tournament.run_prebid_calc_with_modifier",
        new=AsyncMock(return_value=FAKE_ESTIMATE),
    ):
        from backend.agents.tournament import run_tournament
        result = await run_tournament(
            description="Build a 10,000 sqft office building in Dallas TX",
            zip_code="75001",
            n_samples=2,
        )

    assert len(result.entries) == 30
    assert len(result.consensus_entries) == 5


@pytest.mark.anyio
async def test_run_tournament_entries_have_temperature(tmp_path, monkeypatch):
    """Each raw entry must carry the temperature it was called with."""
    monkeypatch.setattr(
        "backend.agents.tournament.DB_PATH",
        str(tmp_path / "test.db"),
    )

    import aiosqlite
    from backend.api.main import _CREATE_TABLES
    async with aiosqlite.connect(str(tmp_path / "test.db")) as db:
        await db.executescript(_CREATE_TABLES)
        for sql in [
            "ALTER TABLE tournament_entries ADD COLUMN temperature REAL DEFAULT 0.7",
            "ALTER TABLE tournament_entries ADD COLUMN is_consensus INTEGER DEFAULT 0",
        ]:
            try:
                await db.execute(sql)
            except Exception:
                pass
        await db.commit()

    with patch(
        "backend.agents.tournament.run_prebid_calc_with_modifier",
        new=AsyncMock(return_value=FAKE_ESTIMATE),
    ):
        from backend.agents.tournament import run_tournament
        result = await run_tournament(
            description="Build a 10,000 sqft office building in Dallas TX",
            zip_code="75001",
            n_samples=1,
        )

    temps = {e.temperature for e in result.entries}
    assert temps == {0.3, 0.7, 1.0}
