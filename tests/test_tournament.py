"""Tests for tournament engine — data structures, collapse logic, grid expansion."""

from unittest.mock import AsyncMock, patch

import pytest

from backend.agents.tournament import (
    AgentResult,
    TournamentResult,
    _collapse_to_consensus,
    _maybe_rerank_by_accuracy,
)


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


@pytest.mark.anyio
async def test_run_tournament_no_client_id_has_empty_annotations(tmp_path, monkeypatch):
    """Without a client_id, annotations are empty and recommended_agent is None."""
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

    assert result.accuracy_annotations == {}
    assert result.accuracy_recommended_agent is None


@pytest.mark.anyio
async def test_run_tournament_attaches_client_annotations(tmp_path, monkeypatch):
    """With a client_id and calibration data, annotations flow into TournamentResult."""
    import json as _json

    monkeypatch.setattr(
        "backend.agents.tournament.DB_PATH",
        str(tmp_path / "test.db"),
    )

    # Point feedback_loop at a tmp profiles dir and pre-seed one client
    import backend.agents.feedback_loop as fl

    monkeypatch.setattr(fl, "PROFILES_DIR", tmp_path / "profiles")
    (tmp_path / "profiles").mkdir()
    profile = {
        "client_id": "acme",
        "created_at": "2026-01-01T00:00:00",
        "winning_examples": [],
        "agent_elo": {a: 1000 for a in fl.ALL_AGENTS},
        "stats": {},
        "calibration": {
            "agent_deviation_history": {
                "conservative": [2.0, 3.0, 2.5],
                "balanced": [0.5, 0.8, 0.3],
                "aggressive": [10.0, 9.0, 11.0],
                "historical_match": [1.0, 1.2, 0.9],
                "market_beater": [4.0, 3.5, 4.2],
            },
            "red_flagged_agents": ["aggressive"],
        },
    }
    (tmp_path / "profiles" / "acme.json").write_text(_json.dumps(profile))

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
            description="Office buildout",
            zip_code="75001",
            client_id="acme",
            n_samples=1,
        )

    assert result.accuracy_recommended_agent == "balanced"
    ann = result.accuracy_annotations
    assert ann["balanced"]["avg_deviation_pct"] == pytest.approx(0.5333, abs=0.01)
    assert ann["balanced"]["closed_job_count"] == 3
    assert ann["balanced"]["is_accuracy_flagged"] is False
    assert ann["aggressive"]["is_accuracy_flagged"] is True

    # Consensus order must remain unchanged by annotation (hybrid rollout rule)
    assert len(result.consensus_entries) == 5


@pytest.mark.anyio
async def test_historical_retrieval_runs_off_event_loop(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "backend.agents.tournament.DB_PATH",
        str(tmp_path / "test.db"),
    )

    import aiosqlite
    import backend.agents.feedback_loop as fl

    from backend.api.main import _CREATE_TABLES

    monkeypatch.setattr(fl, "PROFILES_DIR", tmp_path / "profiles")
    (tmp_path / "profiles").mkdir()

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

    to_thread_calls: list[str] = []

    async def fake_to_thread(func, *args, **kwargs):
        to_thread_calls.append(getattr(func, "__name__", repr(func)))
        if getattr(func, "__name__", "") == "get_comparable_jobs":
            return []
        return func(*args, **kwargs)

    with patch(
        "backend.agents.tournament.asyncio.to_thread",
        new=AsyncMock(side_effect=fake_to_thread),
    ):
        with patch(
            "backend.agents.tournament.run_prebid_calc_with_modifier",
            new=AsyncMock(return_value=FAKE_ESTIMATE),
        ):
            from backend.agents.tournament import run_tournament

            await run_tournament(
                description="Office buildout",
                zip_code="75001",
                client_id="acme",
                n_samples=1,
            )

    assert "get_comparable_jobs" in to_thread_calls


# ── Hybrid rollout phase 2: feature-flagged re-ranking ──────────────────────


