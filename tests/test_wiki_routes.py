"""Tests for wiki API routes — job CRUD and lint."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from httpx import AsyncClient, ASGITransport

from backend.api.main import app


@pytest.mark.anyio
async def test_job_create(monkeypatch, tmp_path):
    """POST /api/job/create should create a job and return slug."""
    monkeypatch.setenv("API_KEY", "test-key")

    import backend.agents.wiki_manager as wm
    monkeypatch.setattr(wm, "WIKI_DIR", tmp_path)
    monkeypatch.setattr(wm, "JOBS_DIR", tmp_path / "jobs")
    monkeypatch.setattr(wm, "CLIENTS_DIR", tmp_path / "clients")

    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="# Test Job\n\n## Scope\nBuild something.")]
    mock_client = AsyncMock()
    mock_client.messages.create = AsyncMock(return_value=mock_response)
    monkeypatch.setattr(wm, "_anthropic", mock_client)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post("/api/job/create", json={
            "client_id": "acme",
            "project_name": "Test Job",
            "description": "Build a test structure for testing purposes",
            "zip_code": "78701",
            "trade_type": "general",
        }, headers={"X-API-Key": "test-key"})

    assert resp.status_code == 200
    data = resp.json()
    assert "job_slug" in data
    assert data["status"] == "prospect"


@pytest.mark.anyio
async def test_job_create_missing_fields(monkeypatch):
    """POST /api/job/create with missing fields should return 422."""
    monkeypatch.setenv("API_KEY", "test-key")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post("/api/job/create", json={}, headers={"X-API-Key": "test-key"})
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_job_get_not_found(monkeypatch, tmp_path):
    """GET /api/job/{slug} for nonexistent job should return 404."""
    monkeypatch.setenv("API_KEY", "test-key")

    import backend.agents.wiki_manager as wm
    monkeypatch.setattr(wm, "JOBS_DIR", tmp_path / "jobs")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/api/job/nonexistent-job", headers={"X-API-Key": "test-key"})
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_job_update_status(monkeypatch, tmp_path):
    """POST /api/job/update should advance job status."""
    monkeypatch.setenv("API_KEY", "test-key")

    import backend.agents.wiki_manager as wm
    monkeypatch.setattr(wm, "WIKI_DIR", tmp_path)
    monkeypatch.setattr(wm, "JOBS_DIR", tmp_path / "jobs")
    monkeypatch.setattr(wm, "CLIENTS_DIR", tmp_path / "clients")
    monkeypatch.setattr(wm, "PERSONALITIES_DIR", tmp_path / "personalities")
    monkeypatch.setattr(wm, "MATERIALS_DIR", tmp_path / "materials")

    job_path = tmp_path / "jobs" / "test-job.md"
    wm._write_page(
        job_path,
        {"status": "tournament-complete", "client": "acme", "tournament_id": 1, "winner_personality": "balanced"},
        "# Test\n\n## Scope\nBuild it.",
    )

    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="## Bid Decision\nGoing with $150K.")]
    mock_client = AsyncMock()
    mock_client.messages.create = AsyncMock(return_value=mock_response)
    monkeypatch.setattr(wm, "_anthropic", mock_client)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post("/api/job/update", json={
            "job_slug": "test-job",
            "status": "bid-submitted",
            "our_bid": 150000,
        }, headers={"X-API-Key": "test-key"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "bid-submitted"


@pytest.mark.anyio
async def test_jobs_list(monkeypatch, tmp_path):
    """GET /api/jobs should return list of job frontmatter."""
    monkeypatch.setenv("API_KEY", "test-key")

    import backend.agents.wiki_manager as wm
    monkeypatch.setattr(wm, "JOBS_DIR", tmp_path / "jobs")

    (tmp_path / "jobs").mkdir(parents=True)
    wm._write_page(
        tmp_path / "jobs" / "job-a.md",
        {"status": "prospect", "client": "acme", "date": "2026-04-06", "trade": "general", "zip": "78701"},
        "# Job A",
    )
    wm._write_page(
        tmp_path / "jobs" / "job-b.md",
        {"status": "won", "client": "bob", "date": "2026-04-05", "trade": "concrete", "zip": "76801"},
        "# Job B",
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/api/jobs", headers={"X-API-Key": "test-key"})

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2


@pytest.mark.anyio
async def test_jobs_list_filter_active(monkeypatch, tmp_path):
    """GET /api/jobs?status=active should exclude closed and lost."""
    monkeypatch.setenv("API_KEY", "test-key")

    import backend.agents.wiki_manager as wm
    monkeypatch.setattr(wm, "JOBS_DIR", tmp_path / "jobs")

    (tmp_path / "jobs").mkdir(parents=True)
    wm._write_page(tmp_path / "jobs" / "active.md", {"status": "prospect", "client": "a"}, "# Active")
    wm._write_page(tmp_path / "jobs" / "done.md", {"status": "closed", "client": "b"}, "# Done")
    wm._write_page(tmp_path / "jobs" / "lost.md", {"status": "lost", "client": "c"}, "# Lost")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/api/jobs?status=active", headers={"X-API-Key": "test-key"})

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["status"] == "prospect"


@pytest.mark.anyio
async def test_wiki_lint_endpoint(monkeypatch, tmp_path):
    """GET /api/wiki/lint should return a lint report."""
    monkeypatch.setenv("API_KEY", "test-key")

    import backend.agents.wiki_manager as wm
    monkeypatch.setattr(wm, "WIKI_DIR", tmp_path)
    monkeypatch.setattr(wm, "JOBS_DIR", tmp_path / "jobs")
    monkeypatch.setattr(wm, "CLIENTS_DIR", tmp_path / "clients")
    monkeypatch.setattr(wm, "MATERIALS_DIR", tmp_path / "materials")
    monkeypatch.setattr(wm, "PERSONALITIES_DIR", tmp_path / "personalities")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/api/wiki/lint", headers={"X-API-Key": "test-key"})

    assert resp.status_code == 200
    data = resp.json()
    assert "broken_links" in data
    assert "stale_jobs" in data
