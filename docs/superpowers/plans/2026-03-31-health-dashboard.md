# Health Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a single-page vanilla HTML/JS health dashboard at `localhost:3000/health` where an estimator can inspect agent calibration, price deviations, and review queue — and fix problems inline.

**Architecture:** One new static HTML file (`frontend/dist/health.html`) calls existing `/api/*` endpoints via `fetch()`. Three new/modified backend endpoints support the dashboard's actions. The nginx config gets one new `location` block. No build step, no framework, no CDN.

**Tech Stack:** Python/FastAPI (backend additions), vanilla HTML/CSS/JS (dashboard), nginx (routing), pytest + aiosqlite (tests)

---

## File Map

| File | Change | What It Does |
|---|---|---|
| `backend/scheduler.py` | MODIFY | Extract `run_verification_batch()` return value so API can call it |
| `backend/api/verification.py` | MODIFY | Add `POST /api/verify/run`; add `custom_price` to `QueueResolveRequest`; call `_update_seed_csv` on approval |
| `backend/agents/feedback_loop.py` | MODIFY | Add `exclude_agent()` and `reset_agent_history()` |
| `backend/api/routes.py` | MODIFY | Add `POST /api/client/{client_id}/exclude-agent` and `DELETE /api/client/{client_id}/agent-history/{agent_name}` |
| `backend/agents/tournament.py` | MODIFY | Skip agents listed in `client_profile.excluded_agents[]` |
| `nginx.conf` | MODIFY | Add `location = /health` route |
| `frontend/dist/health.html` | CREATE | Complete dashboard — HTML + CSS + vanilla JS |
| `tests/test_health_endpoints.py` | CREATE | Tests for all 3 new backend endpoints |

---

## Task 1: Extract `run_verification_batch()` + Add `POST /api/verify/run`

**Files:**
- Modify: `backend/scheduler.py`
- Modify: `backend/api/verification.py`
- Create: `tests/test_health_endpoints.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_health_endpoints.py`:

```python
import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient


def make_app(db_path: str):
    from fastapi import FastAPI
    from backend.api.verification import verification_router
    import backend.api.verification as vmod
    vmod.DB_PATH = db_path
    app = FastAPI()
    app.include_router(verification_router, prefix="/api")
    return app


@pytest.fixture
def client(tmp_path):
    import asyncio, aiosqlite
    from backend.api.main import _CREATE_TABLES
    db_path = str(tmp_path / "test.db")
    async def setup():
        async with aiosqlite.connect(db_path) as db:
            await db.executescript(_CREATE_TABLES)
            await db.commit()
    asyncio.get_event_loop().run_until_complete(setup())
    app = make_app(db_path)
    with TestClient(app) as c:
        yield c


def test_post_verify_run_returns_summary(client):
    """POST /api/verify/run returns a summary dict with expected keys."""
    mock_result = {
        "status": "complete",
        "items_checked": 22,
        "flagged": 2,
        "auto_updated": 1,
        "duration_seconds": 4.2,
        "triggered_at": "2026-03-31T10:00:00+00:00",
    }
    with patch("backend.api.verification.run_verification_batch", new=AsyncMock(return_value=mock_result)):
        resp = client.post("/api/verify/run")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "complete"
    assert data["items_checked"] == 22
    assert "triggered_at" in data
```

- [ ] **Step 2: Run to confirm FAIL**

```bash
cd "/Users/bevo/Library/CloudStorage/GoogleDrive-kroberts2007@gmail.com/My Drive/TakeoffAI"
uv run pytest tests/test_health_endpoints.py::test_post_verify_run_returns_summary -v
```

Expected: FAIL — `run_verification_batch` not importable yet.

- [ ] **Step 3: Extract `run_verification_batch()` in `scheduler.py`**

Open `backend/scheduler.py`. Add the following function **after** the `CSV_PATH` line and **before** `_run_nightly_verification`. Also add `timezone` to the datetime import:

Replace:
```python
import csv
import logging
from datetime import datetime
from pathlib import Path
```

With:
```python
import csv
import logging
from datetime import datetime, timezone
from pathlib import Path
```

Then add `run_verification_batch` before `_run_nightly_verification`:

```python
async def run_verification_batch() -> dict:
    """
    Verify all rows in material_costs.csv against web sources.
    Returns a summary dict. Called by both the nightly scheduler and the
    on-demand API endpoint POST /api/verify/run.
    """
    if not CSV_PATH.exists():
        return {
            "status": "skipped",
            "reason": "material_costs.csv not found",
            "items_checked": 0,
            "flagged": 0,
            "auto_updated": 0,
            "duration_seconds": 0.0,
            "triggered_at": datetime.now(timezone.utc).isoformat(),
        }

    with CSV_PATH.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    line_items = [
        {
            "description": row["item"],
            "unit": row["unit"],
            "unit_material_cost": float(row.get("low_cost", 0)),
        }
        for row in rows
        if row.get("item") and row.get("unit")
    ]

    triggered_at = datetime.now(timezone.utc)
    records = await verify_line_items(line_items, triggered_by="on_demand")
    elapsed = (datetime.now(timezone.utc) - triggered_at).total_seconds()

    return {
        "status": "complete",
        "items_checked": len(records),
        "flagged": sum(1 for r in records if r.get("flagged")),
        "auto_updated": sum(1 for r in records if r.get("auto_updated")),
        "duration_seconds": round(elapsed, 1),
        "triggered_at": triggered_at.isoformat(),
    }
```

Then update `_run_nightly_verification` to call `run_verification_batch`:

```python
async def _run_nightly_verification() -> None:
    """
    Nightly job: verify every row in material_costs.csv against web sources.
    Delegates to run_verification_batch() and logs the summary.
    """
    result = await run_verification_batch()
    logger.info(
        "Nightly verification: %s items checked | %s flagged | %s auto-updated | %.1fs",
        result["items_checked"], result["flagged"], result["auto_updated"], result["duration_seconds"],
    )
```

- [ ] **Step 4: Add `POST /api/verify/run` to `verification.py`**

Add to the imports at the top of `backend/api/verification.py`:

```python
from backend.scheduler import run_verification_batch
```

Add the endpoint after `get_accuracy`:

```python
@verification_router.post("/verify/run")
async def run_verification():
    """On-demand: trigger verification of all material_costs.csv rows. Waits for completion."""
    try:
        result = await run_verification_batch()
        return result
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
```

- [ ] **Step 5: Run test**

```bash
uv run pytest tests/test_health_endpoints.py::test_post_verify_run_returns_summary -v
```

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/scheduler.py backend/api/verification.py tests/test_health_endpoints.py
git commit -m "feat: add POST /api/verify/run endpoint and extract run_verification_batch"
```

---

## Task 2: Add `custom_price` to Queue Resolution + CSV Update on Approval

**Files:**
- Modify: `backend/api/verification.py`
- Modify: `tests/test_health_endpoints.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_health_endpoints.py`:

```python
import asyncio
import aiosqlite as _aiosqlite


def test_patch_queue_approved_triggers_csv_update(tmp_path):
    """PATCH /api/verify/queue/{id} with approved calls _update_seed_csv."""
    import aiosqlite

    db_path = str(tmp_path / "test2.db")
    from backend.api.main import _CREATE_TABLES

    async def setup():
        async with aiosqlite.connect(db_path) as db:
            await db.executescript(_CREATE_TABLES)
            await db.execute(
                "INSERT INTO price_audit (id, triggered_by, line_item, unit, ai_unit_cost, "
                "verified_low, verified_high, verified_mid, source_count) VALUES (?,?,?,?,?,?,?,?,?)",
                (1, "nightly", "Framing Lumber (2x4x8)", "LF", 0.60, 0.90, 1.05, 0.975, 3)
            )
            await db.execute(
                "INSERT INTO review_queue (id, audit_id, line_item, unit, ai_unit_cost, "
                "verified_mid, deviation_pct) VALUES (?,?,?,?,?,?,?)",
                (1, 1, "Framing Lumber (2x4x8)", "LF", 0.60, 0.975, -38.0)
            )
            await db.commit()

    asyncio.get_event_loop().run_until_complete(setup())

    app = make_app(db_path)
    with TestClient(app) as c:
        with patch("backend.api.verification._update_seed_csv") as mock_csv:
            resp = c.patch("/api/verify/queue/1", json={"status": "approved"})

    assert resp.status_code == 200
    assert resp.json()["status"] == "approved"
    mock_csv.assert_called_once_with(
        item="Framing Lumber (2x4x8)",
        new_low=0.90,
        new_high=1.05,
    )


