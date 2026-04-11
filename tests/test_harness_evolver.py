import asyncio
import json
import shutil
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

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
        __import__("backend.agents.harness_evolver", fromlist=["evolve_harness"]).evolve_harness(
            "nonexistent"
        )
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
                "conservative": 0.22,
                "balanced": 0.20,
                "aggressive": 0.20,
                "historical_match": 0.19,
                "market_beater": 0.19,
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
            "win_rate_by_agent": {
                "aggressive": 0.75,
                "conservative": 0.06,
                "balanced": 0.06,
                "historical_match": 0.07,
                "market_beater": 0.06,
            },
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
            "win_rate_by_agent": {
                "aggressive": 0.40,
                "conservative": 0.25,
                "balanced": 0.15,
                "historical_match": 0.10,
                "market_beater": 0.10,
            },
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
    (tmp_path / "default.json").write_text(json.dumps(profile))

    async def _mock_proposer(**kw):
        return '{"conservative": "## BIDDING PERSONALITY: CONSERVATIVE\\nEVOLVED CONTENT\\n"}'

    monkeypatch.setattr(ev, "_run_agentic_proposer", _mock_proposer)
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
                "conservative": 0.08,
                "balanced": 0.08,
                "aggressive": 0.68,
                "historical_match": 0.08,
                "market_beater": 0.08,
            },
            "avg_winning_bid": 0.0,
            "avg_winning_margin": 0.0,
            "wins_by_agent": {},
        },
    }
    (tmp_path / "md_client.json").write_text(json.dumps(profile))

    wrapped = '```json\n{"balanced": "## BIDDING PERSONALITY: BALANCED\\nFROM MARKDOWN\\n"}\n```'

    async def _mock_proposer_md(**kw):
        return wrapped

    monkeypatch.setattr(ev, "_run_agentic_proposer", _mock_proposer_md)
    monkeypatch.setattr(ev, "_get_generation_number", lambda: 2)
    with patch("backend.agents.harness_evolver._git_commit") as mock_git:
        mock_git.return_value = "def5678"
        result = asyncio.run(ev.evolve_harness("md_client"))

    assert result["status"] == "evolved"
    assert "FROM MARKDOWN" in fake_tourn.read_text()


def test_judge_tournament_fires_evolution_when_dominant(tmp_path, monkeypatch):
    """judge_tournament creates an evolution task when one agent dominates."""
    import asyncio

    import aiosqlite

    import backend.agents.feedback_loop as fl
    import backend.agents.harness_evolver as ev
    from backend.api.main import _CREATE_TABLES

    monkeypatch.setattr(fl, "PROFILES_DIR", tmp_path)

    db_path = str(tmp_path / "judge_test.db")
    import backend.agents.judge as judge_mod

    monkeypatch.setattr(judge_mod, "DB_PATH", db_path)

    # Profile with dominant agent
    profile = {
        "client_id": "judge_client",
        "winning_examples": [],
        "agent_elo": {a: 1000 for a in fl.ALL_AGENTS},
        "stats": {
            "total_tournaments": 12,
            "win_rate_by_agent": {
                "conservative": 0.07,
                "balanced": 0.07,
                "aggressive": 0.72,
                "historical_match": 0.07,
                "market_beater": 0.07,
            },
            "avg_winning_bid": 0.0,
            "avg_winning_margin": 0.0,
            "wins_by_agent": {a: 0 for a in fl.ALL_AGENTS},
        },
    }
    (tmp_path / "judge_client.json").write_text(json.dumps(profile))

    async def run():
        async with aiosqlite.connect(db_path) as db:
            await db.executescript(_CREATE_TABLES)
            await db.execute(
                "INSERT INTO bid_tournaments (client_id, project_description, zip_code, status) VALUES (?,?,?,?)",
                ("judge_client", "test project", "76801", "pending"),
            )
            await db.execute(
                "INSERT INTO tournament_entries (tournament_id, agent_name, total_bid, line_items_json, won, score) VALUES (?,?,?,?,?,?)",
                (1, "aggressive", 100000.0, '{"line_items": []}', 0, None),
            )
            await db.commit()

        tasks_created = []
        original_create_task = asyncio.create_task

        def mock_create_task(coro, **kwargs):
            tasks_created.append(coro.__qualname__ if hasattr(coro, "__qualname__") else str(coro))
            # Cancel it immediately so it doesn't run
            t = original_create_task(coro, **kwargs)
            t.cancel()
            return t

        with patch("backend.agents.judge.asyncio.create_task", side_effect=mock_create_task):
            with patch("backend.agents.feedback_loop.update_client_profile"):
                from backend.agents.judge import judge_tournament

                await judge_tournament(
                    tournament_id=1,
                    winner_agent_name="aggressive",
                )

        return tasks_created

    tasks = asyncio.run(run())
    # At least one task should be the evolve_harness coroutine
    assert any("evolve_harness" in t for t in tasks)


