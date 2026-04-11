"""Tests for BidToWin agent — mock LLM, verify async behavior and JSON parsing."""

from unittest.mock import AsyncMock, patch

import pytest

from backend.agents.bid_to_win import run_bid_to_win

_MOCK_RESPONSE = {
    "rfp_analysis": {
        "owner_priorities": ["lowest price"],
        "scoring_criteria": ["price 60%", "qualifications 40%"],
        "scope_summary": "Office renovation",
        "deadline": "2026-05-01",
        "red_flags": [],
    },
    "scope_gaps": [],
    "competitor_range": {"low": 120000, "mid": 135000, "high": 150000},
    "bid_scenarios": [
        {
            "name": "Conservative",
            "bid_price": 145000,
            "markup_over_cost": 18.0,
            "win_probability": 0.35,
            "notes": "Safe",
        },
        {
            "name": "Balanced",
            "bid_price": 138000,
            "markup_over_cost": 12.0,
            "win_probability": 0.55,
            "notes": "Mid",
        },
        {
            "name": "Aggressive",
            "bid_price": 128000,
            "markup_over_cost": 5.0,
            "win_probability": 0.75,
            "notes": "Lean",
        },
    ],
    "recommended_scenario": "Balanced",
    "proposal_narrative": "We propose a competitive bid...",
    "scope_exclusions": ["furniture"],
    "strategy_notes": "Focus on qualifications.",
}


@pytest.mark.anyio
async def test_run_bid_to_win_returns_strategy():
    """run_bid_to_win should return parsed JSON with bid_scenarios."""
    with patch(
        "backend.agents.bid_to_win.call_with_json_retry",
        new=AsyncMock(return_value=_MOCK_RESPONSE),
    ):
        result = await run_bid_to_win(
            estimate={"total_bid": 125000, "project_summary": "Office reno", "location": "76801"},
            rfp_text="Owner seeks bids for office renovation. 5000 sqft. Best value selection.",
            project_type="commercial",
            known_competitors=["ABC Corp", "XYZ Builders"],
        )

    assert "bid_scenarios" in result
    assert len(result["bid_scenarios"]) == 3
    assert result["recommended_scenario"] == "Balanced"


@pytest.mark.anyio
async def test_run_bid_to_win_no_competitors():
    """Should handle None competitors gracefully."""
    with patch(
        "backend.agents.bid_to_win.call_with_json_retry",
        new=AsyncMock(return_value=_MOCK_RESPONSE),
    ) as mock_call:
        await run_bid_to_win(
            estimate={"total_bid": 100000},
            rfp_text="Simple residential remodel project scope.",
            project_type="residential",
            known_competitors=None,
        )

    # Verify "unknown" is in the user message when no competitors
    call_args = mock_call.call_args
    messages = call_args.kwargs["messages"]
    assert "unknown" in messages[0]["content"].lower()


@pytest.mark.anyio
async def test_run_bid_to_win_uses_settings_model():
    """Should use settings.claude_model, not a hardcoded model string."""
    with patch(
        "backend.agents.bid_to_win.call_with_json_retry",
        new=AsyncMock(return_value=_MOCK_RESPONSE),
    ) as mock_call:
        with patch("backend.agents.bid_to_win.settings") as mock_settings:
            mock_settings.claude_model = "claude-test-model"
            await run_bid_to_win(
                estimate={"total_bid": 100000},
                rfp_text="Test project requiring bid strategy analysis.",
            )

    call_args = mock_call.call_args
    assert call_args.kwargs["model"] == "claude-test-model"