def test_patch_queue_approved_custom_price(tmp_path):
    """PATCH with custom_price uses ±5% band for CSV update."""
    import aiosqlite

    db_path = str(tmp_path / "test3.db")
    from backend.api.main import _CREATE_TABLES

    async def setup():
        async with aiosqlite.connect(db_path) as db:
            await db.executescript(_CREATE_TABLES)
            await db.execute(
                "INSERT INTO price_audit (id, triggered_by, line_item, unit, ai_unit_cost, "
                "verified_low, verified_high, verified_mid, source_count) VALUES (?,?,?,?,?,?,?,?,?)",
                (1, "nightly", "Framing Lumber (2x4x8)", "LF", 0.60, 0.90, 1.05, 0.975, 3)
            )
            await db.execute(
                "INSERT INTO review_queue (id, audit_id, line_item, unit, ai_unit_cost, "
                "verified_mid, deviation_pct) VALUES (?,?,?,?,?,?,?)",
                (1, 1, "Framing Lumber (2x4x8)", "LF", 0.60, 0.975, -38.0)
            )
            await db.commit()

    asyncio.get_event_loop().run_until_complete(setup())

    app = make_app(db_path)
    with TestClient(app) as c:
        with patch("backend.api.verification._update_seed_csv") as mock_csv:
            resp = c.patch("/api/verify/queue/1", json={"status": "approved", "custom_price": 1.00})

    assert resp.status_code == 200
    mock_csv.assert_called_once_with(
        item="Framing Lumber (2x4x8)",
        new_low=pytest.approx(0.95),
        new_high=pytest.approx(1.05),
    )


def test_patch_queue_rejected_no_csv_update(tmp_path):
    """PATCH with rejected does NOT call _update_seed_csv."""
    import aiosqlite

    db_path = str(tmp_path / "test4.db")
    from backend.api.main import _CREATE_TABLES

    async def setup():
        async with aiosqlite.connect(db_path) as db:
            await db.executescript(_CREATE_TABLES)
            await db.execute(
                "INSERT INTO price_audit (id, triggered_by, line_item, unit, ai_unit_cost, "
                "verified_low, verified_high, verified_mid, source_count) VALUES (?,?,?,?,?,?,?,?,?)",
                (1, "nightly", "Concrete (3000 PSI)", "CY", 155.0, 140.0, 150.0, 145.0, 2)
            )
            await db.execute(
                "INSERT INTO review_queue (id, audit_id, line_item, unit, ai_unit_cost, "
                "verified_mid, deviation_pct) VALUES (?,?,?,?,?,?,?)",
                (1, 1, "Concrete (3000 PSI)", "CY", 155.0, 145.0, 6.9)
            )
            await db.commit()

    asyncio.get_event_loop().run_until_complete(setup())

    app = make_app(db_path)
    with TestClient(app) as c:
        with patch("backend.api.verification._update_seed_csv") as mock_csv:
            resp = c.patch("/api/verify/queue/1", json={"status": "rejected"})

    assert resp.status_code == 200
    mock_csv.assert_not_called()
```

- [ ] **Step 2: Run to confirm FAIL**

```bash
uv run pytest tests/test_health_endpoints.py -v -k "csv_update or custom_price or rejected_no"
```

Expected: FAIL — `_update_seed_csv` not imported in verification.py yet.

- [ ] **Step 3: Update `QueueResolveRequest` and `resolve_queue_item` in `verification.py`**

Add `_update_seed_csv` to the price_verifier import at the top of `backend/api/verification.py`:

```python
from backend.agents.price_verifier import verify_line_items, _update_seed_csv
```

Replace `QueueResolveRequest`:

```python
class QueueResolveRequest(BaseModel):
    status: Literal["approved", "rejected"]
    reviewer_notes: Optional[str] = None
    custom_price: Optional[float] = None
```

Replace the entire `resolve_queue_item` endpoint:

```python
@verification_router.patch("/verify/queue/{queue_id}")
async def resolve_queue_item(queue_id: int, req: QueueResolveRequest):
    """Approve or reject a flagged price deviation. Approval updates material_costs.csv."""
    try:
        resolved_at = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row

            # Load queue item joined with audit for verified prices
            async with db.execute(
                """
                SELECT rq.*, pa.verified_low, pa.verified_high
                FROM review_queue rq
                LEFT JOIN price_audit pa ON rq.audit_id = pa.id
                WHERE rq.id = ?
                """,
                (queue_id,),
            ) as cur:
                full_row = await cur.fetchone()

            if not full_row:
                raise HTTPException(status_code=404, detail=f"Queue item {queue_id} not found")

            full_row = dict(full_row)

            await db.execute(
                "UPDATE review_queue SET status = ?, reviewer_notes = ?, resolved_at = ? WHERE id = ?",
                (req.status, req.reviewer_notes, resolved_at, queue_id),
            )
            await db.commit()

            async with db.execute(
                "SELECT * FROM review_queue WHERE id = ?", (queue_id,)
            ) as cur:
                row = dict(await cur.fetchone())

        # Update seed CSV if approved
        if req.status == "approved":
            if req.custom_price is not None:
                new_low = round(req.custom_price * 0.95, 4)
                new_high = round(req.custom_price * 1.05, 4)
            else:
                new_low = full_row.get("verified_low")
                new_high = full_row.get("verified_high")

            if new_low and new_high:
                _update_seed_csv(
                    item=full_row["line_item"],
                    new_low=new_low,
                    new_high=new_high,
                )

        return row
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_health_endpoints.py -v
```

Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add backend/api/verification.py tests/test_health_endpoints.py
git commit -m "feat: add custom_price to queue resolution and CSV update on approval"
```

---

## Task 3: Agent Management — Exclude + Reset History

**Files:**
- Modify: `backend/agents/feedback_loop.py`
- Modify: `backend/api/routes.py`
- Modify: `tests/test_health_endpoints.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_health_endpoints.py`:

```python
def test_exclude_agent_adds_to_profile(tmp_path, monkeypatch):
    """POST /api/client/{id}/exclude-agent adds agent to excluded_agents list."""
    import backend.agents.feedback_loop as fl
    monkeypatch.setattr(fl, "PROFILES_DIR", tmp_path)

    # Create a profile
    import json
    profile = {
        "client_id": "client1",
        "created_at": "2026-01-01T00:00:00",
        "winning_examples": [],
        "agent_elo": {a: 1000 for a in fl.ALL_AGENTS},
        "stats": {"total_tournaments": 0, "win_rate_by_agent": {}, "avg_winning_bid": 0.0,
                  "avg_winning_margin": 0.0, "wins_by_agent": {}},
    }
    (tmp_path / "client1.json").write_text(json.dumps(profile))

    from fastapi import FastAPI
    from backend.api.routes import router
    app = FastAPI()
    app.include_router(router, prefix="/api")

    with TestClient(app) as c:
        resp = c.post("/api/client/client1/exclude-agent", json={"agent_name": "aggressive"})

    assert resp.status_code == 200
    updated = json.loads((tmp_path / "client1.json").read_text())
    assert "aggressive" in updated.get("excluded_agents", [])


def test_reset_agent_history_clears_deviation(tmp_path, monkeypatch):
    """DELETE /api/client/{id}/agent-history/{agent} clears history and removes red flag."""
    import backend.agents.feedback_loop as fl
    monkeypatch.setattr(fl, "PROFILES_DIR", tmp_path)

    import json
    profile = {
        "client_id": "client2",
        "created_at": "2026-01-01T00:00:00",
        "winning_examples": [],
        "agent_elo": {a: 1000 for a in fl.ALL_AGENTS},
        "stats": {"total_tournaments": 0, "win_rate_by_agent": {}, "avg_winning_bid": 0.0,
                  "avg_winning_margin": 0.0, "wins_by_agent": {}},
        "calibration": {
            "agent_deviation_history": {"aggressive": [8.0, 7.5, 9.0, 6.5, 8.2]},
            "red_flagged_agents": ["aggressive"],
            "win_prob_predictions": [], "win_prob_actuals": [], "brier_score": None,
            "confidence_accuracy": {},
        },
    }
    (tmp_path / "client2.json").write_text(json.dumps(profile))

    from fastapi import FastAPI
    from backend.api.routes import router
    app = FastAPI()
    app.include_router(router, prefix="/api")

    with TestClient(app) as c:
        resp = c.delete("/api/client/client2/agent-history/aggressive")

    assert resp.status_code == 200
    updated = json.loads((tmp_path / "client2.json").read_text())
    assert updated["calibration"]["agent_deviation_history"]["aggressive"] == []
    assert "aggressive" not in updated["calibration"]["red_flagged_agents"]
```

