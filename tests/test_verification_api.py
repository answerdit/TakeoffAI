import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import aiosqlite
import pytest
from fastapi.testclient import TestClient


def make_test_app(db_path: str):
    """Create a minimal FastAPI app with the verification router wired up."""
    from fastapi import FastAPI

    import backend.api.verification as vmod
    from backend.api.verification import verification_router

    vmod.DB_PATH = db_path
    app = FastAPI()
    app.include_router(verification_router, prefix="/api")
    return app


@pytest.fixture
def db_and_client(tmp_path):
    db_path = str(tmp_path / "test.db")
    import asyncio

    from backend.api.main import _CREATE_TABLES

    asyncio.run(_setup_db(db_path, _CREATE_TABLES))
    app = make_test_app(db_path)
    with TestClient(app) as client:
        yield db_path, client


async def _setup_db(db_path, ddl):
    async with aiosqlite.connect(db_path) as db:
        await db.executescript(ddl)
        await db.commit()


def test_get_audit_empty(db_and_client):
    _, client = db_and_client
    resp = client.get("/api/verify/audit")
    assert resp.status_code == 200
    assert resp.json() == []


def test_get_queue_empty(db_and_client):
    _, client = db_and_client
    resp = client.get("/api/verify/queue")
    assert resp.status_code == 200
    assert resp.json() == []


def test_verify_estimate_endpoint(db_and_client):
    db_path, client = db_and_client
    with patch(
        "backend.api.verification.verify_line_items",
        new=AsyncMock(
            return_value=[
                {
                    "audit_id": 1,
                    "line_item": "Concrete",
                    "unit": "CY",
                    "ai_unit_cost": 150.0,
                    "verified_mid": 160.0,
                    "deviation_pct": -6.25,
                    "flagged": 1,
                    "source_count": 3,
                    "auto_updated": 0,
                }
            ]
        ),
    ):
        resp = client.post(
            "/api/verify/estimate",
            json={
                "line_items": [
                    {"description": "Concrete", "unit": "CY", "unit_material_cost": 150.0}
                ]
            },
        )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["flagged"] == 1


def test_patch_queue_resolve(db_and_client):
    db_path, client = db_and_client

    import asyncio

    async def seed_queue():
        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                "INSERT INTO price_audit (triggered_by, line_item, unit, ai_unit_cost, source_count) "
                "VALUES (?,?,?,?,?)",
                ("test", "Lumber", "LF", 0.60, 0),
            )
            await db.execute(
                "INSERT INTO review_queue (audit_id, line_item, unit, ai_unit_cost, verified_mid, deviation_pct) "
                "VALUES (?,?,?,?,?,?)",
                (1, "Lumber", "LF", 0.60, 1.00, -40.0),
            )
            await db.commit()

    asyncio.run(seed_queue())

    resp = client.patch(
        "/api/verify/queue/1",
        json={"status": "approved", "reviewer_notes": "confirmed price increase"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "approved"


def test_post_outcome(db_and_client, tmp_path):
    db_path, client = db_and_client

    import asyncio

    async def seed_tournament():
        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                "INSERT INTO bid_tournaments (id, client_id, project_description, zip_code) "
                "VALUES (?,?,?,?)",
                (10, "clientX", "test project", "76801"),
            )
            await db.execute(
                "INSERT INTO tournament_entries (tournament_id, agent_name, total_bid, won) "
                "VALUES (?,?,?,?)",
                (10, "balanced", 100000.0, 1),
            )
            await db.commit()

    asyncio.run(seed_tournament())

    with patch("backend.api.verification.record_actual_outcome", return_value={"calibration": {}}):
        resp = client.post(
            "/api/verify/outcome",
            json={
                "client_id": "clientX",
                "tournament_id": 10,
                "actual_cost": 95000.0,
                "won": True,
                "win_probability": 0.65,
            },
        )
    assert resp.status_code == 200


def test_get_accuracy_report(db_and_client, tmp_path, monkeypatch):
    _, client = db_and_client
    with patch(
        "backend.api.verification.get_agent_accuracy_report",
        return_value={
            "conservative": {
                "avg_deviation_pct": 1.5,
                "red_flagged": False,
                "deviation_history": [],
            },
            "balanced": {"avg_deviation_pct": 0.4, "red_flagged": False, "deviation_history": []},
            "aggressive": {"avg_deviation_pct": 8.0, "red_flagged": True, "deviation_history": []},
            "historical_match": {
                "avg_deviation_pct": 0.3,
                "red_flagged": False,
                "deviation_history": [],
            },
            "market_beater": {
                "avg_deviation_pct": 2.1,
                "red_flagged": False,
                "deviation_history": [],
            },
            "recommended_agent": "historical_match",
            "brier_score": 0.18,
            "win_prob_predictions_count": 5,
        },
    ):
        resp = client.get("/api/verify/accuracy/clientX")
    assert resp.status_code == 200
    data = resp.json()
    assert data["recommended_agent"] == "historical_match"
    assert data["aggressive"]["red_flagged"] is True
