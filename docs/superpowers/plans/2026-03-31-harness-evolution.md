# Harness Evolution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Auto-evolve underperforming `PERSONALITY_PROMPTS` in `tournament.py` using Claude as a proposer when one agent dominates a client's win history, with git commits as the version store.

**Architecture:** `harness_evolver.py` holds all evolution logic (dominance check, Claude API call, regex rewrite, git commit). `judge.py` fires it as a background task after each tournament judgment when dominance is detected. `routes.py` exposes a manual trigger endpoint. No new DB tables — git history is the version record.

**Tech Stack:** Python/FastAPI, Anthropic SDK (`anthropic`), `re`, `subprocess` (git), pytest

---

## File Map

| File | Change | Responsibility |
|---|---|---|
| `backend/agents/harness_evolver.py` | CREATE | All evolution logic: dominance check, context assembly, Claude call, regex rewrite, git commit |
| `backend/agents/judge.py` | MODIFY | Fire `evolve_harness` as background task after profile update when dominance detected |
| `backend/api/routes.py` | MODIFY | Add `POST /api/tournament/evolve` manual trigger endpoint |
| `tests/test_harness_evolver.py` | CREATE | Unit + integration tests for all evolver behavior |

> **Note on circular imports:** `check_dominance` lives in `harness_evolver.py` (not `feedback_loop.py`) to avoid a circular import — `harness_evolver` imports from `feedback_loop`, so `feedback_loop` cannot import back. `judge.py` imports from `harness_evolver` directly.

---

## Task 1: Create `harness_evolver.py` — scaffold + skip logic

**Files:**
- Create: `backend/agents/harness_evolver.py`
- Create: `tests/test_harness_evolver.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_harness_evolver.py`:

```python
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
```

- [ ] **Step 2: Run to confirm FAIL**

```bash
cd "/Users/bevo/Library/CloudStorage/GoogleDrive-kroberts2007@gmail.com/My Drive/TakeoffAI"
uv run pytest tests/test_harness_evolver.py -v --tb=short 2>&1
```

Expected: `ModuleNotFoundError: No module named 'backend.agents.harness_evolver'`

- [ ] **Step 3: Create `backend/agents/harness_evolver.py`**

