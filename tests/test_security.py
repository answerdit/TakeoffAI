"""Security hardening tests — auth, error handling, uploads, rate limiting."""

import pytest
from httpx import AsyncClient, ASGITransport

from backend.api.main import app


# ── Fix 1: Auth on all routers ──────────────────────────────────────────────

@pytest.mark.anyio
async def test_upload_endpoint_requires_api_key(monkeypatch):
    """Upload endpoints should return 403 without a valid API key."""
    monkeypatch.setenv("API_KEY", "test-secret-key")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post("/api/upload/bids/manual", json={"client_id": "x", "bids": []})
    assert resp.status_code == 403


@pytest.mark.anyio
async def test_verification_endpoint_requires_api_key(monkeypatch):
    """Verification endpoints should return 403 without a valid API key."""
    monkeypatch.setenv("API_KEY", "test-secret-key")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post("/api/verify/estimate", json={"line_items": []})
    assert resp.status_code == 403


# ── Fix 2: Fail-closed API key ─────────────────────────────────────────────

@pytest.mark.anyio
async def test_empty_api_key_rejects_requests(monkeypatch):
    """When API_KEY env var is empty, all authenticated endpoints must reject."""
    monkeypatch.setenv("API_KEY", "")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post("/api/estimate", json={
            "description": "Build a warehouse",
            "zip_code": "75001",
        })
    assert resp.status_code == 403


@pytest.mark.anyio
async def test_health_remains_unauthenticated(monkeypatch):
    """Health endpoint must work without any API key."""
    monkeypatch.setenv("API_KEY", "test-secret-key")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/api/health")
    assert resp.status_code == 200
