# Temperature & Self-Consistency Ensemble Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expand the 5-agent tournament into a 5×3×N grid (personality × temperature × samples), collapse each personality to a median-consensus entry, and expose both raw and consensus results.

**Architecture:** Thread a `temperature` param down through `call_with_json_retry` → `run_prebid_calc_with_modifier` → `_run_single_agent`. The tournament builds a full grid and dispatches all cells in one `asyncio.gather`. A `_collapse_to_consensus` helper groups by personality and picks the entry closest to the median bid.

**Tech Stack:** Python 3.14, asyncio, anthropic AsyncAnthropic, aiosqlite, FastAPI/Pydantic, pytest-anyio

---

## File Map

| File | Change |
|------|--------|
| `backend/agents/utils.py` | Add `temperature: float = 0.7` to `call_with_json_retry` |
| `backend/agents/pre_bid_calc.py` | Add `temperature: float = 0.7` to `run_prebid_calc_with_modifier` |
| `backend/agents/tournament.py` | New fields on dataclasses, `_collapse_to_consensus`, grid loop, updated `_save_entries` |
| `backend/api/main.py` | Two new migration entries for `temperature` and `is_consensus` columns |
| `backend/api/routes.py` | `n_samples` field on `TournamentRunRequest`, `consensus_entries` in response |
| `tests/test_utils.py` | Test temperature kwarg passes through to `messages.create` |
| `tests/test_tournament.py` | New file — test collapse logic, grid shape, full `run_tournament` mock |
| `tests/test_routes.py` | Test `n_samples` field validation, `consensus_entries` in response |

---

## Task 1: Add `temperature` to `call_with_json_retry`

**Files:**
- Modify: `backend/agents/utils.py`
- Modify: `tests/test_utils.py`

- [ ] **Step 1: Write the failing test**

Open `tests/test_utils.py` and add at the bottom:

```python
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.anyio
async def test_call_with_json_retry_passes_temperature():
    """temperature kwarg must reach client.messages.create."""
    fake_response = MagicMock()
    fake_response.content = [MagicMock(text='{"result": 1}')]

    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(return_value=fake_response)

    from backend.agents.utils import call_with_json_retry

    await call_with_json_retry(
        mock_client,
        model="claude-sonnet-4-6",
        max_tokens=100,
        system="sys",
        messages=[{"role": "user", "content": "hi"}],
        temperature=0.3,
    )

    call_kwargs = mock_client.messages.create.call_args.kwargs
    assert call_kwargs["temperature"] == 0.3


@pytest.mark.anyio
async def test_call_with_json_retry_default_temperature():
    """Default temperature must be 0.7."""
    fake_response = MagicMock()
    fake_response.content = [MagicMock(text='{"result": 1}')]

    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(return_value=fake_response)

    from backend.agents.utils import call_with_json_retry

    await call_with_json_retry(
        mock_client,
        model="claude-sonnet-4-6",
        max_tokens=100,
        system="sys",
        messages=[{"role": "user", "content": "hi"}],
    )

    call_kwargs = mock_client.messages.create.call_args.kwargs
    assert call_kwargs["temperature"] == 0.7
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/bevo/Documents/answerD.it/TakeoffAI
uv run pytest tests/test_utils.py::test_call_with_json_retry_passes_temperature tests/test_utils.py::test_call_with_json_retry_default_temperature -v
```

Expected: FAIL — `call_with_json_retry` does not accept `temperature` yet.

- [ ] **Step 3: Add `temperature` param to `call_with_json_retry`**

In `backend/agents/utils.py`, update the function signature and the `client.messages.create` call:

```python
async def call_with_json_retry(
    client: AsyncAnthropic,
    *,
    model: str,
    max_tokens: int,
    system: str,
    messages: list,
    max_retries: int = 2,
    temperature: float = 0.7,
) -> dict:
```

And inside the function, update the `client.messages.create` call to include `temperature`:

```python
            response = await client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=conversation,
                temperature=temperature,
            )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_utils.py::test_call_with_json_retry_passes_temperature tests/test_utils.py::test_call_with_json_retry_default_temperature -v
```

Expected: PASS

- [ ] **Step 5: Run full test suite to check for regressions**

```bash
uv run pytest tests/ -v
```

Expected: All existing tests still pass.

- [ ] **Step 6: Commit**

