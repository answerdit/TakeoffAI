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
    asyncio.run(setup())
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


def test_post_verify_run_returns_500_on_error(client):
    with patch("backend.api.verification.run_verification_batch",
               new=AsyncMock(side_effect=RuntimeError("network timeout"))):
        resp = client.post("/api/verify/run")
    assert resp.status_code == 500
    assert "network timeout" in resp.json().get("detail", "")


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

    asyncio.run(setup())

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

    asyncio.run(setup())

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

    asyncio.run(setup())

    app = make_app(db_path)
    with TestClient(app) as c:
        with patch("backend.api.verification._update_seed_csv") as mock_csv:
            resp = c.patch("/api/verify/queue/1", json={"status": "rejected"})

    assert resp.status_code == 200
    mock_csv.assert_not_called()


def test_exclude_agent_adds_to_profile(tmp_path, monkeypatch):
    """POST /api/client/{id}/exclude-agent adds agent to excluded_agents list."""
    import backend.agents.feedback_loop as fl
    monkeypatch.setattr(fl, "PROFILES_DIR", tmp_path)

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
