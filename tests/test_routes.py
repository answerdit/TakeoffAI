"""Tests for FastAPI routes — health endpoint and basic 422 validation."""

import pytest
from httpx import AsyncClient, ASGITransport

from backend.api.main import app


@pytest.mark.anyio
async def test_health_ok(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["product"] == "TakeoffAI"


@pytest.mark.anyio
async def test_estimate_missing_fields():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post("/api/estimate", json={})
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_bid_strategy_missing_fields():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post("/api/bid/strategy", json={})
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_tournament_get_not_found(client):
    resp = await client.get("/api/tournament/999999")
    assert resp.status_code == 404


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