```python
"""
Harness Evolver — TakeoffAI
Observes agent win-rate imbalance and evolves underperforming PERSONALITY_PROMPTS
in tournament.py using Claude as a proposer. Each successful evolution is committed
to git — rollback and fork are native git operations.
"""

import asyncio
import json
import os
import re
import subprocess
from pathlib import Path

MIN_TOURNAMENTS = 10
DOMINANCE_THRESHOLD = 0.60
HARNESS_EVOLVER_MODEL = os.getenv("HARNESS_EVOLVER_MODEL", "claude-sonnet-4-6")

TOURNAMENT_PY = Path(__file__).parent / "tournament.py"

_evolution_lock = asyncio.Lock()


def _get_generation_number() -> int:
    """Count prior harness evolution git commits."""
    try:
        result = subprocess.run(
            ["git", "log", "--oneline", "--grep=harness: gen"],
            capture_output=True,
            text=True,
            cwd=TOURNAMENT_PY.parent.parent.parent,
        )
        lines = [l for l in result.stdout.strip().splitlines() if l]
        return len(lines)
    except Exception:
        return 0


def _replace_prompt_in_source(source: str, agent_name: str, new_prompt: str) -> str:
    """Surgically replace the triple-quoted string for agent_name in tournament.py source."""
    pattern = rf'("{agent_name}":\s*""")(.*?)(""")'
    return re.sub(
        pattern,
        lambda m: m.group(1) + new_prompt + m.group(3),
        source,
        flags=re.DOTALL,
    )


def check_dominance(client_id: str) -> bool:
    """
    Return True if one agent has won > DOMINANCE_THRESHOLD of this client's
    tournaments and MIN_TOURNAMENTS have been played.
    Called synchronously from judge.py before firing a background evolution task.
    """
    from backend.agents.feedback_loop import _profile_path

    path = _profile_path(client_id)
    if not path.exists():
        return False

    profile = json.loads(path.read_text())
    total = profile.get("stats", {}).get("total_tournaments", 0)
    if total < MIN_TOURNAMENTS:
        return False

    win_rates = profile.get("stats", {}).get("win_rate_by_agent", {})
    if not win_rates:
        return False

    return max(win_rates.values()) > DOMINANCE_THRESHOLD


def _build_context_prompt(
    profile: dict,
    underperforming: list[str],
    dominant_agent: str,
    dominant_rate: float,
) -> str:
    """Build the diagnostic context prompt sent to Claude."""
    from backend.agents.tournament import PERSONALITY_PROMPTS

    current_prompts = "\n\n".join(
        f'CURRENT PROMPT FOR "{agent}":\n{PERSONALITY_PROMPTS[agent]}'
        for agent in underperforming
        if agent in PERSONALITY_PROMPTS
    )
    win_rates = profile.get("stats", {}).get("win_rate_by_agent", {})
    cal = profile.get("calibration", {})
    dev_history = cal.get("agent_deviation_history", {})
    examples = profile.get("winning_examples", [])[-5:]

    return f"""You are improving an AI construction bidding tournament system.

The system runs 5 agent personalities that each estimate construction job costs.
The personality that produces the most competitive (winning) bid is reinforced.

PROBLEM: One agent is dominating — the others need more distinctive, competitive strategies.

Dominant agent: {dominant_agent} ({dominant_rate:.0%} win rate)
Agents to improve: {", ".join(underperforming)}

WIN RATES BY AGENT:
{json.dumps(win_rates, indent=2)}

DEVIATION HISTORY (last recorded deviations from actual costs, per agent):
{json.dumps({a: dev_history.get(a, []) for a in underperforming}, indent=2)}

LAST 5 WINNING EXAMPLES:
{json.dumps(examples, indent=2)}

CURRENT PROMPTS FOR UNDERPERFORMING AGENTS:
{current_prompts}

TASK: Rewrite ONLY the underperforming agents' personality prompts to make them
more competitive. Preserve diversity of bidding strategy — do not homogenize.
Each prompt must guide an LLM estimator with a distinct, viable approach.
Do NOT rewrite the {dominant_agent} prompt.

Return ONLY a valid JSON object mapping agent name to new prompt string.
Include only the agents listed under "Agents to improve". Example format:
{{
  "conservative": "## BIDDING PERSONALITY: CONSERVATIVE\\n...",
  "balanced": "## BIDDING PERSONALITY: BALANCED\\n..."
}}"""


async def evolve_harness(client_id: str) -> dict:
    """
    Read client diagnostic context, call Claude to propose improved prompts
    for underperforming agents, apply changes to tournament.py, and git commit.

    Returns a result dict with status: 'evolved' | 'skipped' | 'locked'.
    Raises ValueError on bad Claude response.
    """
    if _evolution_lock.locked():
        return {"status": "locked"}

    async with _evolution_lock:
        from backend.agents.feedback_loop import _profile_path, ALL_AGENTS

        path = _profile_path(client_id)
        if not path.exists():
            return {"status": "skipped", "reason": "no profile found"}

        profile = json.loads(path.read_text())
        total = profile.get("stats", {}).get("total_tournaments", 0)

        if total < MIN_TOURNAMENTS:
            return {
                "status": "skipped",
                "reason": f"insufficient data ({total}/{MIN_TOURNAMENTS} tournaments)",
            }

        win_rates = profile.get("stats", {}).get("win_rate_by_agent", {})
        if not win_rates or max(win_rates.values()) <= DOMINANCE_THRESHOLD:
            return {
                "status": "skipped",
                "reason": "no dominance detected",
                "win_rates": win_rates,
            }

        dominant_agent = max(win_rates, key=win_rates.get)
        dominant_rate = win_rates[dominant_agent]
        underperforming = [a for a in ALL_AGENTS if a != dominant_agent]

        # ── Call Claude ───────────────────────────────────────────────────────
        import anthropic

        prompt = _build_context_prompt(profile, underperforming, dominant_agent, dominant_rate)
        client = anthropic.Anthropic()
        message = client.messages.create(
            model=HARNESS_EVOLVER_MODEL,
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = message.content[0].text.strip()

        # ── Parse JSON response ───────────────────────────────────────────────
        try:
            proposed = json.loads(raw)
        except json.JSONDecodeError:
            match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
            if match:
                proposed = json.loads(match.group(1))
            else:
                raise ValueError(f"Claude returned non-JSON response: {raw[:300]}")

        # Only apply keys that are valid underperforming agents
        valid_proposed = {
            k: v
            for k, v in proposed.items()
            if k in ALL_AGENTS and k != dominant_agent
        }
        if not valid_proposed:
            raise ValueError(f"Claude returned no valid agent keys: {list(proposed.keys())}")

        # ── Apply to tournament.py ────────────────────────────────────────────
        source = TOURNAMENT_PY.read_text()
        for agent_name, new_prompt in valid_proposed.items():
            source = _replace_prompt_in_source(source, agent_name, new_prompt)
        TOURNAMENT_PY.write_text(source)

        # ── Git commit ────────────────────────────────────────────────────────
        gen = _get_generation_number() + 1
        agent_list = ",".join(valid_proposed.keys())
        commit_msg = (
            f"harness: gen-{gen} — evolved {agent_list} "
            f"(dominant: {dominant_agent} at {dominant_rate:.0%})"
        )
        commit_hash = None
        try:
            repo_root = TOURNAMENT_PY.parent.parent.parent
            subprocess.run(["git", "add", str(TOURNAMENT_PY)], cwd=repo_root, check=True)
            subprocess.run(["git", "commit", "-m", commit_msg], cwd=repo_root, check=True)
            result = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                capture_output=True,
                text=True,
                cwd=repo_root,
            )
            commit_hash = result.stdout.strip()
        except subprocess.CalledProcessError:
            # File is already written on disk — not fatal, return commit: null
            pass

        return {
            "status": "evolved",
            "generation": gen,
            "evolved_agents": list(valid_proposed.keys()),
            "dominant_agent": dominant_agent,
            "dominant_win_rate": dominant_rate,
            "commit": commit_hash,
        }
```

