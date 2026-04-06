"""Tests for FastAPI routes — health endpoint and basic 422 validation."""

import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import AsyncMock, patch

from backend.api.main import app


@pytest.mark.anyio
async def test_health_ok(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["product"] == "TakeoffAI"


@pytest.mark.anyio
async def test_estimate_missing_fields(monkeypatch):
    monkeypatch.setenv("API_KEY", "test-key")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post("/api/estimate", json={}, headers={"X-API-Key": "test-key"})
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_bid_strategy_missing_fields(monkeypatch):
    monkeypatch.setenv("API_KEY", "test-key")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post("/api/bid/strategy", json={}, headers={"X-API-Key": "test-key"})
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_tournament_get_not_found(client):
    resp = await client.get("/api/tournament/999999", headers={"X-API-Key": "test-key"})
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_tournament_run_n_samples_invalid(monkeypatch):
    """n_samples > 5 should return 422."""
    monkeypatch.setenv("API_KEY", "test-key")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post("/api/tournament/run", json={
            "description": "Build a 10,000 sqft warehouse in Houston TX",
            "zip_code": "77001",
            "n_samples": 99,
        }, headers={"X-API-Key": "test-key"})
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_tournament_run_n_samples_zero_invalid(monkeypatch):
    """n_samples=0 should return 422."""
    monkeypatch.setenv("API_KEY", "test-key")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post("/api/tournament/run", json={
            "description": "Build a 10,000 sqft warehouse in Houston TX",
            "zip_code": "77001",
            "n_samples": 0,
        }, headers={"X-API-Key": "test-key"})
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_estimate_passes_through_confidence_band(monkeypatch):
    """estimate endpoint should pass estimate_low and estimate_high from the agent."""
    monkeypatch.setenv("API_KEY", "test-key")

    mock_result = {
        "project_summary": "Small office build",
        "location": "75001",
        "line_items": [],
        "subtotal": 100000.0,
        "overhead_pct": 20,
        "overhead_amount": 20000.0,
        "margin_pct": 12,
        "margin_amount": 14400.0,
        "total_bid": 134400.0,
        "estimate_low": 118000.0,
        "estimate_high": 151000.0,
        "confidence": "medium",
        "notes": "Test note",
    }

    with patch("backend.api.routes.run_prebid_calc", new=AsyncMock(return_value=mock_result)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post(
                "/api/estimate",
                json={"description": "Build a small office", "zip_code": "75001"},
                headers={"X-API-Key": "test-key"},
            )

    assert resp.status_code == 200
    data = resp.json()
    assert "estimate_low" in data
    assert "estimate_high" in data
    assert data["estimate_low"] < data["total_bid"] < data["estimate_high"]


@pytest.mark.anyio
async def test_estimate_without_confidence_band_fields(monkeypatch):
    """estimate endpoint handles agents that don't return estimate_low/estimate_high (old schema)."""
    monkeypatch.setenv("API_KEY", "test-key")

    mock_result = {
        "project_summary": "Small office build",
        "location": "75001",
        "line_items": [],
        "subtotal": 100000.0,
        "overhead_pct": 20,
        "overhead_amount": 20000.0,
        "margin_pct": 12,
        "margin_amount": 14400.0,
        "total_bid": 134400.0,
        "confidence": "medium",
        "notes": "",
    }

    with patch("backend.api.routes.run_prebid_calc", new=AsyncMock(return_value=mock_result)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post(
                "/api/estimate",
                json={"description": "Build a small office", "zip_code": "75001"},
                headers={"X-API-Key": "test-key"},
            )

    assert resp.status_code == 200
    data = resp.json()
    assert "total_bid" in data
    # Fields absent from agent response should not appear in API response
    assert "estimate_low" not in data
    assert "estimate_high" not in data
