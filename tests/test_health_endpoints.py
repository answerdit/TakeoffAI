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