- [ ] **Step 2: Run to confirm FAIL**

```bash
uv run pytest tests/test_health_endpoints.py -v -k "exclude_agent or reset_agent"
```

Expected: FAIL — endpoints not yet defined.

- [ ] **Step 3: Add `exclude_agent()` and `reset_agent_history()` to `feedback_loop.py`**

Append to the end of `backend/agents/feedback_loop.py`:

```python
def exclude_agent(client_id: str, agent_name: str) -> dict:
    """
    Add agent_name to excluded_agents list in client profile.
    Tournament engine skips excluded agents when running for this client.
    Returns updated profile.
    """
    path = _profile_path(client_id)
    profile = json.loads(path.read_text()) if path.exists() else _empty_profile(client_id)
    excluded = profile.setdefault("excluded_agents", [])
    if agent_name not in excluded:
        excluded.append(agent_name)
    path.write_text(json.dumps(profile, indent=2))
    return profile


def reset_agent_history(client_id: str, agent_name: str) -> dict:
    """
    Clear deviation history for agent_name and remove from red_flagged_agents.
    Used when an estimator decides to give a flagged agent a clean slate.
    Returns updated calibration block.
    """
    path = _profile_path(client_id)
    profile = json.loads(path.read_text()) if path.exists() else _empty_profile(client_id)

    cal = profile.setdefault("calibration", {
        "win_prob_predictions": [],
        "win_prob_actuals": [],
        "brier_score": None,
        "confidence_accuracy": {},
        "agent_deviation_history": {a: [] for a in ALL_AGENTS},
        "red_flagged_agents": [],
    })
    cal.setdefault("agent_deviation_history", {})[agent_name] = []
    red_flagged = cal.setdefault("red_flagged_agents", [])
    if agent_name in red_flagged:
        red_flagged.remove(agent_name)

    path.write_text(json.dumps(profile, indent=2))
    return profile.get("calibration", {})
```

- [ ] **Step 4: Add endpoints to `routes.py`**

Add these imports at the top of `backend/api/routes.py` (after existing imports):

```python
from backend.agents.feedback_loop import exclude_agent as _exclude_agent, reset_agent_history as _reset_agent_history
```

Add Pydantic model and two endpoints after the existing `client_profile` endpoint:

```python
class ExcludeAgentRequest(BaseModel):
    agent_name: str = Field(..., description="Agent personality to exclude from tournaments")


@router.post("/client/{client_id}/exclude-agent")
async def exclude_agent_endpoint(client_id: str, req: ExcludeAgentRequest):
    """Add an agent to the client's excluded list — it will be skipped in future tournaments."""
    try:
        profile = await asyncio.to_thread(_exclude_agent, client_id, req.agent_name)
        return {"client_id": client_id, "excluded_agents": profile.get("excluded_agents", [])}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.delete("/client/{client_id}/agent-history/{agent_name}")
async def reset_agent_history_endpoint(client_id: str, agent_name: str):
    """Clear deviation history for an agent and remove its red-flag status."""
    try:
        calibration = await asyncio.to_thread(_reset_agent_history, client_id, agent_name)
        return {"client_id": client_id, "agent_name": agent_name, "calibration": calibration}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
```

Also add `asyncio` to the imports at the top of `routes.py`:

```python
import asyncio
```

- [ ] **Step 5: Run tests**

```bash
uv run pytest tests/test_health_endpoints.py -v
```

Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add backend/agents/feedback_loop.py backend/api/routes.py tests/test_health_endpoints.py
git commit -m "feat: add exclude_agent and reset_agent_history endpoints for dashboard fix actions"
```

---

## Task 4: Tournament Agent Exclusion Support

**Files:**
- Modify: `backend/agents/tournament.py`
- Modify: `tests/test_health_endpoints.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_health_endpoints.py`:

```python
def test_excluded_agents_skipped_in_tournament(tmp_path, monkeypatch):
    """run_tournament skips agents listed in client_profile.excluded_agents."""
    import json
    import backend.agents.feedback_loop as fl
    monkeypatch.setattr(fl, "PROFILES_DIR", tmp_path)

    profile = {
        "client_id": "client3",
        "created_at": "2026-01-01T00:00:00",
        "winning_examples": [],
        "agent_elo": {a: 1000 for a in fl.ALL_AGENTS},
        "stats": {"total_tournaments": 0, "win_rate_by_agent": {}, "avg_winning_bid": 0.0,
                  "avg_winning_margin": 0.0, "wins_by_agent": {}},
        "excluded_agents": ["aggressive"],
    }
    (tmp_path / "client3.json").write_text(json.dumps(profile))

    import aiosqlite, asyncio
    db_path = str(tmp_path / "test_tourn.db")
    from backend.api.main import _CREATE_TABLES
    import backend.agents.tournament as tourn_mod
    monkeypatch.setattr(tourn_mod, "DB_PATH", db_path)

    async def setup_and_run():
        async with aiosqlite.connect(db_path) as db:
            await db.executescript(_CREATE_TABLES)
            await db.commit()

        agents_run = []

        async def fake_run_single(agent_name, *args, **kwargs):
            agents_run.append(agent_name)
            from backend.agents.tournament import AgentResult
            return AgentResult(
                agent_name=agent_name,
                estimate={"total_bid": 100000, "line_items": [], "margin_pct": 12},
                total_bid=100000,
                margin_pct=12,
                confidence="medium",
            )

        with patch("backend.agents.tournament._run_single_agent", new=fake_run_single):
            from backend.agents.tournament import run_tournament
            result = await run_tournament(
                description="40x60 metal building Brownwood TX",
                zip_code="76801",
                trade_type="general",
                overhead_pct=20.0,
                margin_pct=12.0,
                client_id="client3",
                n_agents=5,
            )

        return agents_run

    agents_run = asyncio.get_event_loop().run_until_complete(setup_and_run())
    assert "aggressive" not in agents_run
    assert "balanced" in agents_run
```

- [ ] **Step 2: Run to confirm FAIL**

```bash
uv run pytest tests/test_health_endpoints.py::test_excluded_agents_skipped_in_tournament -v
```

Expected: FAIL — tournament doesn't check `excluded_agents` yet.

- [ ] **Step 3: Modify `tournament.py` to skip excluded agents**

Open `backend/agents/tournament.py`. Find the `run_tournament` function. Locate where it builds the list of agents to run (look for the section iterating over `PERSONALITY_PROMPTS`). Add exclusion logic.

Read the full `run_tournament` function first, then add this block **after** loading client context and **before** dispatching agents. The exact insertion point is after the `load_client_context` call and before the `asyncio.gather` / agent dispatch section:

```python
    # Load excluded agents for this client
    excluded_agents: list[str] = []
    if client_id:
        from backend.agents.feedback_loop import _profile_path
        _prof_path = _profile_path(client_id)
        if _prof_path.exists():
            import json as _json
            _prof = _json.loads(_prof_path.read_text())
            excluded_agents = _prof.get("excluded_agents", [])
```

Then when building the list of agents to run, filter out excluded ones. Look for the loop that picks which personalities to run and wrap it:

```python
    agents_to_run = [
        name for name in list(PERSONALITY_PROMPTS.keys())[:n_agents]
        if name not in excluded_agents
    ]
```

Use `agents_to_run` instead of the original agent list when dispatching.

- [ ] **Step 4: Run test**

```bash
uv run pytest tests/test_health_endpoints.py::test_excluded_agents_skipped_in_tournament -v
```

Expected: PASS

- [ ] **Step 5: Run full test suite**

```bash
uv run pytest tests/ -v --tb=short
```

Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add backend/agents/tournament.py tests/test_health_endpoints.py
git commit -m "feat: skip excluded_agents in tournament run; wired to client profile"
```