- [ ] **Step 4: Run tests to confirm PASS**

```bash
cd "/Users/bevo/Library/CloudStorage/GoogleDrive-kroberts2007@gmail.com/My Drive/TakeoffAI"
uv run pytest tests/test_harness_evolver.py -v --tb=short 2>&1
```

Expected: All 7 tests PASS.

- [ ] **Step 5: Commit**

```bash
cd "/Users/bevo/Library/CloudStorage/GoogleDrive-kroberts2007@gmail.com/My Drive/TakeoffAI"
git add backend/agents/harness_evolver.py tests/test_harness_evolver.py
git commit -m "feat: add harness_evolver scaffold — skip logic, dominance check, regex rewriter"
```

---

## Task 2: `evolve_harness` — Claude call + file rewrite test

**Files:**
- Modify: `tests/test_harness_evolver.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_harness_evolver.py`:

```python
import shutil
from unittest.mock import patch, MagicMock


def test_evolve_harness_applies_proposed_prompts(tmp_path, monkeypatch):
    """evolve_harness rewrites tournament.py with Claude's proposed prompt."""
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

    with patch("anthropic.Anthropic") as mock_cls:
        mock_cls.return_value.messages.create.return_value = mock_msg
        monkeypatch.setattr(ev, "_get_generation_number", lambda: 0)
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="abc1234\n", returncode=0)
            result = asyncio.run(ev.evolve_harness("default"))

    assert result["status"] == "evolved"
    assert "conservative" in result["evolved_agents"]
    assert "aggressive" not in result["evolved_agents"]
    assert result["dominant_agent"] == "aggressive"
    assert "EVOLVED CONTENT" in fake_tourn.read_text()


def test_evolve_harness_returns_locked_when_already_running(tmp_path, monkeypatch):
    """evolve_harness returns locked immediately if lock is held."""
    import backend.agents.harness_evolver as ev

    async def run():
        # Acquire lock manually then call evolve_harness
        async with ev._evolution_lock:
            return await ev.evolve_harness("any_client")

    result = asyncio.run(run())
    assert result["status"] == "locked"


def test_evolve_harness_handles_markdown_wrapped_json(tmp_path, monkeypatch):
    """Claude sometimes wraps JSON in ```json ... ``` — parser handles it."""
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
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text=wrapped)]

    with patch("anthropic.Anthropic") as mock_cls:
        mock_cls.return_value.messages.create.return_value = mock_msg
        monkeypatch.setattr(ev, "_get_generation_number", lambda: 2)
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="def5678\n", returncode=0)
            result = asyncio.run(ev.evolve_harness("md_client"))

    assert result["status"] == "evolved"
    assert "FROM MARKDOWN" in fake_tourn.read_text()
```

