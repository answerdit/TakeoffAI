"""Tests for tournament engine — data structures, collapse logic, grid expansion."""

import pytest
from backend.agents.tournament import AgentResult, TournamentResult


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