---

## Task 5: nginx Route for `/health`

**Files:**
- Modify: `nginx.conf`

- [ ] **Step 1: Add `/health` location block**

Open `nginx.conf`. Add a new location block **before** the existing `location /` catch-all:

```nginx
    # Health dashboard
    location = /health {
        try_files /health.html =404;
    }
```

The full updated `nginx.conf` should look like:

```nginx
server {
    listen 80;
    root /usr/share/nginx/html;
    index index.html;

    # Proxy all /api/ requests to the backend container — eliminates CORS
    location /api/ {
        proxy_pass         http://backend:8000/api/;
        proxy_http_version 1.1;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Real-IP         $remote_addr;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_read_timeout 120s;
    }

    # Health dashboard
    location = /health {
        try_files /health.html =404;
    }

    # Serve static files; fall back to index.html for SPA routing
    location / {
        try_files $uri $uri/ /index.html;
    }
}
```

- [ ] **Step 2: Verify nginx config syntax (if nginx is installed locally)**

```bash
nginx -t -c "$(pwd)/nginx.conf" 2>/dev/null && echo "ok" || echo "check nginx install"
```

If nginx is not installed locally, skip — it will be validated when Docker builds.

- [ ] **Step 3: Commit**

```bash
git add nginx.conf
git commit -m "feat: add /health route to nginx config"
```

---

## Task 6: `health.html` — Skeleton, CSS, Tab Switching

**Files:**
- Create: `frontend/dist/health.html`

- [ ] **Step 1: Create the skeleton file**

