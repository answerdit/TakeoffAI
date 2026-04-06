# Agentic Trace Proposer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade the harness evolver from a single-shot summarized-context call to a full agentic loop that navigates raw per-tournament trace files using tool calls, matching the Meta-Harness paper's core diagnostic insight.

**Architecture:** `tournament.py:_save_entries` writes a JSON trace file per agent per tournament alongside the existing DB insert. `harness_evolver.py` gains two tool handlers (`_handle_list_traces`, `_handle_read_file`) and an agentic loop (`_run_agentic_proposer`) that replaces the current `_call_claude_sync`. `evolve_harness` wires in the new proposer; everything else (lock, dominance check, regex rewrite, git commit) is untouched.

**Tech Stack:** Python/FastAPI, Anthropic SDK (`anthropic`), `pathlib`, pytest

---

## File Map

| File | Change | Responsibility |
|---|---|---|
| `backend/agents/tournament.py` | MODIFY | `_save_entries` writes trace files after DB insert |
| `backend/agents/harness_evolver.py` | MODIFY | Add `HARNESS_EVOLVER_MAX_TOOL_CALLS`, `_TOOLS`, `_handle_list_traces`, `_handle_read_file`, `_run_agentic_proposer`; remove `_build_context_prompt` and `_call_claude_sync`; wire new proposer into `evolve_harness` |
| `tests/test_agentic_trace_proposer.py` | CREATE | All tests for this feature |

---

## Task 1: Trace file persistence in `tournament.py`

**Files:**
- Modify: `backend/agents/tournament.py`
- Create: `tests/test_agentic_trace_proposer.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_agentic_trace_proposer.py`:

```python
import asyncio
import json
import pytest
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock


# ── Trace persistence tests ────────────────────────────────────────────────────

def test_trace_files_written_after_tournament(tmp_path, monkeypatch):
    """_save_entries writes one trace file per agent under data/traces/{tournament_id}/."""
    import aiosqlite
    from backend.agents.tournament import _save_entries, AgentResult
    from backend.api.main import _CREATE_TABLES

    # Patch the traces dir to tmp_path
    import backend.agents.tournament as tm
    monkeypatch.setattr(tm, "DB_PATH", str(tmp_path / "test.db"))

    # Monkeypatch Path so trace files land in tmp_path
    original_file = tm.Path(__file__)  # unused, just confirming import
    traces_root = tmp_path / "data" / "traces"

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

    # Patch TRACES_DIR so files land in tmp_path
    import backend.agents.tournament as tm_mod
    monkeypatch.setattr(tm_mod, "TRACES_DIR", traces_root)

    async def run():
        async with aiosqlite.connect(str(tmp_path / "test.db")) as db:
            await db.executescript(_CREATE_TABLES)
            await _save_entries(
                db, 42, results,
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
    from backend.agents.tournament import _save_entries, AgentResult
    from backend.api.main import _CREATE_TABLES
    import backend.agents.tournament as tm_mod

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
    from backend.agents.tournament import _save_entries, AgentResult
    from backend.api.main import _CREATE_TABLES
    import backend.agents.tournament as tm_mod

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
                db, 1, results,
                client_id="c1", description="test", zip_code="76801",
            )
            cursor = await db.execute("SELECT COUNT(*) FROM tournament_entries")
            row = await cursor.fetchone()
            return row[0]

    count = asyncio.run(run())
    assert count == 1  # DB write succeeded despite trace failure
```

- [ ] **Step 2: Run to confirm FAIL**

```bash
cd "/Users/bevo/Library/CloudStorage/GoogleDrive-kroberts2007@gmail.com/My Drive/TakeoffAI"
uv run pytest tests/test_agentic_trace_proposer.py -v --tb=short 2>&1
```

Expected: `ImportError` or `AttributeError: module 'backend.agents.tournament' has no attribute 'TRACES_DIR'`