# ── Dry-run tests ─────────────────────────────────────────────────────────────


def test_evolve_harness_dry_run_returns_diff_without_writing(tmp_path, monkeypatch):
    """dry_run=True returns proposed diff and does NOT write tournament.py or commit."""
    import shutil

    import backend.agents.feedback_loop as fl
    import backend.agents.harness_evolver as ev

    monkeypatch.setattr(fl, "PROFILES_DIR", tmp_path)
    fake_tourn = tmp_path / "tournament.py"
    shutil.copy(ev.TOURNAMENT_PY, fake_tourn)
    original_content = fake_tourn.read_text()
    monkeypatch.setattr(ev, "TOURNAMENT_PY", fake_tourn)

    profile = {
        "client_id": "dry_client",
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
    (tmp_path / "dry_client.json").write_text(json.dumps(profile))

    async def _mock_proposer_dry(**kw):
        return '{"conservative": "## BIDDING PERSONALITY: CONSERVATIVE\\nDRY RUN CONTENT\\n"}'

    monkeypatch.setattr(ev, "_run_agentic_proposer", _mock_proposer_dry)

    with patch("backend.agents.harness_evolver._git_commit") as mock_git:
        result = asyncio.run(ev.evolve_harness("dry_client", dry_run=True))
        mock_git.assert_not_called()

    assert result["status"] == "dry_run"
    assert "conservative" in result["evolved_agents"]
    assert "proposed_prompts" in result
    assert "DRY RUN CONTENT" in result["proposed_prompts"]["conservative"]
    assert "diff" in result
    assert "DRY RUN CONTENT" in result["diff"]
    # File must be untouched
    assert fake_tourn.read_text() == original_content


def test_post_evolve_dry_run_endpoint(tmp_path, monkeypatch):
    """POST /api/tournament/evolve with dry_run=true returns dry_run status, no git commit."""
    import shutil

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import backend.agents.feedback_loop as fl
    import backend.agents.harness_evolver as ev
    from backend.api.routes import router

    monkeypatch.setattr(fl, "PROFILES_DIR", tmp_path)
    monkeypatch.setenv("API_KEY", "test-key")
    fake_tourn = tmp_path / "tournament.py"
    shutil.copy(ev.TOURNAMENT_PY, fake_tourn)
    original_content = fake_tourn.read_text()
    monkeypatch.setattr(ev, "TOURNAMENT_PY", fake_tourn)

    profile = {
        "client_id": "dry_ep_client",
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
    (tmp_path / "dry_ep_client.json").write_text(json.dumps(profile))

    async def _mock_proposer_ep_dry(**kw):
        return '{"balanced": "## BIDDING PERSONALITY: BALANCED\\nEP DRY RUN\\n"}'

    monkeypatch.setattr(ev, "_run_agentic_proposer", _mock_proposer_ep_dry)

    with patch("backend.agents.harness_evolver._git_commit") as mock_git:
        app = FastAPI()
        app.include_router(router, prefix="/api")
        with TestClient(app) as c:
            resp = c.post(
                "/api/tournament/evolve",
                json={"client_id": "dry_ep_client", "dry_run": True},
                headers={"X-API-Key": "test-key"},
            )
        mock_git.assert_not_called()

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "dry_run"
    assert "diff" in data
    assert fake_tourn.read_text() == original_content


# ── Endpoint tests ─────────────────────────────────────────────────────────────


def test_post_evolve_returns_skipped_when_no_dominance(tmp_path, monkeypatch):
    """POST /api/tournament/evolve returns skipped when win rates are balanced."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import backend.agents.feedback_loop as fl
    import backend.agents.harness_evolver as ev
    from backend.api.routes import router

    monkeypatch.setattr(fl, "PROFILES_DIR", tmp_path)
    monkeypatch.setenv("API_KEY", "test-key")

    profile = {
        "client_id": "balanced_client",
        "winning_examples": [],
        "stats": {
            "total_tournaments": 20,
            "win_rate_by_agent": {
                "conservative": 0.22,
                "balanced": 0.20,
                "aggressive": 0.20,
                "historical_match": 0.19,
                "market_beater": 0.19,
            },
            "avg_winning_bid": 0.0,
            "avg_winning_margin": 0.0,
            "wins_by_agent": {},
        },
    }
    (tmp_path / "balanced_client.json").write_text(json.dumps(profile))

    app = FastAPI()
    app.include_router(router, prefix="/api")
    with TestClient(app) as c:
        resp = c.post(
            "/api/tournament/evolve",
            json={"client_id": "balanced_client"},
            headers={"X-API-Key": "test-key"},
        )

    assert resp.status_code == 200
    assert resp.json()["status"] == "skipped"


def test_post_evolve_returns_423_when_locked(monkeypatch):
    """POST /api/tournament/evolve returns 423 when evolution is in progress."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import backend.agents.harness_evolver as ev
    from backend.api.routes import router

    monkeypatch.setenv("API_KEY", "test-key")

    app = FastAPI()
    app.include_router(router, prefix="/api")

    async def run_while_locked():
        async with ev._get_lock():
            with TestClient(app) as c:
                return c.post(
                    "/api/tournament/evolve",
                    json={"client_id": "any"},
                    headers={"X-API-Key": "test-key"},
                )

    resp = asyncio.run(run_while_locked())
    assert resp.status_code == 423


def test_post_evolve_returns_evolved_on_success(tmp_path, monkeypatch):
    """POST /api/tournament/evolve returns evolved result when Claude succeeds."""
    import shutil

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import backend.agents.feedback_loop as fl
    import backend.agents.harness_evolver as ev
    from backend.api.routes import router

    monkeypatch.setattr(fl, "PROFILES_DIR", tmp_path)
    monkeypatch.setenv("API_KEY", "test-key")
    fake_tourn = tmp_path / "tournament.py"
    shutil.copy(ev.TOURNAMENT_PY, fake_tourn)
    monkeypatch.setattr(ev, "TOURNAMENT_PY", fake_tourn)

    profile = {
        "client_id": "evolve_client",
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
    (tmp_path / "evolve_client.json").write_text(json.dumps(profile))

    async def _mock_proposer_ep(**kw):
        return '{"conservative": "## BIDDING PERSONALITY: CONSERVATIVE\\nEP TEST\\n"}'

    monkeypatch.setattr(ev, "_run_agentic_proposer", _mock_proposer_ep)
    monkeypatch.setattr(ev, "_get_generation_number", lambda: 1)
    with patch("backend.agents.harness_evolver._git_commit") as mock_git:
        mock_git.return_value = "fff9999"
        app = FastAPI()
        app.include_router(router, prefix="/api")
        with TestClient(app) as c:
            resp = c.post(
                "/api/tournament/evolve",
                json={"client_id": "evolve_client"},
                headers={"X-API-Key": "test-key"},
            )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "evolved"
    assert "conservative" in data["evolved_agents"]
    assert data["generation"] == 2