Create `frontend/dist/health.html` with the full structure, CSS, and tab switching logic. All tab content panels are empty placeholders — they'll be filled in subsequent tasks:

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>TakeoffAI Health</title>
  <style>
    :root {
      --bg:         #0f172a;
      --bg-card:    #1e293b;
      --bg-input:   #0f172a;
      --border:     #334155;
      --blue:       #38bdf8;
      --blue-dim:   #1e3a5f;
      --green:      #4ade80;
      --green-dim:  #14532d;
      --yellow:     #facc15;
      --yellow-dim: #422006;
      --red:        #f87171;
      --red-dim:    #1c0a0a;
      --red-border: #7f1d1d;
      --text:       #f1f5f9;
      --muted:      #94a3b8;
      --faint:      #64748b;
      --radius:     6px;
      --font:       'SF Mono', 'Fira Code', 'Cascadia Code', monospace;
    }

    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

    body {
      font-family: var(--font);
      background: var(--bg);
      color: var(--text);
      font-size: 12px;
      min-height: 100vh;
    }

    /* ── Top bar ─────────────────────────────────────────────── */
    .topbar {
      background: var(--bg);
      border-bottom: 1px solid var(--border);
      padding: 10px 20px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      position: sticky;
      top: 0;
      z-index: 100;
    }
    .topbar-title { color: var(--blue); font-weight: bold; font-size: 14px; }
    .topbar-right { display: flex; gap: 10px; align-items: center; }
    .topbar-client-label { color: var(--faint); }
    .topbar-client-input {
      background: var(--bg-card);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      color: var(--text);
      font-family: var(--font);
      font-size: 11px;
      padding: 3px 8px;
      width: 140px;
    }
    .topbar-time { color: var(--faint); font-size: 10px; }
    .btn-run {
      background: var(--blue-dim);
      border: 1px solid var(--blue);
      border-radius: var(--radius);
      color: var(--blue);
      font-family: var(--font);
      font-size: 11px;
      padding: 5px 14px;
      cursor: pointer;
    }
    .btn-run:disabled { opacity: 0.5; cursor: not-allowed; }
    .btn-run:hover:not(:disabled) { background: #1e4a7f; }

    /* ── Tabs ────────────────────────────────────────────────── */
    .tabs {
      display: flex;
      border-bottom: 1px solid var(--border);
      background: var(--bg);
      padding: 0 20px;
    }
    .tab {
      padding: 9px 16px;
      color: var(--faint);
      cursor: pointer;
      border-bottom: 2px solid transparent;
      font-size: 11px;
      letter-spacing: 0.05em;
    }
    .tab:hover { color: var(--text); }
    .tab.active { color: var(--blue); border-bottom-color: var(--blue); }
    .tab-badge {
      background: var(--yellow-dim);
      color: var(--yellow);
      border-radius: 8px;
      padding: 1px 5px;
      font-size: 10px;
      margin-left: 4px;
    }

    /* ── Tab panels ──────────────────────────────────────────── */
    .panel { display: none; padding: 20px; max-width: 900px; }
    .panel.active { display: block; }

    /* ── Scorecards ──────────────────────────────────────────── */
    .scorecards {
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 10px;
      margin-bottom: 16px;
    }
    .scorecard {
      background: var(--bg-card);
      border-radius: var(--radius);
      padding: 12px;
      text-align: center;
    }
    .scorecard-value { font-size: 24px; font-weight: bold; }
    .scorecard-label { color: var(--faint); font-size: 9px; margin-top: 3px; letter-spacing: 0.06em; }
    .c-green { color: var(--green); }
    .c-yellow { color: var(--yellow); }
    .c-red { color: var(--red); }
    .c-blue { color: var(--blue); }
    .c-muted { color: var(--muted); }

    /* ── Alert rows ──────────────────────────────────────────── */
    .alert {
      border-radius: var(--radius);
      padding: 10px 12px;
      margin-bottom: 8px;
    }
    .alert-red { background: var(--red-dim); border: 1px solid var(--red-border); }
    .alert-yellow { background: #1c1400; border: 1px solid #854d0e; }
    .alert-green { background: #0a1a0a; border: 1px solid var(--green-dim); }
    .alert-row { display: flex; justify-content: space-between; align-items: center; }
    .alert-title { font-size: 11px; }
    .alert-sub { color: var(--faint); font-size: 10px; }
    .btn-fix {
      color: var(--blue);
      border: 1px solid var(--blue-dim);
      border-radius: var(--radius);
      padding: 2px 10px;
      cursor: pointer;
      font-size: 10px;
      font-family: var(--font);
      background: transparent;
      white-space: nowrap;
    }
    .btn-fix:hover { background: var(--blue-dim); }
    .fix-panel {
      margin-top: 10px;
      padding-top: 10px;
      border-top: 1px solid var(--red-border);
      display: none;
    }
    .fix-panel.open { display: block; }
    .fix-panel-yellow { border-top-color: #854d0e; }
    .fix-desc { color: var(--muted); font-size: 10px; margin-bottom: 8px; }
    .fix-actions { display: flex; gap: 8px; flex-wrap: wrap; }

    /* ── Buttons ─────────────────────────────────────────────── */
    .btn-accept {
      background: var(--green-dim);
      border: 1px solid #166534;
      color: var(--green);
      border-radius: var(--radius);
      padding: 4px 12px;
      cursor: pointer;
      font-size: 10px;
      font-family: var(--font);
    }
    .btn-reject {
      background: var(--bg-card);
      border: 1px solid var(--border);
      color: var(--muted);
      border-radius: var(--radius);
      padding: 4px 12px;
      cursor: pointer;
      font-size: 10px;
      font-family: var(--font);
    }
    .btn-neutral {
      background: var(--bg-card);
      border: 1px solid var(--border);
      color: var(--muted);
      border-radius: var(--radius);
      padding: 4px 12px;
      cursor: pointer;
      font-size: 10px;
      font-family: var(--font);
    }

    /* ── Table (Prices tab) ──────────────────────────────────── */
    .data-table { width: 100%; border-collapse: collapse; }
    .data-table th {
      color: var(--faint);
      font-size: 9px;
      letter-spacing: 0.06em;
      text-align: left;
      padding: 6px 8px;
      border-bottom: 1px solid var(--border);
    }
    .data-table td {
      padding: 6px 8px;
      border-bottom: 1px solid #1e293b;
      font-size: 11px;
      color: var(--muted);
    }
    .data-table tr:hover td { background: #1a2538; }
    .badge {
      border-radius: 4px;
      padding: 1px 6px;
      font-size: 9px;
    }
    .badge-blue { background: var(--blue-dim); color: var(--blue); }
    .badge-green { background: var(--green-dim); color: var(--green); }
    .badge-red { background: var(--red-dim); color: var(--red); }

    /* ── Queue cards ─────────────────────────────────────────── */
    .queue-card {
      background: var(--bg-card);
      border-radius: var(--radius);
      padding: 12px;
      margin-bottom: 10px;
    }
    .queue-card-header { display: flex; justify-content: space-between; margin-bottom: 4px; }
    .queue-card-item { color: var(--text); font-size: 11px; }
    .queue-card-sources { color: var(--faint); font-size: 10px; margin-bottom: 8px; }
    .queue-card-actions { display: flex; gap: 6px; align-items: center; }
    .custom-price-input {
      background: var(--bg);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      color: var(--muted);
      font-family: var(--font);
      font-size: 10px;
      padding: 3px 6px;
      width: 70px;
    }
    .btn-set {
      color: var(--blue);
      background: transparent;
      border: none;
      cursor: pointer;
      font-size: 10px;
      font-family: var(--font);
    }

    /* ── Agents table ────────────────────────────────────────── */
    .agent-row {
      display: flex;
      justify-content: space-between;
      align-items: center;
      background: var(--bg-card);
      padding: 8px 12px;
      border-radius: var(--radius);
      margin-bottom: 6px;
    }
    .agent-row.flagged { background: var(--red-dim); border: 1px solid var(--red-border); }
    .agent-name { color: var(--text); font-size: 11px; width: 130px; }
    .agent-stats { color: var(--faint); font-size: 10px; }
    .agent-status { font-size: 10px; }

    /* ── Filter bar ──────────────────────────────────────────── */
    .filter-bar { display: flex; gap: 10px; margin-bottom: 14px; align-items: center; }
    .filter-bar label { color: var(--faint); font-size: 10px; }
    .filter-toggle {
      background: var(--bg-card);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      color: var(--muted);
      font-family: var(--font);
      font-size: 10px;
      padding: 3px 10px;
      cursor: pointer;
    }
    .filter-toggle.on { background: var(--red-dim); border-color: var(--red-border); color: var(--red); }

    /* ── Loading / empty states ──────────────────────────────── */
    .loading { color: var(--faint); font-size: 11px; padding: 20px 0; }
    .empty { color: var(--faint); font-size: 11px; padding: 16px 0; }
    .error-msg { color: var(--red); font-size: 10px; margin-top: 6px; }

    /* ── Section header ──────────────────────────────────────── */
    .section-title { color: var(--faint); font-size: 9px; letter-spacing: 0.06em; margin-bottom: 10px; }
  </style>
</head>
<body>

<!-- ── Top bar ──────────────────────────────────────────────────────── -->
<div class="topbar">
  <span class="topbar-title">⚡ TakeoffAI Health</span>
  <div class="topbar-right">
    <span class="topbar-client-label">client:</span>
    <input class="topbar-client-input" id="clientInput" placeholder="default" />
    <span class="topbar-time" id="lastRunTime">—</span>
    <button class="btn-run" id="btnRun" onclick="runCheck()">▶ Run Check Now</button>
  </div>
</div>

<!-- ── Tab navigation ───────────────────────────────────────────────── -->
<div class="tabs">
  <div class="tab active" data-tab="overview" onclick="switchTab('overview')">OVERVIEW</div>
  <div class="tab" data-tab="agents" onclick="switchTab('agents')">AGENTS</div>
  <div class="tab" data-tab="prices" onclick="switchTab('prices')">PRICES</div>
  <div class="tab" data-tab="queue" onclick="switchTab('queue')">
    QUEUE<span class="tab-badge" id="queueBadge" style="display:none">0</span>
  </div>
</div>

<!-- ── Tab panels ───────────────────────────────────────────────────── -->
<div class="panel active" id="panel-overview">
  <div class="loading">Loading overview...</div>
</div>

<div class="panel" id="panel-agents">
  <div class="loading">Loading agents...</div>
</div>

<div class="panel" id="panel-prices">
  <div class="loading">Loading prices...</div>
</div>

<div class="panel" id="panel-queue">
  <div class="loading">Loading queue...</div>
</div>

<script>
// ── State ──────────────────────────────────────────────────────────────
let currentClient = new URLSearchParams(window.location.search).get('client') || 'default';
let allData = { audit: [], queue: [], accuracy: null, health: null };
let queueFilterFlagged = false;
let clientDebounce = null;

// ── Tab switching ──────────────────────────────────────────────────────
function switchTab(name) {
  document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t.dataset.tab === name));
  document.querySelectorAll('.panel').forEach(p => p.classList.toggle('active', p.id === 'panel-' + name));
}

// ── Client switcher ────────────────────────────────────────────────────
const clientInput = document.getElementById('clientInput');
clientInput.value = currentClient;
clientInput.addEventListener('input', () => {
  clearTimeout(clientDebounce);
  clientDebounce = setTimeout(() => {
    currentClient = clientInput.value.trim() || 'default';
    history.replaceState({}, '', '?client=' + encodeURIComponent(currentClient));
    loadAll();
  }, 500);
});

// ── Toggle fix panel ───────────────────────────────────────────────────
function toggleFix(panelId, btn) {
  const panel = document.getElementById(panelId);
  const isOpen = panel.classList.toggle('open');
  btn.textContent = isOpen ? '✕ close' : '→ fix';
}

// ── Run Check Now ──────────────────────────────────────────────────────
async function runCheck() {
  const btn = document.getElementById('btnRun');
  btn.disabled = true;
  btn.textContent = '↻ Running...';
  try {
    const res = await fetch('/api/verify/run', { method: 'POST' });
    if (!res.ok) throw new Error(await res.text());
    await loadAll();
  } catch (e) {
    console.error('Run check failed:', e);
  } finally {
    btn.disabled = false;
    btn.textContent = '▶ Run Check Now';
  }
}

// ── Helpers ────────────────────────────────────────────────────────────
function fmt$(n) { return n != null ? '$' + Number(n).toFixed(2) : '—'; }
function fmtDev(n) {
  if (n == null) return '—';
  const s = (n > 0 ? '+' : '') + n.toFixed(1) + '%';
  return `<span class="${Math.abs(n) > 5 ? 'c-red' : 'c-muted'}">${s}</span>`;
}
function fmtDate(s) {
  if (!s) return '—';
  const d = new Date(s);
  return d.toLocaleDateString() + ' ' + d.toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'});
}
function colorScore(score, thresholds, colors) {
  for (let i = 0; i < thresholds.length; i++) {
    if (score <= thresholds[i]) return colors[i];
  }
  return colors[colors.length - 1];
}

// ── Data fetching ──────────────────────────────────────────────────────
async function safeFetch(url) {
  try {
    const r = await fetch(url);
    if (!r.ok) return null;
    return await r.json();
  } catch { return null; }
}

async function loadAll() {
  const [health, audit, queue, accuracy] = await Promise.all([
    safeFetch('/api/health'),
    safeFetch('/api/verify/audit?limit=100'),
    safeFetch('/api/verify/queue?status=pending'),
    safeFetch('/api/verify/accuracy/' + encodeURIComponent(currentClient)),
  ]);

  allData = { health, audit: audit || [], queue: queue || [], accuracy };

  // Update queue badge
  const pending = (queue || []).length;
  const badge = document.getElementById('queueBadge');
  badge.style.display = pending > 0 ? 'inline' : 'none';
  badge.textContent = pending;

  // Update last-run time from most recent nightly audit record
  const nightlyRecords = (audit || []).filter(r => r.triggered_by === 'nightly');
  if (nightlyRecords.length > 0) {
    document.getElementById('lastRunTime').textContent = 'Last run: ' + fmtDate(nightlyRecords[0].created_at);
  }

  renderOverview();
  renderAgents();
  renderPrices();
  renderQueue();
}

// Placeholder render functions — replaced in Tasks 7-11
function renderOverview() { document.getElementById('panel-overview').innerHTML = '<div class="loading">Overview loading...</div>'; }
function renderAgents()   { document.getElementById('panel-agents').innerHTML   = '<div class="loading">Agents loading...</div>'; }
function renderPrices()   { document.getElementById('panel-prices').innerHTML   = '<div class="loading">Prices loading...</div>'; }
function renderQueue()    { document.getElementById('panel-queue').innerHTML    = '<div class="loading">Queue loading...</div>'; }

// ── Boot ───────────────────────────────────────────────────────────────
loadAll();
</script>

</body>
</html>
```

- [ ] **Step 2: Verify the file opens in a browser**

Start the API and serve the frontend:

```bash
cd "/Users/bevo/Library/CloudStorage/GoogleDrive-kroberts2007@gmail.com/My Drive/TakeoffAI"
uv run uvicorn backend.api.main:app --port 8000 &
cd frontend/dist && python3 -m http.server 3000 &
```

Open `http://localhost:3000/health.html` in a browser.

Expected: Dark page loads, "⚡ TakeoffAI Health" in top bar, 4 tabs visible, "Loading..." text in each panel, no JS console errors.

```bash
kill %1 %2
```

- [ ] **Step 3: Commit**

```bash
cd "/Users/bevo/Library/CloudStorage/GoogleDrive-kroberts2007@gmail.com/My Drive/TakeoffAI"
git add frontend/dist/health.html
git commit -m "feat: add health.html skeleton with tabs, CSS, and data loading scaffolding"
```

---

## Task 7: Overview Tab — Scorecards + Alert Strip

**Files:**
- Modify: `frontend/dist/health.html`

- [ ] **Step 1: Replace `renderOverview` in `health.html`**

Find the line:

```javascript
function renderOverview() { document.getElementById('panel-overview').innerHTML = '<div class="loading">Overview loading...</div>'; }
```

Replace it with:

```javascript
function renderOverview() {
  const { audit, queue, accuracy, health } = allData;
  const flaggedAgents = accuracy ? Object.entries(accuracy)
    .filter(([k, v]) => typeof v === 'object' && v !== null && v.red_flagged)
    .map(([k]) => k) : [];

  const agentsHealthy = accuracy
    ? Object.values(accuracy).filter(v => typeof v === 'object' && v !== null && v.avg_deviation_pct != null && !v.red_flagged).length
    : '—';
  const agentsTotal = 5;
  const queueCount = queue.length;
  const brierScore = accuracy ? accuracy.brier_score : null;
  const pricesVerified = audit.length;

  // Scorecard color logic
  const agentColor = flaggedAgents.length === 0 ? 'c-green' : 'c-yellow';
  const queueColor = queueCount === 0 ? 'c-green' : queueCount <= 2 ? 'c-yellow' : 'c-red';
  const brierColor = brierScore == null ? 'c-muted'
    : brierScore < 0.25 ? 'c-green'
    : brierScore < 0.50 ? 'c-yellow' : 'c-red';

  // Nightly batch status
  const nightlyRecords = audit.filter(r => r.triggered_by === 'nightly');
  const lastNightly = nightlyRecords[0];
  let nightlyAge = Infinity;
  if (lastNightly) {
    nightlyAge = (Date.now() - new Date(lastNightly.created_at).getTime()) / 3600000; // hours
  }
  const nightlyColor = nightlyAge > 25 ? 'c-yellow' : 'c-green';
  const nightlyText = lastNightly
    ? `ran ${fmtDate(lastNightly.created_at)}`
    : 'no runs recorded yet';

  // Build alert HTML for each flagged agent
  const agentAlerts = flaggedAgents.map((agent, i) => {
    const stats = accuracy[agent];
    return `
    <div class="alert alert-red" id="alert-agent-${agent}">
      <div class="alert-row">
        <div>
          <span class="alert-title c-red">⚠ ${agent}</span>
          <span class="alert-sub"> — avg deviation ${stats.avg_deviation_pct != null ? Math.abs(stats.avg_deviation_pct).toFixed(1) : '?'}% over last ${stats.deviation_history ? stats.deviation_history.length : 0} jobs</span>
        </div>
        <button class="btn-fix" onclick="toggleFix('fix-agent-${agent}', this)">→ fix</button>
      </div>
      <div class="fix-panel" id="fix-agent-${agent}">
        <div class="fix-desc">This agent is consistently over/under-estimating. Choose an action:</div>
        <div class="fix-actions">
          <button class="btn-accept" onclick="excludeAgent('${agent}')">Exclude from tournaments</button>
          <button class="btn-neutral" onclick="dismissAlert('alert-agent-${agent}')">Keep &amp; watch</button>
          <button class="btn-reject" onclick="resetAgentHistory('${agent}')">Reset history</button>
        </div>
        <div class="error-msg" id="fix-agent-${agent}-err" style="display:none"></div>
      </div>
    </div>`;
  }).join('');

  // Queue alert
  const queueAlert = queueCount > 0 ? `
    <div class="alert alert-yellow" id="alert-queue">
      <div class="alert-row">
        <div>
          <span class="alert-title c-yellow">⚠ ${queueCount} price${queueCount > 1 ? 's' : ''} need review</span>
          <span class="alert-sub"> — deviation &gt;5% flagged from web sources</span>
        </div>
        <button class="btn-fix" onclick="switchTab('queue')">→ fix</button>
      </div>
    </div>` : '';

  // Nightly batch row
  const nightlyAlert = `
    <div class="alert alert-green">
      <span class="alert-title ${nightlyColor}">✓ Nightly batch</span>
      <span class="alert-sub"> — ${nightlyText}</span>
    </div>`;

  document.getElementById('panel-overview').innerHTML = `
    <div class="scorecards">
      <div class="scorecard">
        <div class="scorecard-value ${agentColor}">${flaggedAgents.length === 0 ? agentsTotal + '/' + agentsTotal : (agentsTotal - flaggedAgents.length) + '/' + agentsTotal}</div>
        <div class="scorecard-label">AGENTS HEALTHY</div>
      </div>
      <div class="scorecard">
        <div class="scorecard-value ${queueColor}">${queueCount}</div>
        <div class="scorecard-label">IN REVIEW QUEUE</div>
      </div>
      <div class="scorecard">
        <div class="scorecard-value ${brierColor}">${brierScore != null ? brierScore.toFixed(2) : '—'}</div>
        <div class="scorecard-label">BRIER SCORE</div>
      </div>
      <div class="scorecard">
        <div class="scorecard-value c-blue">${pricesVerified}</div>
        <div class="scorecard-label">PRICES VERIFIED</div>
      </div>
    </div>
    ${agentAlerts}
    ${queueAlert}
    ${nightlyAlert}
  `;
}

// ── Fix action handlers ────────────────────────────────────────────────
async function excludeAgent(agentName) {
  try {
    const r = await fetch(`/api/client/${encodeURIComponent(currentClient)}/exclude-agent`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({agent_name: agentName}),
    });
    if (!r.ok) throw new Error(await r.text());
    document.getElementById('alert-agent-' + agentName).remove();
    await loadAll();
  } catch (e) {
    const err = document.getElementById('fix-agent-' + agentName + '-err');
    if (err) { err.textContent = '⚠ Failed — ' + e.message; err.style.display = 'block'; }
  }
}

async function resetAgentHistory(agentName) {
  try {
    const r = await fetch(`/api/client/${encodeURIComponent(currentClient)}/agent-history/${encodeURIComponent(agentName)}`, {
      method: 'DELETE',
    });
    if (!r.ok) throw new Error(await r.text());
    document.getElementById('alert-agent-' + agentName).remove();
    await loadAll();
  } catch (e) {
    const err = document.getElementById('fix-agent-' + agentName + '-err');
    if (err) { err.textContent = '⚠ Failed — ' + e.message; err.style.display = 'block'; }
  }
}

function dismissAlert(alertId) {
  const el = document.getElementById(alertId);
  if (el) el.remove();
}
```

- [ ] **Step 2: Verify in browser**

```bash
cd "/Users/bevo/Library/CloudStorage/GoogleDrive-kroberts2007@gmail.com/My Drive/TakeoffAI"
uv run uvicorn backend.api.main:app --port 8000 &
cd frontend/dist && python3 -m http.server 3000 &
```

Open `http://localhost:3000/health.html`.

Expected:
- 4 scorecards appear with values
- No red alerts if system is clean (nightly batch row shows in green)
- If any agents are flagged, red alert rows appear with → fix button
- Clicking → fix expands the fix panel inline
- Console has no errors

```bash
kill %1 %2
```

- [ ] **Step 3: Commit**

```bash
cd "/Users/bevo/Library/CloudStorage/GoogleDrive-kroberts2007@gmail.com/My Drive/TakeoffAI"
git add frontend/dist/health.html
git commit -m "feat: render Overview tab with scorecards, agent alerts, and expand-in-place fix"
```

---

## Task 8: Agents Tab

**Files:**
- Modify: `frontend/dist/health.html`

- [ ] **Step 1: Replace `renderAgents` placeholder**

Find:
```javascript
function renderAgents()   { document.getElementById('panel-agents').innerHTML   = '<div class="loading">Agents loading...</div>'; }
```

Replace with:

```javascript
function renderAgents() {
  const { accuracy } = allData;
  const ALL_AGENTS = ['conservative', 'balanced', 'aggressive', 'historical_match', 'market_beater'];

  if (!accuracy) {
    document.getElementById('panel-agents').innerHTML =
      '<div class="empty">No accuracy data yet for this client — run some tournaments first.</div>';
    return;
  }

  const rows = ALL_AGENTS.map(agent => {
    const stats = accuracy[agent] || {};
    const flagged = stats.red_flagged || false;
    const avgDev = stats.avg_deviation_pct;
    const history = stats.deviation_history || [];
    // Inline sparkline: small bars showing last 5 deviations
    const sparkline = history.map(v => {
      const h = Math.min(14, Math.max(2, Math.abs(v) * 1.2));
      const color = Math.abs(v) > 5 ? '#f87171' : '#4ade80';
      return `<span style="display:inline-block;width:6px;height:${h}px;background:${color};border-radius:1px;margin-right:1px;vertical-align:bottom"></span>`;
    }).join('');

    return `
    <div class="agent-row${flagged ? ' flagged' : ''}">
      <span class="agent-name">${agent}</span>
      <span class="agent-stats">${avgDev != null ? 'avg dev: ' + Math.abs(avgDev).toFixed(1) + '%' : 'no data'}</span>
      <span style="display:flex;gap:2px;align-items:flex-end;height:16px">${sparkline || '<span class="c-faint">—</span>'}</span>
      <span class="agent-status ${flagged ? 'c-red' : 'c-green'}">${flagged ? '⚠ flagged' : '● healthy'}</span>
    </div>`;
  }).join('');

  const brier = accuracy.brier_score;
  const brierVerdict = brier == null ? 'Not enough data (need 5+ outcomes)'
    : brier < 0.25 ? '✓ Well calibrated'
    : brier < 0.50 ? '~ Moderate'
    : '⚠ Poor calibration';
  const brierColor = brier == null ? 'c-muted' : brier < 0.25 ? 'c-green' : brier < 0.50 ? 'c-yellow' : 'c-red';
  const predCount = accuracy.win_prob_predictions_count || 0;
  const recommended = accuracy.recommended_agent;

  document.getElementById('panel-agents').innerHTML = `
    <div class="section-title">AGENT HEALTH — ${currentClient}</div>
    ${rows}
    <div style="margin-top:14px;padding-top:10px;border-top:1px solid var(--border);color:var(--faint);font-size:10px;display:flex;gap:24px">
      <span>Brier score: <span class="${brierColor}">${brier != null ? brier.toFixed(2) : '—'}</span> &nbsp;${brierVerdict}</span>
      <span>Predictions tracked: ${predCount}</span>
      ${recommended ? `<span>Recommended: <span class="c-green">${recommended}</span></span>` : ''}
    </div>
  `;
}
```

- [ ] **Step 2: Verify in browser**

Start servers, open `http://localhost:3000/health.html`, click the AGENTS tab.

Expected:
- 5 agent rows with name, avg deviation, sparkline bars, health status
- Flagged agents appear with red background
- Brier score footer shows correct values or "Not enough data" message

- [ ] **Step 3: Commit**

```bash
git add frontend/dist/health.html
git commit -m "feat: render Agents tab with per-agent deviation, sparklines, and Brier score"
```

---

## Task 9: Prices Tab

**Files:**
- Modify: `frontend/dist/health.html`

- [ ] **Step 1: Replace `renderPrices` placeholder**

Find:
```javascript
function renderPrices()   { document.getElementById('panel-prices').innerHTML   = '<div class="loading">Prices loading...</div>'; }
```

Replace with:

```javascript
function renderPrices() {
  const { audit } = allData;

  if (!audit || audit.length === 0) {
    document.getElementById('panel-prices').innerHTML =
      '<div class="empty">No price verification runs yet — click ▶ Run Check Now to verify prices.</div>';
    return;
  }

  // Filter logic
  const filtered = queueFilterFlagged ? audit.filter(r => r.flagged) : audit;

  const filterBar = `
    <div class="filter-bar">
      <label>Show:</label>
      <button class="filter-toggle${queueFilterFlagged ? ' on' : ''}" onclick="togglePriceFilter()">
        ${queueFilterFlagged ? '⚠ Flagged only' : 'All records'}
      </button>
      <span style="color:var(--faint)">${filtered.length} of ${audit.length} records</span>
    </div>`;

  const rows = filtered.map(r => {
    const dev = r.deviation_pct != null ? r.deviation_pct.toFixed(1) + '%' : '—';
    const devColor = r.flagged ? 'c-red' : 'c-muted';
    const triggeredBadge = `<span class="badge badge-blue">${r.triggered_by}</span>`;
    const autoUpdated = r.auto_updated ? '<span class="badge badge-green">✓ updated</span>' : '';
    const flagged = r.flagged ? '<span class="badge badge-red">⚠ flagged</span>' : '';
    return `
      <tr>
        <td style="color:var(--text)">${r.line_item || '—'}</td>
        <td>${r.unit || '—'}</td>
        <td>${fmt$(r.ai_unit_cost)}</td>
        <td>${fmt$(r.verified_mid)}</td>
        <td class="${devColor}">${dev} ${flagged}</td>
        <td>${r.source_count || 0}</td>
        <td>${triggeredBadge}</td>
        <td>${autoUpdated}</td>
        <td style="color:var(--faint);font-size:10px">${r.created_at ? new Date(r.created_at).toLocaleDateString() : '—'}</td>
      </tr>`;
  }).join('');

  document.getElementById('panel-prices').innerHTML = `
    <div class="section-title">PRICE AUDIT LOG</div>
    ${filterBar}
    <table class="data-table">
      <thead>
        <tr>
          <th>ITEM</th><th>UNIT</th><th>AI PRICE</th><th>WEB PRICE</th>
          <th>DEVIATION</th><th>SOURCES</th><th>TRIGGER</th><th>UPDATED</th><th>DATE</th>
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>
  `;
}

function togglePriceFilter() {
  queueFilterFlagged = !queueFilterFlagged;
  renderPrices();
}
```

- [ ] **Step 2: Verify in browser**

Click the PRICES tab.

Expected:
- Table of all audit records
- "All records" / "⚠ Flagged only" filter toggle works
- Flagged rows show deviation in red with ⚠ badge
- Auto-updated rows show green ✓ updated badge

- [ ] **Step 3: Commit**

```bash
git add frontend/dist/health.html
git commit -m "feat: render Prices tab with audit table and flagged filter"
```

---

## Task 10: Queue Tab — Inline Accept / Keep / Custom

**Files:**
- Modify: `frontend/dist/health.html`

- [ ] **Step 1: Replace `renderQueue` placeholder**

Find:
```javascript
function renderQueue()    { document.getElementById('panel-queue').innerHTML    = '<div class="loading">Queue loading...</div>'; }
```

Replace with:

```javascript
function renderQueue() {
  const { queue } = allData;

  if (!queue || queue.length === 0) {
    document.getElementById('panel-queue').innerHTML =
      '<div class="empty">✓ Nothing to review — all prices verified.</div>';
    return;
  }

  const cards = queue.map(item => {
    const sources = (() => {
      try {
        const arr = JSON.parse(item.sources || '[]');
        return arr.map(s => `${s.source} ${fmt$(s.price)}`).join(' · ');
      } catch { return '—'; }
    })();
    const devColor = item.deviation_pct != null && Math.abs(item.deviation_pct) > 5 ? 'c-red' : 'c-yellow';
    const devText = item.deviation_pct != null ? (item.deviation_pct > 0 ? '+' : '') + item.deviation_pct.toFixed(1) + '%' : '—';

    return `
    <div class="queue-card" id="qcard-${item.id}">
      <div class="queue-card-header">
        <span class="queue-card-item">${item.line_item} · ${item.unit}</span>
        <span class="${devColor}">${devText}</span>
      </div>
      <div class="queue-card-sources">
        AI: ${fmt$(item.ai_unit_cost)} &nbsp;·&nbsp; Web: ${fmt$(item.verified_mid)}
        ${sources ? ' &nbsp;·&nbsp; ' + sources : ''}
      </div>
      <div class="queue-card-actions">
        <button class="btn-accept" onclick="resolveQueue(${item.id}, 'approved', null, this)">✓ Accept ${fmt$(item.verified_mid)}</button>
        <button class="btn-reject" onclick="resolveQueue(${item.id}, 'rejected', null, this)">✗ Keep ${fmt$(item.ai_unit_cost)}</button>
        <input class="custom-price-input" id="custom-${item.id}" placeholder="$ custom" type="number" step="0.01" min="0" />
        <button class="btn-set" onclick="resolveQueue(${item.id}, 'approved', parseFloat(document.getElementById('custom-${item.id}').value)||null, this)">→ set</button>
      </div>
      <div class="error-msg" id="qerr-${item.id}" style="display:none"></div>
    </div>`;
  }).join('');

  document.getElementById('panel-queue').innerHTML = `
    <div class="section-title">${queue.length} ITEM${queue.length > 1 ? 'S' : ''} PENDING REVIEW</div>
    ${cards}
  `;
}

async function resolveQueue(queueId, status, customPrice, btn) {
  btn.disabled = true;
  const body = { status };
  if (customPrice != null && !isNaN(customPrice) && customPrice > 0) body.custom_price = customPrice;
  try {
    const r = await fetch(`/api/verify/queue/${queueId}`, {
      method: 'PATCH',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    });
    if (!r.ok) throw new Error(await r.text());

    // Flash ✓ Done then remove card
    const card = document.getElementById('qcard-' + queueId);
    if (card) {
      card.innerHTML = '<div style="color:var(--green);padding:8px;font-size:11px">✓ Done</div>';
      setTimeout(() => {
        card.remove();
        // Decrement badge
        const badge = document.getElementById('queueBadge');
        const cur = parseInt(badge.textContent) - 1;
        badge.textContent = cur;
        if (cur <= 0) badge.style.display = 'none';
        // Re-render overview scorecards
        allData.queue = allData.queue.filter(q => q.id !== queueId);
        renderOverview();
      }, 400);
    }
  } catch (e) {
    btn.disabled = false;
    const err = document.getElementById('qerr-' + queueId);
    if (err) { err.textContent = '⚠ Failed — ' + e.message; err.style.display = 'block'; }
  }
}
```

- [ ] **Step 2: Verify in browser**

Click the QUEUE tab.

Expected:
- If queue is empty: "✓ Nothing to review" message
- If items exist: cards with AI price vs web price, deviation %, source breakdown
- Click "✓ Accept $X.XX" — card flashes ✓ Done, disappears, badge decrements
- Click "✗ Keep $X.XX" — same UI behavior, CSV not updated
- Type custom price, click → set — sends custom_price in payload

- [ ] **Step 3: Commit**

```bash
git add frontend/dist/health.html
git commit -m "feat: render Queue tab with inline accept/keep/custom resolution"
```

---

## Task 11: End-to-End Smoke Test

**Files:**
- No new files

- [ ] **Step 1: Start the full stack**

```bash
cd "/Users/bevo/Library/CloudStorage/GoogleDrive-kroberts2007@gmail.com/My Drive/TakeoffAI"
uv run uvicorn backend.api.main:app --port 8000 &
cd frontend/dist && python3 -m http.server 3000 &
sleep 2
```

- [ ] **Step 2: Verify all backend endpoints respond**

```bash
# Health check
curl -s http://localhost:8000/api/health | python3 -m json.tool

# POST /api/verify/run (will be slow — actually hits web sources)
curl -s -X POST http://localhost:8000/api/verify/run | python3 -m json.tool | head -10

# GET /api/verify/audit
curl -s "http://localhost:8000/api/verify/audit?limit=3" | python3 -m json.tool | head -20

# GET /api/verify/queue
curl -s http://localhost:8000/api/verify/queue | python3 -m json.tool

# Test exclude-agent
curl -s -X POST http://localhost:8000/api/client/default/exclude-agent \
  -H "Content-Type: application/json" \
  -d '{"agent_name": "aggressive"}' | python3 -m json.tool

# Test reset history
curl -s -X DELETE http://localhost:8000/api/client/default/agent-history/aggressive | python3 -m json.tool
```

Expected: All return JSON with correct shapes, no 500 errors.

- [ ] **Step 3: Verify dashboard in browser**

Open `http://localhost:3000/health.html`

Checklist:
- [ ] Page loads, no JS console errors
- [ ] 4 scorecards show data
- [ ] Tab switching works (click each tab)
- [ ] AGENTS tab shows 5 agent rows
- [ ] PRICES tab shows table with records (or empty state message)
- [ ] QUEUE tab shows items or "✓ Nothing to review"
- [ ] "▶ Run Check Now" button shows spinner when clicked
- [ ] Client switcher — type a different client ID, data refreshes

- [ ] **Step 4: Run full test suite one final time**

```bash
cd "/Users/bevo/Library/CloudStorage/GoogleDrive-kroberts2007@gmail.com/My Drive/TakeoffAI"
uv run pytest tests/ -v --tb=short
```

Expected: All PASS

- [ ] **Step 5: Kill servers and commit**

```bash
kill %1 %2
git add -A
git commit -m "chore: health dashboard smoke test complete — all endpoints and UI verified"
```

---

## Self-Review Checklist

| Spec Requirement | Covered In |
|---|---|
| Tabbed layout (Overview, Agents, Prices, Queue) | Task 6 — 4 tabs with switchTab() |
| 4 scorecards (Agents Healthy, Queue Count, Brier, Prices Verified) | Task 7 — renderOverview() |
| Alert strip with → fix per active issue | Task 7 — agentAlerts, queueAlert |
| Expand-in-place fix (no modal) | Task 7 — toggleFix() + fix-panel divs |
| Exclude from tournaments fix action | Task 7 — excludeAgent() → POST /api/client/{id}/exclude-agent |
| Keep & watch (dismiss locally) | Task 7 — dismissAlert() |
| Reset history fix action | Task 7 — resetAgentHistory() → DELETE /api/client/{id}/agent-history/{agent} |
| Nightly batch status row | Task 7 — nightlyAlert with age check |
| Agents tab with ELO, deviation, sparklines, health status | Task 8 |
| Brier score + calibration verdict in Agents footer | Task 8 |
| Prices tab with full audit table | Task 9 |
| Prices tab flagged-only filter toggle | Task 9 — togglePriceFilter() |
| Queue tab with Accept / Keep / Custom | Task 10 |
| Queue resolution calls PATCH /api/verify/queue/{id} | Task 10 — resolveQueue() |
| Queue badge decrements on resolution | Task 10 — badge update after resolve |
| ✓ Done flash before card removal | Task 10 — 400ms flash |
| Run Check Now button → POST /api/verify/run | Task 6 — runCheck() |
| POST /api/verify/run endpoint | Task 1 |
| CSV update on queue approval | Task 2 — _update_seed_csv called on approved |
| custom_price ±5% band | Task 2 — new_low/new_high calculation |
| POST /api/client/{id}/exclude-agent | Task 3 |
| DELETE /api/client/{id}/agent-history/{agent} | Task 3 |
| tournament.py skips excluded_agents | Task 4 |
| nginx /health route | Task 5 |
| Client switcher with debounce + URL update | Task 6 — clientInput handler |
| Parallel data loading | Task 6 — Promise.all in loadAll() |
| Per-section error handling | Task 6 — safeFetch() returns null on error |
| Empty states for all tabs | Tasks 7-10 — each renderX checks for empty data |