- [ ] **Step 3: Modify `backend/agents/tournament.py`**

Add `TRACES_DIR` constant after `DB_PATH` (around line 16):

```python
TRACES_DIR = Path(__file__).parent.parent / "data" / "traces"
```

Update `_save_entries` signature and body. Replace the entire function:

```python
async def _save_entries(
    db: aiosqlite.Connection,
    tournament_id: int,
    results: list[AgentResult],
    client_id: Optional[str] = None,
    description: str = "",
    zip_code: str = "",
) -> None:
    import logging
    from datetime import datetime, timezone

    for result in results:
        await db.execute(
            """INSERT INTO tournament_entries
               (tournament_id, agent_name, total_bid, line_items_json, won, score)
               VALUES (?, ?, ?, ?, 0, NULL)""",
            (
                tournament_id,
                result.agent_name,
                result.total_bid,
                json.dumps(result.estimate),
            ),
        )
    await db.commit()

    # Write trace files — best-effort, must not break tournament
    if client_id:
        logger = logging.getLogger(__name__)
        trace_dir = TRACES_DIR / str(tournament_id)
        try:
            trace_dir.mkdir(parents=True, exist_ok=True)
            for result in results:
                trace = {
                    "tournament_id": tournament_id,
                    "agent_name": result.agent_name,
                    "client_id": client_id,
                    "project_description": description,
                    "zip_code": zip_code,
                    "won": False,
                    "score": None,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "estimate": result.estimate,
                }
                (trace_dir / f"{result.agent_name}.json").write_text(
                    json.dumps(trace, indent=2)
                )
        except Exception as exc:
            logger.warning(
                "Failed to write trace files for tournament %s: %s", tournament_id, exc
            )
```

Update the call site in `run_tournament` (replace the `_save_entries` call ~line 210):

```python
        await _save_entries(db, tournament_id, results, client_id, description, zip_code)
```

- [ ] **Step 4: Run tests to confirm PASS**

```bash
cd "/Users/bevo/Library/CloudStorage/GoogleDrive-kroberts2007@gmail.com/My Drive/TakeoffAI"
uv run pytest tests/test_agentic_trace_proposer.py -v --tb=short 2>&1
```

Expected: All 3 tests PASS.

- [ ] **Step 5: Run full suite to confirm nothing broken**

```bash
cd "/Users/bevo/Library/CloudStorage/GoogleDrive-kroberts2007@gmail.com/My Drive/TakeoffAI"
uv run pytest tests/ -v --tb=short 2>&1
```

Expected: All PASS.

- [ ] **Step 6: Commit**

```bash
cd "/Users/bevo/Library/CloudStorage/GoogleDrive-kroberts2007@gmail.com/My Drive/TakeoffAI"
git add backend/agents/tournament.py tests/test_agentic_trace_proposer.py
git commit -m "feat: write per-agent trace files in _save_entries"
```

---

## Task 2: Tool handlers in `harness_evolver.py`

**Files:**
- Modify: `backend/agents/harness_evolver.py`
- Modify: `tests/test_agentic_trace_proposer.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_agentic_trace_proposer.py`:

```python
# ── Tool handler tests ─────────────────────────────────────────────────────────

def test_list_traces_returns_files_for_client(tmp_path):
    """_handle_list_traces returns trace metadata for matching client_id."""
    import backend.agents.harness_evolver as ev

    # Write two trace files for "client_a" and one for "client_b"
    for tid, agent, cid in [(1, "aggressive", "client_a"), (1, "conservative", "client_a"), (2, "balanced", "client_b")]:
        d = tmp_path / str(tid)
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{agent}.json").write_text(json.dumps({
            "tournament_id": tid,
            "agent_name": agent,
            "client_id": cid,
            "timestamp": "2026-03-31T10:00:00+00:00",
            "estimate": {"total_bid": 100000.0},
        }))

    results = ev._handle_list_traces(tmp_path, client_id="client_a")
    assert len(results) == 2
    agent_names = {r["agent_name"] for r in results}
    assert agent_names == {"aggressive", "conservative"}
    # Each result has expected keys
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
        (d / f"{agent}.json").write_text(json.dumps({
            "tournament_id": 5,
            "agent_name": agent,
            "client_id": "c1",
            "timestamp": "2026-03-31T10:00:00+00:00",
            "estimate": {"total_bid": 50000.0},
        }))

    results = ev._handle_list_traces(tmp_path, client_id="c1", agent_name="aggressive")
    assert len(results) == 1
    assert results[0]["agent_name"] == "aggressive"


def test_list_traces_respects_limit(tmp_path):
    """_handle_list_traces returns at most `limit` results."""
    import backend.agents.harness_evolver as ev

    for i in range(10):
        d = tmp_path / str(i)
        d.mkdir(parents=True, exist_ok=True)
        (d / "aggressive.json").write_text(json.dumps({
            "tournament_id": i,
            "agent_name": "aggressive",
            "client_id": "c1",
            "timestamp": "2026-03-31T10:00:00+00:00",
            "estimate": {"total_bid": float(i * 1000)},
        }))

    results = ev._handle_list_traces(tmp_path, client_id="c1", limit=3)
    assert len(results) == 3


def test_read_file_returns_content_within_data_dir(tmp_path):
    """_handle_read_file returns parsed JSON for files inside data_dir."""
    import backend.agents.harness_evolver as ev

    f = tmp_path / "test.json"
    f.write_text(json.dumps({"key": "value"}))

    result = ev._handle_read_file(tmp_path, str(f))
    assert result == {"key": "value"}


def test_read_file_blocks_paths_outside_data_dir(tmp_path):
    """_handle_read_file returns error dict for paths outside data_dir."""
    import backend.agents.harness_evolver as ev

    # Try to read a file outside tmp_path
    outside = tmp_path.parent / "secrets.txt"
    outside.write_text("secret")

    result = ev._handle_read_file(tmp_path, str(outside))
    assert "error" in result
    assert "Access denied" in result["error"]


def test_read_file_returns_error_for_missing_file(tmp_path):
    """_handle_read_file returns error dict when file does not exist."""
    import backend.agents.harness_evolver as ev

    result = ev._handle_read_file(tmp_path, str(tmp_path / "nonexistent.json"))
    assert "error" in result
```

- [ ] **Step 2: Run to confirm FAIL**

```bash
cd "/Users/bevo/Library/CloudStorage/GoogleDrive-kroberts2007@gmail.com/My Drive/TakeoffAI"
uv run pytest tests/test_agentic_trace_proposer.py -v -k "list_traces or read_file" --tb=short 2>&1
```

Expected: `AttributeError: module 'backend.agents.harness_evolver' has no attribute '_handle_list_traces'`

- [ ] **Step 3: Add to `backend/agents/harness_evolver.py`**

Add after the existing constants (after `HARNESS_EVOLVER_MODEL` line 17), before `TOURNAMENT_PY`:

```python
HARNESS_EVOLVER_MAX_TOOL_CALLS = int(os.getenv("HARNESS_EVOLVER_MAX_TOOL_CALLS", "30"))
```

Add after the `_git_commit` function and before `evolve_harness`, replacing `_build_context_prompt` and `_call_claude_sync` entirely. Delete those two functions and insert:

```python
# ── Agentic proposer tools ────────────────────────────────────────────────────

_TOOLS = [
    {
        "name": "list_traces",
        "description": (
            "List available trace files for a client. Returns file paths with metadata "
            "(agent_name, tournament_id, total_bid, timestamp). Use to find which "
            "tournaments to investigate."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "client_id": {"type": "string"},
                "agent_name": {
                    "type": "string",
                    "description": "Filter by agent name (optional)",
                },
                "limit": {"type": "integer", "default": 50},
            },
            "required": ["client_id"],
        },
    },
    {
        "name": "read_file",
        "description": (
            "Read a file from the data directory. Use to read trace files or the client "
            "profile. Path must be under backend/data/."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute path. Must be under backend/data/.",
                }
            },
            "required": ["path"],
        },
    },
]


def _handle_list_traces(
    data_dir: Path,
    client_id: str,
    agent_name: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """Return metadata list for trace files matching client_id (and optionally agent_name)."""
    pattern = f"*/{agent_name or '*'}.json"
    files = sorted(
        data_dir.glob(f"traces/{pattern}"),
        key=lambda p: int(p.parent.name) if p.parent.name.isdigit() else 0,
        reverse=True,
    )
    results = []
    for f in files:
        try:
            meta = json.loads(f.read_text())
            if meta.get("client_id") != client_id:
                continue
            results.append({
                "path": str(f),
                "agent_name": meta.get("agent_name"),
                "tournament_id": meta.get("tournament_id"),
                "total_bid": meta.get("estimate", {}).get("total_bid"),
                "timestamp": meta.get("timestamp"),
            })
            if len(results) >= limit:
                break
        except Exception:
            continue
    return results


def _handle_read_file(data_dir: Path, path: str) -> dict:
    """Read a file inside data_dir. Returns error dict if path is outside or missing."""
    try:
        target = Path(path).resolve()
        allowed = data_dir.resolve()
        if not str(target).startswith(str(allowed)):
            return {"error": f"Access denied: path must be under {allowed}"}
        content = target.read_text()
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return {"content": content}
    except FileNotFoundError:
        return {"error": f"File not found: {path}"}
    except Exception as exc:
        return {"error": str(exc)}
```

- [ ] **Step 4: Run tests to confirm PASS**

```bash
cd "/Users/bevo/Library/CloudStorage/GoogleDrive-kroberts2007@gmail.com/My Drive/TakeoffAI"
uv run pytest tests/test_agentic_trace_proposer.py -v --tb=short 2>&1
```

Expected: All 9 tests PASS (3 from Task 1 + 6 new).

- [ ] **Step 5: Run full suite**

```bash
cd "/Users/bevo/Library/CloudStorage/GoogleDrive-kroberts2007@gmail.com/My Drive/TakeoffAI"
uv run pytest tests/ -v --tb=short 2>&1
```

Expected: All PASS.

- [ ] **Step 6: Commit**

```bash
cd "/Users/bevo/Library/CloudStorage/GoogleDrive-kroberts2007@gmail.com/My Drive/TakeoffAI"
git add backend/agents/harness_evolver.py tests/test_agentic_trace_proposer.py
git commit -m "feat: add tool handlers + MAX_TOOL_CALLS config to harness_evolver"
```

---

## Task 3: Agentic proposer loop + wire into `evolve_harness`

**Files:**
- Modify: `backend/agents/harness_evolver.py`
- Modify: `tests/test_agentic_trace_proposer.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_agentic_trace_proposer.py`:

```python
# ── Agentic loop tests ─────────────────────────────────────────────────────────

def test_agentic_proposer_multi_turn_tool_loop(tmp_path, monkeypatch):
    """_run_agentic_proposer processes multi-turn tool calls and returns proposed JSON."""
    import backend.agents.harness_evolver as ev

    # Sequence of responses: list_traces → read_file → end_turn with JSON
    list_traces_response = MagicMock()
    list_traces_response.stop_reason = "tool_use"
    list_traces_response.content = [
        MagicMock(
            type="tool_use",
            id="tu1",
            name="list_traces",
            input={"client_id": "c1"},
        )
    ]

    read_file_response = MagicMock()
    read_file_response.stop_reason = "tool_use"
    read_file_response.content = [
        MagicMock(
            type="tool_use",
            id="tu2",
            name="read_file",
            input={"path": str(tmp_path / "traces" / "1" / "aggressive.json")},
        )
    ]

    final_response = MagicMock()
    final_response.stop_reason = "end_turn"
    final_response.content = [
        MagicMock(
            type="text",
            text='{"conservative": "## BIDDING PERSONALITY: CONSERVATIVE\\nNEW CONTENT\\n"}',
            spec=["type", "text"],
        )
    ]
    # hasattr(block, "text") must return True
    type(final_response.content[0]).text = MagicMock()
    final_response.content[0].text = '{"conservative": "## BIDDING PERSONALITY: CONSERVATIVE\\nNEW CONTENT\\n"}'

    call_count = [0]

    def mock_create(**kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            return list_traces_response
        elif call_count[0] == 2:
            return read_file_response
        else:
            return final_response

    with patch("anthropic.Anthropic") as mock_cls:
        mock_cls.return_value.messages.create.side_effect = mock_create
        monkeypatch.setattr(ev, "_handle_list_traces", lambda data_dir, **kw: [])
        monkeypatch.setattr(ev, "_handle_read_file", lambda data_dir, path: {"agent_name": "aggressive"})

        result = ev._run_agentic_proposer(
            data_dir=tmp_path,
            client_id="c1",
            underperforming=["conservative", "balanced"],
            dominant_agent="aggressive",
            dominant_rate=0.72,
            profile_path=tmp_path / "c1.json",
        )

    assert '"conservative"' in result
    assert "NEW CONTENT" in result
    assert call_count[0] == 3


def test_agentic_proposer_soft_cap_forces_proposal(tmp_path, monkeypatch):
    """When tool calls exceed MAX_TOOL_CALLS, a forcing message is injected."""
    import backend.agents.harness_evolver as ev

    monkeypatch.setattr(ev, "HARNESS_EVOLVER_MAX_TOOL_CALLS", 2)

    tool_response = MagicMock()
    tool_response.stop_reason = "tool_use"
    tool_response.content = [
        MagicMock(type="tool_use", id="tu", name="list_traces", input={"client_id": "c1"})
    ]

    forced_response = MagicMock()
    forced_response.stop_reason = "end_turn"
    forced_response.content = [
        MagicMock(type="text", text='{"balanced": "## BIDDING PERSONALITY: BALANCED\\nFORCED\\n"}')
    ]
    forced_response.content[0].text = '{"balanced": "## BIDDING PERSONALITY: BALANCED\\nFORCED\\n"}'

    responses = [tool_response, tool_response, forced_response]
    call_count = [0]

    def mock_create(**kwargs):
        resp = responses[min(call_count[0], len(responses) - 1)]
        call_count[0] += 1
        return resp

    injected_messages = []

    def capture_create(**kwargs):
        messages = kwargs.get("messages", [])
        if any(
            isinstance(m.get("content"), str) and "enough context" in m["content"]
            for m in messages
            if isinstance(m, dict)
        ):
            injected_messages.append(True)
        return mock_create(**kwargs)

    with patch("anthropic.Anthropic") as mock_cls:
        mock_cls.return_value.messages.create.side_effect = capture_create
        monkeypatch.setattr(ev, "_handle_list_traces", lambda data_dir, **kw: [])

        result = ev._run_agentic_proposer(
            data_dir=tmp_path,
            client_id="c1",
            underperforming=["balanced"],
            dominant_agent="aggressive",
            dominant_rate=0.72,
            profile_path=tmp_path / "c1.json",
        )

    assert "FORCED" in result
    assert len(injected_messages) > 0


def test_evolve_harness_uses_agentic_proposer(tmp_path, monkeypatch):
    """evolve_harness calls _run_agentic_proposer (not _call_claude_sync) and rewrites tournament.py."""
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
                "conservative": 0.07, "balanced": 0.07, "aggressive": 0.72,
                "historical_match": 0.07, "market_beater": 0.07,
            },
            "avg_winning_bid": 0.0, "avg_winning_margin": 0.0, "wins_by_agent": {},
        },
    }
    (tmp_path / "evo_client.json").write_text(json.dumps(profile))

    # Mock the agentic proposer to return proposed JSON directly
    monkeypatch.setattr(
        ev,
        "_run_agentic_proposer",
        lambda **kw: '{"conservative": "## BIDDING PERSONALITY: CONSERVATIVE\\nAGENTIC CONTENT\\n"}',
    )
    monkeypatch.setattr(ev, "_get_generation_number", lambda: 0)
    with patch("backend.agents.harness_evolver._git_commit") as mock_git:
        mock_git.return_value = "abc1234"
        result = asyncio.run(ev.evolve_harness("evo_client"))

    assert result["status"] == "evolved"
    assert "AGENTIC CONTENT" in fake_tourn.read_text()
    assert result["dominant_agent"] == "aggressive"
```