def _sample_consensus():
    return [
        make_agent_result("conservative", 100_000.0),
        make_agent_result("balanced", 105_000.0),
        make_agent_result("aggressive", 110_000.0),
        make_agent_result("historical_match", 115_000.0),
        make_agent_result("market_beater", 120_000.0),
    ]


def _sample_annotations(balanced_jobs=5):
    return {
        "conservative": {
            "avg_deviation_pct": 3.0,
            "closed_job_count": 5,
            "is_accuracy_flagged": False,
        },
        "balanced": {
            "avg_deviation_pct": 0.5,
            "closed_job_count": balanced_jobs,
            "is_accuracy_flagged": False,
        },
        "aggressive": {
            "avg_deviation_pct": 10.0,
            "closed_job_count": 5,
            "is_accuracy_flagged": True,
        },
        "historical_match": {
            "avg_deviation_pct": None,
            "closed_job_count": 0,
            "is_accuracy_flagged": False,
        },
        "market_beater": {
            "avg_deviation_pct": 4.0,
            "closed_job_count": 5,
            "is_accuracy_flagged": False,
        },
    }


def test_rerank_disabled_by_default_preserves_order(monkeypatch):
    import backend.config as cfg

    monkeypatch.setattr(cfg.settings, "tournament_accuracy_rerank_enabled", False)
    consensus = _sample_consensus()
    out, active = _maybe_rerank_by_accuracy(consensus, _sample_annotations(), "balanced")
    assert [e.agent_name for e in out] == [e.agent_name for e in consensus]
    assert active is False


def test_rerank_enabled_but_too_few_jobs_preserves_order(monkeypatch):
    import backend.config as cfg

    monkeypatch.setattr(cfg.settings, "tournament_accuracy_rerank_enabled", True)
    monkeypatch.setattr(cfg.settings, "tournament_accuracy_rerank_min_jobs", 5)
    consensus = _sample_consensus()
    out, active = _maybe_rerank_by_accuracy(
        consensus, _sample_annotations(balanced_jobs=2), "balanced"
    )
    assert [e.agent_name for e in out] == [e.agent_name for e in consensus]
    assert active is False


def test_rerank_enabled_sorts_by_deviation_flags_bottom(monkeypatch):
    import backend.config as cfg

    monkeypatch.setattr(cfg.settings, "tournament_accuracy_rerank_enabled", True)
    monkeypatch.setattr(cfg.settings, "tournament_accuracy_rerank_min_jobs", 5)
    consensus = _sample_consensus()
    out, active = _maybe_rerank_by_accuracy(consensus, _sample_annotations(), "balanced")
    # Non-flagged with data, ascending: balanced(0.5) < conservative(3.0) < market_beater(4.0)
    # Then no-data: historical_match
    # Then flagged: aggressive
    assert [e.agent_name for e in out] == [
        "balanced",
        "conservative",
        "market_beater",
        "historical_match",
        "aggressive",
    ]
    assert active is True


def test_rerank_no_recommended_agent_preserves_order(monkeypatch):
    import backend.config as cfg

    monkeypatch.setattr(cfg.settings, "tournament_accuracy_rerank_enabled", True)
    monkeypatch.setattr(cfg.settings, "tournament_accuracy_rerank_min_jobs", 5)
    consensus = _sample_consensus()
    out, active = _maybe_rerank_by_accuracy(consensus, _sample_annotations(), None)
    assert [e.agent_name for e in out] == [e.agent_name for e in consensus]
    assert active is False


# ── Regression: economic & tier invariants of the hybrid rule ──────────────


