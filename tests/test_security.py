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


# ── Fixes 4/6: Generic error messages ──────────────────────────────────────

@pytest.mark.anyio
async def test_500_error_does_not_leak_details(monkeypatch):
    """500 responses must say 'Internal server error', not the raw exception."""
    monkeypatch.setenv("API_KEY", "test-key")
    from unittest.mock import AsyncMock, patch
    with patch("backend.agents.pre_bid_calc.run_prebid_calc",
               new=AsyncMock(side_effect=RuntimeError("secret DB connection string leaked"))):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post(
                "/api/estimate",
                json={"description": "Build a 10000 sqft warehouse", "zip_code": "75001"},
                headers={"X-API-Key": "test-key"},
            )
    assert resp.status_code == 500
    assert "secret" not in resp.json()["detail"]
    assert resp.json()["detail"] == "Internal server error"


# ── Fix 3: File size limit ─────────────────────────────────────────────────

@pytest.mark.anyio
async def test_oversized_upload_returns_413(monkeypatch):
    """Uploads over 10MB must be rejected with 413."""
    monkeypatch.setenv("API_KEY", "test-key")
    import io
    big_content = b"a" * (10 * 1024 * 1024 + 1)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post(
            "/api/upload/bids/csv",
            files={"file": ("big.csv", io.BytesIO(big_content), "text/csv")},
            data={"client_id": "test"},
            headers={"X-API-Key": "test-key"},
        )
    assert resp.status_code == 413


# ── Fix 7: Client ID sanitization in uploads ──────────────────────────────

@pytest.mark.anyio
async def test_malicious_client_id_in_upload_returns_400(monkeypatch):
    """client_id with path traversal chars must be rejected."""
    monkeypatch.setenv("API_KEY", "test-key")
    import io
    csv_content = b"project_name,zip_code,bid_date,your_bid_amount\nTest,75001,2024-01-01,100000\n"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post(
            "/api/upload/bids/csv",
            files={"file": ("bids.csv", io.BytesIO(csv_content), "text/csv")},
            data={"client_id": "../../../etc/passwd"},
            headers={"X-API-Key": "test-key"},
        )
    assert resp.status_code == 400


# ── Fix 8b: File type content validation ───────────────────────────────────

@pytest.mark.anyio
async def test_binary_file_renamed_to_csv_returns_400(monkeypatch):
    """A binary file with .csv extension must be rejected."""
    monkeypatch.setenv("API_KEY", "test-key")
    import io
    binary_content = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post(
            "/api/upload/bids/csv",
            files={"file": ("fake.csv", io.BytesIO(binary_content), "text/csv")},
            data={"client_id": "test"},
            headers={"X-API-Key": "test-key"},
        )
    assert resp.status_code == 400


@pytest.mark.anyio
async def test_csv_renamed_to_xlsx_returns_400(monkeypatch):
    """A CSV file with .xlsx extension must be rejected (no PK magic bytes)."""
    monkeypatch.setenv("API_KEY", "test-key")
    import io
    csv_content = b"project_name,zip_code,bid_date,your_bid_amount\nTest,75001,2024-01-01,100000\n"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post(
            "/api/upload/bids/excel",
            files={"file": ("fake.xlsx", io.BytesIO(csv_content), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            data={"client_id": "test"},
            headers={"X-API-Key": "test-key"},
        )
    assert resp.status_code == 400