- [ ] **Step 2: Run to confirm FAIL**

```bash
cd "/Users/bevo/Library/CloudStorage/GoogleDrive-kroberts2007@gmail.com/My Drive/TakeoffAI"
uv run pytest tests/test_agentic_trace_proposer.py -v -k "proposer or evolve_harness_uses" --tb=short 2>&1
```

Expected: `AttributeError: module 'backend.agents.harness_evolver' has no attribute '_run_agentic_proposer'`

- [ ] **Step 3: Add `_run_agentic_proposer` and wire into `evolve_harness`**

In `backend/agents/harness_evolver.py`, add `_run_agentic_proposer` after `_handle_read_file` and before `evolve_harness`:

```python
_SYSTEM_PROMPT = (
    "You are a harness optimization agent for a construction bidding AI system. "
    "The system runs 5 bidding personalities in parallel on each job. You must improve "
    "the underperforming personalities by finding concrete evidence of why they lose.\n\n"
    "Use list_traces to find relevant tournaments, read_file to examine bid breakdowns "
    "in detail, and read the client profile for aggregate win rates and history.\n\n"
    "When you have sufficient evidence, output ONLY a valid JSON object mapping "
    "agent name to new prompt string. Include only the agents you were asked to improve."
)


def _run_agentic_proposer(
    *,
    data_dir: Path,
    client_id: str,
    underperforming: list[str],
    dominant_agent: str,
    dominant_rate: float,
    profile_path: Path,
) -> str:
    """
    Run an agentic loop with file-reading tools. Claude navigates trace files to
    gather diagnostic evidence, then proposes improved personality prompts.
    Returns raw text from Claude's final response (JSON string, possibly markdown-wrapped).
    """
    import anthropic

    initial_message = (
        f"Client: {client_id}\n"
        f"Dominant agent: {dominant_agent} ({dominant_rate:.0%} win rate)\n"
        f"Agents to improve: {', '.join(underperforming)}\n\n"
        f"Client profile path: {profile_path}\n\n"
        "Investigate why the underperforming agents lose by reading trace files, "
        "then propose improved personality prompts."
    )

    client = anthropic.Anthropic()
    messages: list[dict] = [{"role": "user", "content": initial_message}]
    tool_call_count = 0

    while True:
        kwargs: dict = {
            "model": HARNESS_EVOLVER_MODEL,
            "max_tokens": 4096,
            "system": _SYSTEM_PROMPT,
            "messages": messages,
        }
        if tool_call_count < HARNESS_EVOLVER_MAX_TOOL_CALLS:
            kwargs["tools"] = _TOOLS

        response = client.messages.create(**kwargs)
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            for block in response.content:
                if hasattr(block, "text"):
                    return block.text.strip()
            raise ValueError("Agentic proposer returned no text in final response")

        if response.stop_reason == "tool_use":
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    tool_call_count += 1
                    if block.name == "list_traces":
                        result = _handle_list_traces(data_dir, **block.input)
                    elif block.name == "read_file":
                        result = _handle_read_file(data_dir, block.input["path"])
                    else:
                        result = {"error": f"Unknown tool: {block.name}"}

                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result),
                    })

            messages.append({"role": "user", "content": tool_results})

            if tool_call_count >= HARNESS_EVOLVER_MAX_TOOL_CALLS:
                messages.append({
                    "role": "user",
                    "content": (
                        "You have enough context. "
                        "Output your proposed prompts now as a JSON object."
                    ),
                })
```

