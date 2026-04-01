import asyncio
import json
import pytest
from unittest.mock import patch, MagicMock, AsyncMock


# ── Regex unit test ────────────────────────────────────────────────────────────

def test_replace_prompt_in_source_replaces_target_agent():
    from backend.agents.harness_evolver import _replace_prompt_in_source
    source = '''\
PERSONALITY_PROMPTS = {
    "conservative": """## CONSERVATIVE
old conservative text
""",
    "balanced": """## BALANCED
old balanced text
""",
}'''
    result = _replace_prompt_in_source(source, "conservative", "## CONSERVATIVE\nnew content\n")
    assert "new content" in result
    assert "old conservative text" not in result
    assert "old balanced text" in result  # unchanged


def test_replace_prompt_in_source_leaves_other_agents_untouched():
    from backend.agents.harness_evolver import _replace_prompt_in_source
    source = '''\
    "aggressive": """## AGGRESSIVE
aggressive text
""",
    "balanced": """## BALANCED
balanced text
""",'''
    result = _replace_prompt_in_source(source, "aggressive", "## AGGRESSIVE\nreplaced\n")
    assert "balanced text" in result


# ── Skip logic tests ───────────────────────────────────────────────────────────

def test_evolve_harness_skips_when_no_profile(tmp_path, monkeypatch):
    import backend.agents.feedback_loop as fl
    monkeypatch.setattr(fl, "PROFILES_DIR", tmp_path)

    result = asyncio.run(
        __import__("backend.agents.harness_evolver", fromlist=["evolve_harness"]).evolve_harness("nonexistent")
    )
    assert result["status"] == "skipped"
    assert "no profile" in result["reason"]


def test_evolve_harness_skips_insufficient_tournaments(tmp_path, monkeypatch):
    import backend.agents.feedback_loop as fl
    import backend.agents.harness_evolver as ev
    monkeypatch.setattr(fl, "PROFILES_DIR", tmp_path)

    profile = {
        "client_id": "c1",
        "stats": {"total_tournaments": 5, "win_rate_by_agent": {"aggressive": 0.80}},
    }
    (tmp_path / "c1.json").write_text(json.dumps(profile))

    result = asyncio.run(ev.evolve_harness("c1"))
    assert result["status"] == "skipped"
    assert "insufficient" in result["reason"]


def test_evolve_harness_skips_no_dominance(tmp_path, monkeypatch):
    import backend.agents.feedback_loop as fl
    import backend.agents.harness_evolver as ev
    monkeypatch.setattr(fl, "PROFILES_DIR", tmp_path)

    profile = {
        "client_id": "c2",
        "stats": {
            "total_tournaments": 20,
            "win_rate_by_agent": {
                "conservative": 0.22, "balanced": 0.20, "aggressive": 0.20,
                "historical_match": 0.19, "market_beater": 0.19,
            },
        },
    }
    (tmp_path / "c2.json").write_text(json.dumps(profile))

    result = asyncio.run(ev.evolve_harness("c2"))
    assert result["status"] == "skipped"
    assert "no dominance" in result["reason"]


def test_check_dominance_returns_true_when_above_threshold(tmp_path, monkeypatch):
    import backend.agents.feedback_loop as fl
    import backend.agents.harness_evolver as ev
    monkeypatch.setattr(fl, "PROFILES_DIR", tmp_path)

    profile = {
        "client_id": "c3",
        "stats": {
            "total_tournaments": 15,
            "win_rate_by_agent": {"aggressive": 0.75, "conservative": 0.06,
                                   "balanced": 0.06, "historical_match": 0.07, "market_beater": 0.06},
        },
    }
    (tmp_path / "c3.json").write_text(json.dumps(profile))
    assert ev.check_dominance("c3") is True


def test_check_dominance_returns_false_below_threshold(tmp_path, monkeypatch):
    import backend.agents.feedback_loop as fl
    import backend.agents.harness_evolver as ev
    monkeypatch.setattr(fl, "PROFILES_DIR", tmp_path)

    profile = {
        "client_id": "c4",
        "stats": {
            "total_tournaments": 15,
            "win_rate_by_agent": {"aggressive": 0.40, "conservative": 0.25,
                                   "balanced": 0.15, "historical_match": 0.10, "market_beater": 0.10},
        },
    }
    (tmp_path / "c4.json").write_text(json.dumps(profile))
    assert ev.check_dominance("c4") is False


def test_check_dominance_returns_false_insufficient_data(tmp_path, monkeypatch):
    import backend.agents.feedback_loop as fl
    import backend.agents.harness_evolver as ev
    monkeypatch.setattr(fl, "PROFILES_DIR", tmp_path)

    profile = {
        "client_id": "c5",
        "stats": {"total_tournaments": 9, "win_rate_by_agent": {"aggressive": 0.90}},
    }
    (tmp_path / "c5.json").write_text(json.dumps(profile))
    assert ev.check_dominance("c5") is False