def test_rerank_flagged_cheapest_bid_still_sorts_last(monkeypatch):
    """Economic invariant: a flagged agent must land at the bottom of the
    consensus order even when it produced the LOWEST raw bid. This encodes
    the whole point of the hybrid rollout — "don't trust cheap-but-inaccurate."
    Any refactor that accidentally re-sorts by bid after the rerank, or drops
    `is_accuracy_flagged` through the tier-key lookup, will invert this and
    this test will catch it."""
    import backend.config as cfg

    monkeypatch.setattr(cfg.settings, "tournament_accuracy_rerank_enabled", True)
    monkeypatch.setattr(cfg.settings, "tournament_accuracy_rerank_min_jobs", 5)

    # aggressive is the cheapest ($85k) AND the flagged one
    consensus = [
        make_agent_result("conservative", 150_000.0),
        make_agent_result("balanced", 140_000.0),
        make_agent_result("aggressive", 85_000.0),
        make_agent_result("historical_match", 155_000.0),
        make_agent_result("market_beater", 145_000.0),
    ]
    out, active = _maybe_rerank_by_accuracy(consensus, _sample_annotations(), "balanced")

    assert active is True
    cheapest = min(consensus, key=lambda e: e.total_bid)
    assert cheapest.agent_name == "aggressive", "test fixture must put aggressive as cheapest"
    # The cheapest bid is flagged → must sort last, not first
    assert out[-1].agent_name == "aggressive"
    assert out[-1].total_bid == 85_000.0
    assert out[0].agent_name != "aggressive"


def test_rerank_no_data_tier_sits_between_data_and_flagged(monkeypatch):
    """Tier invariant: an agent with no deviation history must sort AFTER
    every non-flagged agent that has data, and BEFORE any flagged agent.
    This protects the three-tier rule from refactors that collapse tiers
    (e.g. treating `avg_deviation_pct is None` as 0.0 and letting it outrank
    real data, or lumping no-data in with flagged)."""
    import backend.config as cfg

    monkeypatch.setattr(cfg.settings, "tournament_accuracy_rerank_enabled", True)
    monkeypatch.setattr(cfg.settings, "tournament_accuracy_rerank_min_jobs", 5)

    consensus = _sample_consensus()
    out, active = _maybe_rerank_by_accuracy(consensus, _sample_annotations(), "balanced")
    assert active is True

    names = [e.agent_name for e in out]
    no_data_idx = names.index("historical_match")
    flagged_idx = names.index("aggressive")

    data_tier_agents = {"balanced", "conservative", "market_beater"}
    data_indices = [names.index(n) for n in data_tier_agents]

    assert all(
        i < no_data_idx for i in data_indices
    ), f"no-data agent leaked ahead of an agent with data: {names}"
    assert no_data_idx < flagged_idx, f"no-data agent must sort before flagged agent: {names}"


@pytest.mark.anyio
async def test_historical_retrieval_runs_off_event_loop(tmp_path, monkeypatch):
    """P2 regression: get_comparable_jobs is synchronous file I/O (glob +
    frontmatter parse for every job in the vault). It must be dispatched via
    asyncio.to_thread inside run_tournament so a growing wiki/jobs/ corpus
    doesn't stall concurrent tournament requests on the event loop."""
    monkeypatch.setattr(
        "backend.agents.tournament.DB_PATH",
        str(tmp_path / "test.db"),
    )

    # Hermetic vault + profile paths so the test doesn't read the real repo.
    import backend.agents._wiki_io as _io
    import backend.agents.feedback_loop as fl

    monkeypatch.setattr(_io, "JOBS_DIR", tmp_path / "jobs")
    monkeypatch.setattr(fl, "PROFILES_DIR", tmp_path / "profiles")

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

    import asyncio

    real_to_thread = asyncio.to_thread
    to_thread_targets: list[str] = []

    async def recording_to_thread(func, /, *args, **kwargs):
        name = getattr(func, "__name__", repr(func))
        to_thread_targets.append(name)
        return await real_to_thread(func, *args, **kwargs)

    with patch(
        "backend.agents.tournament.run_prebid_calc_with_modifier",
        new=AsyncMock(return_value=FAKE_ESTIMATE),
    ), patch("backend.agents.tournament.asyncio.to_thread", new=recording_to_thread):
        from backend.agents.tournament import run_tournament

        await run_tournament(
            description="Build a small office in Austin",
            zip_code="78701",
            client_id="acme",
            n_samples=1,
        )

    assert "get_comparable_jobs" in to_thread_targets, (
        "get_comparable_jobs must run via asyncio.to_thread, not directly on "
        f"the event loop. Dispatched via to_thread: {to_thread_targets}"
    )
