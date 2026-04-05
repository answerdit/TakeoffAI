"""Tests for tournament engine — data structures, collapse logic, grid expansion."""

import pytest
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