- [ ] **Step 2: Run to confirm FAIL**

```bash
cd "/Users/bevo/Library/CloudStorage/GoogleDrive-kroberts2007@gmail.com/My Drive/TakeoffAI"
uv run pytest tests/test_harness_evolver.py -v -k "applies_proposed or locked or markdown" --tb=short 2>&1
```

Expected: FAIL — `ModuleNotFoundError: No module named 'anthropic'` or similar (Claude call not yet mocked fully until the module exists with the import).

- [ ] **Step 3: Run the full test file to confirm new tests fail for the right reason**

The scaffold from Task 1 is already there. The new tests fail because `anthropic` is not importable until the mock is in place — but with the mock patched in the test itself, the import failure happens at `import backend.agents.harness_evolver` which imports `anthropic` only inside the function body (lazy import in `evolve_harness`). Verify the tests fail with the expected assertion failures, not import errors.

If `anthropic` is not installed:
```bash
cd "/Users/bevo/Library/CloudStorage/GoogleDrive-kroberts2007@gmail.com/My Drive/TakeoffAI"
uv add anthropic
```

- [ ] **Step 4: Run all harness tests to confirm PASS**

```bash
cd "/Users/bevo/Library/CloudStorage/GoogleDrive-kroberts2007@gmail.com/My Drive/TakeoffAI"
uv run pytest tests/test_harness_evolver.py -v --tb=short 2>&1
```

Expected: All 10 tests PASS.

- [ ] **Step 5: Commit**

```bash
cd "/Users/bevo/Library/CloudStorage/GoogleDrive-kroberts2007@gmail.com/My Drive/TakeoffAI"
git add tests/test_harness_evolver.py
git commit -m "test: add Claude call + file rewrite + lock tests for harness evolver"
```

---

## Task 3: Wire auto-trigger into `judge.py`

**Files:**
- Modify: `backend/agents/judge.py`
- Modify: `tests/test_harness_evolver.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_harness_evolver.py`:

```python
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
                "conservative": 0.07, "balanced": 0.07, "aggressive": 0.72,
                "historical_match": 0.07, "market_beater": 0.07,
            },
            "avg_winning_bid": 0.0, "avg_winning_margin": 0.0,
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
            tasks_created.append(coro.__qualname__ if hasattr(coro, '__qualname__') else str(coro))
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
```

- [ ] **Step 2: Run to confirm FAIL**

```bash
cd "/Users/bevo/Library/CloudStorage/GoogleDrive-kroberts2007@gmail.com/My Drive/TakeoffAI"
uv run pytest tests/test_harness_evolver.py::test_judge_tournament_fires_evolution_when_dominant -v --tb=short 2>&1
```

Expected: FAIL — evolve_harness task not created yet.

- [ ] **Step 3: Modify `backend/agents/judge.py`**

Find the block at line ~162:

```python
    # ── Trigger feedback loop outside DB context ──────────────────────────────
    if client_id and winner_entry:
        from backend.agents.feedback_loop import update_client_profile
        await asyncio.to_thread(update_client_profile, client_id, winner_entry)
```

Add after it (before the background price verification block):

```python
    # ── Auto-evolve harness if one agent is dominating ────────────────────────
    if client_id and winner_entry:
        from backend.agents.harness_evolver import check_dominance, evolve_harness
        if check_dominance(client_id):
            asyncio.create_task(evolve_harness(client_id))
```

- [ ] **Step 4: Run tests to confirm PASS**

```bash
cd "/Users/bevo/Library/CloudStorage/GoogleDrive-kroberts2007@gmail.com/My Drive/TakeoffAI"
uv run pytest tests/test_harness_evolver.py -v --tb=short 2>&1
```

Expected: All 11 tests PASS.

- [ ] **Step 5: Run full test suite**

```bash
cd "/Users/bevo/Library/CloudStorage/GoogleDrive-kroberts2007@gmail.com/My Drive/TakeoffAI"
uv run pytest tests/ -v --tb=short 2>&1
```