```bash
git add backend/agents/utils.py tests/test_utils.py
git commit -m "feat: add temperature param to call_with_json_retry"
```

---

## Task 2: Thread `temperature` through `run_prebid_calc_with_modifier`

**Files:**
- Modify: `backend/agents/pre_bid_calc.py`

No new test file needed — `pre_bid_calc` tests will be covered via the tournament mock tests in Task 5. The integration is simple (one param passthrough).

- [ ] **Step 1: Add `temperature` param to `run_prebid_calc_with_modifier`**

In `backend/agents/pre_bid_calc.py`, update the signature and the `call_with_json_retry` call:

```python
async def run_prebid_calc_with_modifier(
    description: str,
    zip_code: str,
    trade_type: str = "general",
    overhead_pct: float = 20.0,
    margin_pct: float = 12.0,
    system_prompt_modifier: str | None = None,
    temperature: float = 0.7,
) -> dict:
    """
    Run PreBidCalc with an optional personality modifier appended to the system prompt.
    Used by the tournament engine to inject bidding-style instructions per agent.
    """
    system = SYSTEM_PROMPT
    if system_prompt_modifier:
        system = system + f"\n\n---\n\n{system_prompt_modifier}"

    user_message = f"""Project Description: {description}
Zip Code: {zip_code}
Trade Type: {trade_type}
Overhead %: {overhead_pct}
Target Margin %: {margin_pct}

Please generate a detailed line-item cost estimate for this project."""

    from backend.config import settings
    return await call_with_json_retry(
        client,
        model=settings.claude_model,
        max_tokens=8192,
        system=system,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": system,
                        "cache_control": {"type": "ephemeral"},
                    },
                    {
                        "type": "text",
                        "text": user_message,
                    },
                ],
            }
        ],
        temperature=temperature,
    )
```

`run_prebid_calc` (the no-modifier public entry point) does not need changes — it calls `run_prebid_calc_with_modifier` with `system_prompt_modifier=None` and will inherit the default `temperature=0.7`.

- [ ] **Step 2: Run full test suite**

```bash
uv run pytest tests/ -v
```

Expected: All tests pass.

- [ ] **Step 3: Commit**

```bash
git add backend/agents/pre_bid_calc.py
git commit -m "feat: thread temperature param through run_prebid_calc_with_modifier"
```

---

## Task 3: Update `AgentResult` and `TournamentResult` data structures

**Files:**
- Modify: `backend/agents/tournament.py`
- Create: `tests/test_tournament.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_tournament.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_tournament.py -v
```

Expected: FAIL — `AgentResult` has no `temperature` or `sample_index` fields; `TournamentResult` has no `consensus_entries`.

- [ ] **Step 3: Add fields to dataclasses in `tournament.py`**

In `backend/agents/tournament.py`, update the dataclasses:

```python
@dataclass
class AgentResult:
    agent_name: str
    estimate: dict
    total_bid: float
    margin_pct: float
    confidence: str
    temperature: float = 0.7
    sample_index: int = 0
    error: Optional[str] = None


@dataclass
class TournamentResult:
    tournament_id: int
    entries: list[AgentResult] = field(default_factory=list)
    consensus_entries: list[AgentResult] = field(default_factory=list)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_tournament.py -v
```

Expected: PASS

- [ ] **Step 5: Run full test suite**

