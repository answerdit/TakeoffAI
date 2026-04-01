import asyncio
import json
import shutil
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


# ── Claude call + rewrite tests ────────────────────────────────────────────────

def test_evolve_harness_applies_proposed_prompts(tmp_path, monkeypatch):
    """evolve_harness rewrites tournament.py with Claude's proposed prompt."""
    import shutil
    import backend.agents.feedback_loop as fl
    import backend.agents.harness_evolver as ev

    monkeypatch.setattr(fl, "PROFILES_DIR", tmp_path)

    # Point TOURNAMENT_PY at a tmp copy so real source isn't touched
    fake_tourn = tmp_path / "tournament.py"
    shutil.copy(ev.TOURNAMENT_PY, fake_tourn)
    monkeypatch.setattr(ev, "TOURNAMENT_PY", fake_tourn)

    profile = {
        "client_id": "default",
        "winning_examples": [],
        "stats": {
            "total_tournaments": 15,
            "win_rate_by_agent": {
                "conservative": 0.07, "balanced": 0.07, "aggressive": 0.72,
                "historical_match": 0.07, "market_beater": 0.07,
            },
            "avg_winning_bid": 0.0, "avg_winning_margin": 0.0, "wins_by_agent": {},
        },
    }
    (tmp_path / "default.json").write_text(json.dumps(profile))

    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(
        text='{"conservative": "## BIDDING PERSONALITY: CONSERVATIVE\\nEVOLVED CONTENT\\n"}'
    )]

    with patch("backend.agents.harness_evolver._call_claude_sync") as mock_call:
        mock_call.return_value = '{"conservative": "## BIDDING PERSONALITY: CONSERVATIVE\\nEVOLVED CONTENT\\n"}'
        monkeypatch.setattr(ev, "_get_generation_number", lambda: 0)
        with patch("backend.agents.harness_evolver._git_commit") as mock_git:
            mock_git.return_value = "abc1234"
            result = asyncio.run(ev.evolve_harness("default"))

    assert result["status"] == "evolved"
    assert "conservative" in result["evolved_agents"]
    assert "aggressive" not in result["evolved_agents"]
    assert result["dominant_agent"] == "aggressive"
    assert "EVOLVED CONTENT" in fake_tourn.read_text()


def test_evolve_harness_returns_locked_when_already_running():
    """evolve_harness returns locked immediately if lock is held."""
    import backend.agents.harness_evolver as ev

    async def run():
        # Acquire lock manually then call evolve_harness
        async with ev._get_lock():
            return await ev.evolve_harness("any_client")

    result = asyncio.run(run())
    assert result["status"] == "locked"


def test_evolve_harness_handles_markdown_wrapped_json(tmp_path, monkeypatch):
    """Claude sometimes wraps JSON in ```json ... ``` — parser handles it."""
    import shutil
    import backend.agents.feedback_loop as fl
    import backend.agents.harness_evolver as ev

    monkeypatch.setattr(fl, "PROFILES_DIR", tmp_path)
    fake_tourn = tmp_path / "tournament.py"
    shutil.copy(ev.TOURNAMENT_PY, fake_tourn)
    monkeypatch.setattr(ev, "TOURNAMENT_PY", fake_tourn)

    profile = {
        "client_id": "md_client",
        "winning_examples": [],
        "stats": {
            "total_tournaments": 12,
            "win_rate_by_agent": {
                "conservative": 0.08, "balanced": 0.08, "aggressive": 0.68,
                "historical_match": 0.08, "market_beater": 0.08,
            },
            "avg_winning_bid": 0.0, "avg_winning_margin": 0.0, "wins_by_agent": {},
        },
    }
    (tmp_path / "md_client.json").write_text(json.dumps(profile))

    wrapped = '```json\n{"balanced": "## BIDDING PERSONALITY: BALANCED\\nFROM MARKDOWN\\n"}\n```'

    with patch("backend.agents.harness_evolver._call_claude_sync") as mock_call:
        mock_call.return_value = wrapped
        monkeypatch.setattr(ev, "_get_generation_number", lambda: 2)
        with patch("backend.agents.harness_evolver._git_commit") as mock_git:
            mock_git.return_value = "def5678"
            result = asyncio.run(ev.evolve_harness("md_client"))

    assert result["status"] == "evolved"
    assert "FROM MARKDOWN" in fake_tourn.read_text()