Expected: All PASS.

- [ ] **Step 6: Commit**

```bash
cd "/Users/bevo/Library/CloudStorage/GoogleDrive-kroberts2007@gmail.com/My Drive/TakeoffAI"
git add backend/agents/judge.py tests/test_harness_evolver.py
git commit -m "feat: fire evolve_harness background task from judge when agent dominates"
```

---

## Task 4: Add `POST /api/tournament/evolve` endpoint

**Files:**
- Modify: `backend/api/routes.py`
- Modify: `tests/test_harness_evolver.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_harness_evolver.py`:

```python
def test_post_evolve_returns_skipped_when_no_dominance(tmp_path, monkeypatch):
    """POST /api/tournament/evolve returns skipped when win rates are balanced."""
    import backend.agents.feedback_loop as fl
    import backend.agents.harness_evolver as ev
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.api.routes import router

    monkeypatch.setattr(fl, "PROFILES_DIR", tmp_path)

    profile = {
        "client_id": "balanced_client",
        "winning_examples": [],
        "stats": {
            "total_tournaments": 20,
            "win_rate_by_agent": {
                "conservative": 0.22, "balanced": 0.20, "aggressive": 0.20,
                "historical_match": 0.19, "market_beater": 0.19,
            },
            "avg_winning_bid": 0.0, "avg_winning_margin": 0.0, "wins_by_agent": {},
        },
    }
    (tmp_path / "balanced_client.json").write_text(json.dumps(profile))

    app = FastAPI()
    app.include_router(router, prefix="/api")
    with TestClient(app) as c:
        resp = c.post("/api/tournament/evolve", json={"client_id": "balanced_client"})

    assert resp.status_code == 200
    assert resp.json()["status"] == "skipped"


def test_post_evolve_returns_423_when_locked(monkeypatch):
    """POST /api/tournament/evolve returns 423 when evolution is in progress."""
    import backend.agents.harness_evolver as ev
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.api.routes import router

    app = FastAPI()
    app.include_router(router, prefix="/api")

    async def run_while_locked():
        async with ev._evolution_lock:
            with TestClient(app) as c:
                return c.post("/api/tournament/evolve", json={"client_id": "any"})

    resp = asyncio.run(run_while_locked())
    assert resp.status_code == 423


def test_post_evolve_returns_evolved_on_success(tmp_path, monkeypatch):
    """POST /api/tournament/evolve returns evolved result when Claude succeeds."""
    import shutil
    import backend.agents.feedback_loop as fl
    import backend.agents.harness_evolver as ev
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.api.routes import router

    monkeypatch.setattr(fl, "PROFILES_DIR", tmp_path)
    fake_tourn = tmp_path / "tournament.py"
    shutil.copy(ev.TOURNAMENT_PY, fake_tourn)
    monkeypatch.setattr(ev, "TOURNAMENT_PY", fake_tourn)

    profile = {
        "client_id": "evolve_client",
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
    (tmp_path / "evolve_client.json").write_text(json.dumps(profile))

    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(
        text='{"conservative": "## BIDDING PERSONALITY: CONSERVATIVE\\nEP TEST\\n"}'
    )]

    with patch("anthropic.Anthropic") as mock_cls:
        mock_cls.return_value.messages.create.return_value = mock_msg
        monkeypatch.setattr(ev, "_get_generation_number", lambda: 1)
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="fff9999\n", returncode=0)
            app = FastAPI()
            app.include_router(router, prefix="/api")
            with TestClient(app) as c:
                resp = c.post("/api/tournament/evolve", json={"client_id": "evolve_client"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "evolved"
    assert "conservative" in data["evolved_agents"]
    assert data["generation"] == 2
```

- [ ] **Step 2: Run to confirm FAIL**

```bash
cd "/Users/bevo/Library/CloudStorage/GoogleDrive-kroberts2007@gmail.com/My Drive/TakeoffAI"
uv run pytest tests/test_harness_evolver.py -v -k "post_evolve" --tb=short 2>&1
```

Expected: FAIL — `404 Not Found` (endpoint not defined yet).

- [ ] **Step 3: Add endpoint to `backend/api/routes.py`**

Add this import after the existing feedback_loop import:

```python
from backend.agents.harness_evolver import evolve_harness as _evolve_harness, _evolution_lock
```

Add this model and endpoint after the `reset_agent_history_endpoint` at the end of the file:

```python
class EvolveRequest(BaseModel):
    client_id: str = Field(default="default", description="Client ID to use as diagnostic context")


@router.post("/tournament/evolve")
async def evolve_harness_endpoint(req: EvolveRequest):
    """
    Manually trigger harness evolution. Analyzes client tournament history,
    evolves underperforming agent prompts via Claude, and commits to git.
    Returns 423 if evolution is already in progress.
    """
    if _evolution_lock.locked():
        raise HTTPException(status_code=423, detail="Evolution already in progress")
    try:
        result = await _evolve_harness(req.client_id)
        if result.get("status") == "locked":
            raise HTTPException(status_code=423, detail="Evolution already in progress")
        return result
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
```

- [ ] **Step 4: Run all tests to confirm PASS**

```bash
cd "/Users/bevo/Library/CloudStorage/GoogleDrive-kroberts2007@gmail.com/My Drive/TakeoffAI"
uv run pytest tests/test_harness_evolver.py -v --tb=short 2>&1
```

Expected: All 14 tests PASS.

- [ ] **Step 5: Run full test suite**

```bash
cd "/Users/bevo/Library/CloudStorage/GoogleDrive-kroberts2007@gmail.com/My Drive/TakeoffAI"
uv run pytest tests/ -v --tb=short 2>&1
```

Expected: All PASS.

- [ ] **Step 6: Commit**

```bash
cd "/Users/bevo/Library/CloudStorage/GoogleDrive-kroberts2007@gmail.com/My Drive/TakeoffAI"
git add backend/api/routes.py tests/test_harness_evolver.py
git commit -m "feat: add POST /api/tournament/evolve manual trigger endpoint"
```

---

## Task 5: Smoke test

**Files:** No changes

- [ ] **Step 1: Start the server**

```bash
cd "/Users/bevo/Library/CloudStorage/GoogleDrive-kroberts2007@gmail.com/My Drive/TakeoffAI"
uv run uvicorn backend.api.main:app --port 8000 &
sleep 2
```

- [ ] **Step 2: Test the endpoint with insufficient data**

```bash
curl -s -X POST http://localhost:8000/api/tournament/evolve \
  -H "Content-Type: application/json" \
  -d '{"client_id": "default"}' | python3 -m json.tool
```

Expected:
```json
{
  "status": "skipped",
  "reason": "no profile found"
}
```
(or `"insufficient data"` if a real profile exists with < 10 tournaments)

- [ ] **Step 3: Kill server**

```bash
kill %1
```

- [ ] **Step 4: Final full test suite**

```bash
cd "/Users/bevo/Library/CloudStorage/GoogleDrive-kroberts2007@gmail.com/My Drive/TakeoffAI"
uv run pytest tests/ -v --tb=short 2>&1
```

Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
cd "/Users/bevo/Library/CloudStorage/GoogleDrive-kroberts2007@gmail.com/My Drive/TakeoffAI"
git add -A
git commit -m "chore: harness evolution smoke test complete"
```

---

## Self-Review

**Spec coverage:**
- ✓ Auto-replace (no human review gate) — `evolve_harness` writes and commits directly
- ✓ Global prompts — `tournament.py` is shared by all clients
- ✓ Fork/rollback — git commits with `harness: gen-N` messages; rollback via `git checkout <hash> -- tournament.py`
- ✓ Versioning — `_get_generation_number()` counts prior commits; each evolution increments
- ✓ Manual trigger — `POST /api/tournament/evolve`
- ✓ Auto-trigger — `check_dominance` called from `judge.py` after every judgment
- ✓ Trigger condition B — one agent win rate > 60% with ≥ 10 tournaments
- ✓ Surgical — only underperforming agents rewritten; dominant agent untouched
- ✓ Lock — `_evolution_lock` prevents concurrent evolutions; 423 returned to caller
- ✓ Error handling — bad JSON, unknown keys, git failures all handled per spec
- ✓ Model configurable — `HARNESS_EVOLVER_MODEL` env var, defaults to `claude-sonnet-4-6`
- ✓ Tests cover all paths listed in spec