Now update `evolve_harness` to replace the `_call_claude_sync` call. Find this block in `evolve_harness` (~line 202):

```python
        # ── Call Claude ───────────────────────────────────────────────────────
        prompt = _build_context_prompt(profile, underperforming, dominant_agent, dominant_rate)
        raw = await asyncio.to_thread(_call_claude_sync, prompt)
```

Replace it with:

```python
        # ── Call Claude (agentic loop) ────────────────────────────────────────
        from backend.agents.feedback_loop import _profile_path as _fp
        data_dir = TOURNAMENT_PY.parent.parent / "data"
        raw = await asyncio.to_thread(
            _run_agentic_proposer,
            data_dir=data_dir,
            client_id=client_id,
            underperforming=underperforming,
            dominant_agent=dominant_agent,
            dominant_rate=dominant_rate,
            profile_path=_fp(client_id),
        )
```

- [ ] **Step 4: Run tests to confirm PASS**

```bash
cd "/Users/bevo/Library/CloudStorage/GoogleDrive-kroberts2007@gmail.com/My Drive/TakeoffAI"
uv run pytest tests/test_agentic_trace_proposer.py -v --tb=short 2>&1
```

Expected: All 12 tests PASS.

- [ ] **Step 5: Run full suite**

```bash
cd "/Users/bevo/Library/CloudStorage/GoogleDrive-kroberts2007@gmail.com/My Drive/TakeoffAI"
uv run pytest tests/ -v --tb=short 2>&1
```

Expected: All PASS.

- [ ] **Step 6: Commit**

```bash
cd "/Users/bevo/Library/CloudStorage/GoogleDrive-kroberts2007@gmail.com/My Drive/TakeoffAI"
git add backend/agents/harness_evolver.py tests/test_agentic_trace_proposer.py
git commit -m "feat: replace single-shot proposer with agentic tool loop in harness_evolver"
```

---

## Self-Review

**Spec coverage:**
- ✓ Trace files written per agent per tournament at `data/traces/{id}/{agent}.json` — Task 1
- ✓ Trace write failure is best-effort (logged, does not raise) — Task 1 `_save_entries`
- ✓ `HARNESS_EVOLVER_MAX_TOOL_CALLS` env var with default 30 — Task 2
- ✓ `list_traces` tool: filters by client_id, agent_name, limit — Task 2 `_handle_list_traces`
- ✓ `read_file` tool: path-sandboxed to `backend/data/` — Task 2 `_handle_read_file`
- ✓ Full agentic loop (no iteration cap until MAX) — Task 3 `_run_agentic_proposer`
- ✓ Soft cap: inject forcing message + remove tools after MAX_TOOL_CALLS — Task 3
- ✓ `evolve_harness` wired to `_run_agentic_proposer` via `asyncio.to_thread` — Task 3
- ✓ `_build_context_prompt` and `_call_claude_sync` removed — Task 3
- ✓ No SQL, no new DB tables — entire spec
- ✓ All other `evolve_harness` behavior unchanged (lock, skip checks, regex rewrite, git commit)

**Placeholder scan:** None found.

**Type consistency:**
- `_handle_list_traces(data_dir: Path, client_id: str, agent_name: str | None, limit: int)` — consistent across Task 2 and Task 3 tests
- `_handle_read_file(data_dir: Path, path: str) -> dict` — consistent
- `_run_agentic_proposer(*, data_dir, client_id, underperforming, dominant_agent, dominant_rate, profile_path)` — keyword-only args match all test call sites