```bash
uv run pytest tests/ -v
```

Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add backend/agents/tournament.py tests/test_tournament.py
git commit -m "feat: add temperature, sample_index to AgentResult; consensus_entries to TournamentResult"
```

---

## Task 4: Implement `_collapse_to_consensus`

**Files:**
- Modify: `backend/agents/tournament.py`
- Modify: `tests/test_tournament.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_tournament.py`:

```python
def test_collapse_picks_median_entry():
    """Should return the entry closest to the median total_bid per personality."""
    entries = [
        make_agent_result("conservative", 90_000.0, temperature=0.3, sample_index=0),
        make_agent_result("conservative", 100_000.0, temperature=0.7, sample_index=0),
        make_agent_result("conservative", 110_000.0, temperature=1.2, sample_index=0),
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_tournament.py::test_collapse_picks_median_entry tests/test_tournament.py::test_collapse_handles_multiple_personalities -v
```

Expected: FAIL — `_collapse_to_consensus` does not exist yet.

- [ ] **Step 3: Implement `_collapse_to_consensus` in `tournament.py`**

Add this function in `backend/agents/tournament.py` after the `_run_single_agent` function:

```python
def _collapse_to_consensus(results: list[AgentResult]) -> list[AgentResult]:
    """
    Collapse a flat list of AgentResults (from the personality×temperature×sample grid)
    into one consensus AgentResult per personality.

    Strategy: for each personality, take the entry whose total_bid is closest to
    the group median. Entries with errors or zero bids are excluded before collapsing.
    Personalities where all entries are invalid are omitted from the output.
    """
    from collections import defaultdict

    groups: dict[str, list[AgentResult]] = defaultdict(list)
    for r in results:
        if not r.error and r.total_bid > 0:
            groups[r.agent_name].append(r)

    consensus: list[AgentResult] = []
    for name, group in groups.items():
        bids = sorted(r.total_bid for r in group)
        n = len(bids)
        if n == 0:
            continue
        median_bid = bids[n // 2] if n % 2 == 1 else (bids[n // 2 - 1] + bids[n // 2]) / 2
        closest = min(group, key=lambda r: abs(r.total_bid - median_bid))
        consensus.append(closest)

    return consensus
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_tournament.py -v
```

Expected: All tournament tests PASS.

- [ ] **Step 5: Run full test suite**

```bash
uv run pytest tests/ -v
```

Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add backend/agents/tournament.py tests/test_tournament.py
git commit -m "feat: implement _collapse_to_consensus — median entry per personality"
```

---

## Task 5: Grid expansion in `run_tournament`

**Files:**
- Modify: `backend/agents/tournament.py`
- Modify: `tests/test_tournament.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_tournament.py`:

```python
import asyncio
from unittest.mock import AsyncMock, patch


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
        "backend.agents.tournament.settings.db_path",
        str(tmp_path / "test.db"),
    )

    # Bootstrap DB tables including ensemble columns
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
        "backend.agents.tournament.settings.db_path",
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
        "backend.agents.tournament.settings.db_path",
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
    assert temps == {0.3, 0.7, 1.2}
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_tournament.py::test_run_tournament_grid_shape -v
```

Expected: FAIL — `run_tournament` still dispatches 5 agents, not 15.

- [ ] **Step 3: Update `_run_single_agent` to accept `temperature` and `sample_index`**

In `backend/agents/tournament.py`, update `_run_single_agent`:

```python
async def _run_single_agent(
    agent_name: str,
    description: str,
    zip_code: str,
    trade_type: str,
    overhead_pct: float,
    margin_pct: float,
    system_prompt_modifier: str,
    temperature: float = 0.7,
    sample_index: int = 0,
) -> AgentResult:
    """Execute one PreBidCalc call directly (async)."""
    try:
        estimate = await run_prebid_calc_with_modifier(
            description,
            zip_code,
            trade_type,
            overhead_pct,
            margin_pct,
            system_prompt_modifier,
            temperature=temperature,
        )
        return AgentResult(
            agent_name=agent_name,
            estimate=estimate,
            total_bid=float(estimate.get("total_bid", 0.0)),
            margin_pct=float(estimate.get("margin_pct", margin_pct)),
            confidence=estimate.get("confidence", "medium"),
            temperature=temperature,
            sample_index=sample_index,
        )
    except Exception as exc:
        return AgentResult(
            agent_name=agent_name,
            estimate={},
            total_bid=0.0,
            margin_pct=0.0,
            confidence="low",
            temperature=temperature,
            sample_index=sample_index,
            error=str(exc),
        )
```

- [ ] **Step 4: Replace grid loop in `run_tournament`**

In `backend/agents/tournament.py`, add `TEMPERATURES` constant after `PERSONALITY_PROMPTS`:

```python
TEMPERATURES: list[float] = [0.3, 0.7, 1.2]
```

Update the `run_tournament` signature and loop:

```python
async def run_tournament(
    description: str,
    zip_code: str,
    trade_type: str = "general",
    overhead_pct: float = 20.0,
    margin_pct: float = 12.0,
    client_id: Optional[str] = None,
    n_agents: int = 5,
    n_samples: int = 2,
) -> TournamentResult:
    """
    Run PreBidCalc across a personality × temperature × sample grid in parallel.

    Grid: n_agents personalities × 3 temperature tiers × n_samples repeats.
    Default (n_agents=5, n_samples=2): 30 parallel API calls.

    Returns a TournamentResult with:
    - entries: all raw results from the grid
    - consensus_entries: one median-collapsed entry per personality
    """
    personalities = list(PERSONALITY_PROMPTS.keys())[:n_agents]

    # Optionally enrich historical_match with client win history
    client_context = ""
    if client_id and "historical_match" in personalities:
        try:
            from backend.agents.feedback_loop import load_client_context
            client_context = await asyncio.to_thread(load_client_context, client_id)
        except Exception:
            pass

    tasks = []
    for name in personalities:
        modifier = PERSONALITY_PROMPTS[name]
        if name == "historical_match" and client_context:
            modifier = modifier + f"\n\n{client_context}"
        for temp in TEMPERATURES:
            for sample_idx in range(n_samples):
                tasks.append(
                    _run_single_agent(
                        name, description, zip_code, trade_type,
                        overhead_pct, margin_pct, modifier,
                        temperature=temp,
                        sample_index=sample_idx,
                    )
                )

    results: list[AgentResult] = list(await asyncio.gather(*tasks))

    consensus = _collapse_to_consensus(results)

    # For DB storage, keep all raw entries plus mark consensus entries
    consensus_names_bids = {(e.agent_name, e.total_bid, e.temperature, e.sample_index) for e in consensus}

    async with aiosqlite.connect(DB_PATH) as db:
        tournament_id = await _save_tournament(db, client_id, description, zip_code)
        await _save_entries(db, tournament_id, results, consensus_names_bids)

    return TournamentResult(
        tournament_id=tournament_id,
        entries=results,
        consensus_entries=consensus,
    )
```

- [ ] **Step 5: Update `_save_entries` to write `temperature` and `is_consensus`**

In `backend/agents/tournament.py`, update `_save_entries`:

```python
async def _save_entries(
    db: aiosqlite.Connection,
    tournament_id: int,
    results: list[AgentResult],
    consensus_keys: set[tuple],
) -> None:
    for result in results:
        is_consensus = 1 if (
            result.agent_name, result.total_bid, result.temperature, result.sample_index
        ) in consensus_keys else 0
        await db.execute(
            """INSERT INTO tournament_entries
               (tournament_id, agent_name, total_bid, line_items_json, won, score, temperature, is_consensus)
               VALUES (?, ?, ?, ?, 0, NULL, ?, ?)""",
            (
                tournament_id,
                result.agent_name,
                result.total_bid,
                json.dumps(result.estimate),
                result.temperature,
                is_consensus,
            ),
        )
    await db.commit()
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
uv run pytest tests/test_tournament.py -v
```

Expected: All tournament tests PASS.

- [ ] **Step 7: Run full test suite**

```bash
uv run pytest tests/ -v
```

Expected: All tests pass.

- [ ] **Step 8: Commit**

```bash
git add backend/agents/tournament.py tests/test_tournament.py
git commit -m "feat: expand tournament to personality×temperature×sample grid with consensus collapse"
```

---

## Task 6: DB migrations for `temperature` and `is_consensus` columns

**Files:**
- Modify: `backend/api/main.py`

- [ ] **Step 1: Append two migrations to `_MIGRATIONS` in `main.py`**

In `backend/api/main.py`, add to the end of the `_MIGRATIONS` list:

```python
    # migration 4: temperature ensemble — add temperature and is_consensus columns
    """ALTER TABLE tournament_entries ADD COLUMN temperature REAL DEFAULT 0.7""",
    """ALTER TABLE tournament_entries ADD COLUMN is_consensus INTEGER DEFAULT 0""",
```

The existing `_run_migrations` runner handles duplicate-column errors via the `"duplicate column"` check, so this is safe to run against an existing DB.

- [ ] **Step 2: Verify migration runs cleanly**

Start the dev server and check it boots without error:

```bash
uv run uvicorn backend.api.main:app --reload --port 8000
```

Expected: Server starts, no migration errors in logs. `Ctrl+C` to stop.

- [ ] **Step 3: Run full test suite**

```bash
uv run pytest tests/ -v
```

Expected: All tests pass.

- [ ] **Step 4: Commit**

```bash
git add backend/api/main.py
git commit -m "feat: db migration — add temperature and is_consensus columns to tournament_entries"
```

---

## Task 7: Expose `n_samples` on the route and `consensus_entries` in the response

**Files:**
- Modify: `backend/api/routes.py`
- Modify: `tests/test_routes.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_routes.py`:

```python
@pytest.mark.anyio
async def test_tournament_run_n_samples_invalid():
    """n_samples > 5 should return 422."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post("/api/tournament/run", json={
            "description": "Build a 10,000 sqft warehouse in Houston TX",
            "zip_code": "77001",
            "n_samples": 99,
        })
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_tournament_run_n_samples_zero_invalid():
    """n_samples=0 should return 422."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post("/api/tournament/run", json={
            "description": "Build a 10,000 sqft warehouse in Houston TX",
            "zip_code": "77001",
            "n_samples": 0,
        })
    assert resp.status_code == 422
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_routes.py::test_tournament_run_n_samples_invalid tests/test_routes.py::test_tournament_run_n_samples_zero_invalid -v
```

Expected: FAIL — `n_samples` field doesn't exist yet, so request succeeds with 200 or 500 instead of 422.

- [ ] **Step 3: Add `n_samples` to `TournamentRunRequest` in `routes.py`**

In `backend/api/routes.py`, update `TournamentRunRequest`:

```python
class TournamentRunRequest(BaseModel):
    description: str = Field(..., min_length=10, description="Plain-English project description")
    zip_code: str = Field(..., min_length=5, max_length=10, description="Project zip code")
    trade_type: str = Field(default="general", description="Primary trade type")
    overhead_pct: float = Field(default=None, ge=0, le=100, description="Overhead %")
    margin_pct: float = Field(default=None, ge=0, le=100, description="Target margin %")
    client_id: Optional[str] = Field(default=None, description="Client ID for profile-aware bidding")
    n_agents: int = Field(default=5, ge=1, le=5, description="Number of agent personalities to run")
    n_samples: int = Field(default=2, ge=1, le=5, description="Samples per personality×temperature cell (1–5)")
```

- [ ] **Step 4: Pass `n_samples` through and add `consensus_entries` to response**

In `backend/api/routes.py`, update the `tournament_run` endpoint:

```python
@router.post("/tournament/run")
async def tournament_run(req: TournamentRunRequest):
    """Run a bid tournament — N agents estimate the same project in parallel."""
    try:
        result = await run_tournament(
            description=req.description,
            zip_code=req.zip_code,
            trade_type=req.trade_type,
            overhead_pct=req.resolved_overhead(),
            margin_pct=req.resolved_margin(),
            client_id=req.client_id,
            n_agents=req.n_agents,
            n_samples=req.n_samples,
        )

        def _serialize_entry(e):
            return {
                "agent_name": e.agent_name,
                "total_bid": e.total_bid,
                "margin_pct": e.margin_pct,
                "confidence": e.confidence,
                "temperature": e.temperature,
                "sample_index": e.sample_index,
                "estimate": e.estimate,
                "error": e.error,
            }

        return {
            "tournament_id": result.tournament_id,
            "entries": [_serialize_entry(e) for e in result.entries],
            "consensus_entries": [_serialize_entry(e) for e in result.consensus_entries],
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
uv run pytest tests/test_routes.py::test_tournament_run_n_samples_invalid tests/test_routes.py::test_tournament_run_n_samples_zero_invalid -v
```

Expected: PASS

- [ ] **Step 6: Run full test suite**

```bash
uv run pytest tests/ -v
```

Expected: All tests pass.

- [ ] **Step 7: Commit**

```bash
git add backend/api/routes.py tests/test_routes.py
git commit -m "feat: expose n_samples param and consensus_entries in tournament route response"
```

---

## Final Verification

- [ ] **Run full test suite one last time**

```bash
uv run pytest tests/ -v
```

Expected: All tests green.

- [ ] **Smoke test the server**

```bash
uv run uvicorn backend.api.main:app --reload --port 8000
```

In a second terminal:

```bash
curl -s -X POST http://localhost:8000/api/tournament/run \
  -H "Content-Type: application/json" \
  -d '{"description": "Build a 5,000 sqft retail space in Austin TX", "zip_code": "78701", "n_samples": 1}' \
  | python3 -m json.tool | head -40
```

Expected: JSON response with `tournament_id`, `entries` (15 items), and `consensus_entries` (5 items).
