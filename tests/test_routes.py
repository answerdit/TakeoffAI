"""Tests for FastAPI routes — health endpoint and basic 422 validation."""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

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
        resp = await c.post(
            "/api/tournament/run",
            json={
                "description": "Build a 10,000 sqft warehouse in Houston TX",
                "zip_code": "77001",
                "n_samples": 99,
            },
            headers={"X-API-Key": "test-key"},
        )
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_tournament_run_n_samples_zero_invalid(monkeypatch):
    """n_samples=0 should return 422."""
    monkeypatch.setenv("API_KEY", "test-key")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post(
            "/api/tournament/run",
            json={
                "description": "Build a 10,000 sqft warehouse in Houston TX",
                "zip_code": "77001",
                "n_samples": 0,
            },
            headers={"X-API-Key": "test-key"},
        )
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


@pytest.mark.anyio
async def test_estimate_with_job_slug_fires_wiki_hook(monkeypatch, tmp_path):
    """Estimate with job_slug should fire wiki enrich_estimate in background."""
    monkeypatch.setenv("API_KEY", "test-key")

    import backend.agents.wiki_manager as wm

    monkeypatch.setattr(wm, "JOBS_DIR", tmp_path / "jobs")

    # Pre-create job page
    (tmp_path / "jobs").mkdir(parents=True)
    wm._write_page(
        tmp_path / "jobs" / "test-job.md",
        {"status": "prospect", "client": "acme"},
        "# Test\n\n## Scope\nBuild.",
    )

    mock_result = {
        "project_summary": "Test",
        "location": "78701",
        "line_items": [],
        "subtotal": 100000.0,
        "overhead_pct": 20,
        "overhead_amount": 20000.0,
        "margin_pct": 12,
        "margin_amount": 14400.0,
        "total_bid": 134400.0,
        "estimate_low": 120000.0,
        "estimate_high": 150000.0,
        "confidence": "high",
        "notes": "",
    }

    with patch("backend.api.routes.run_prebid_calc", new=AsyncMock(return_value=mock_result)):
        with patch("backend.agents.wiki_manager.enrich_estimate", new=AsyncMock()) as mock_enrich:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post(
                    "/api/estimate",
                    json={
                        "description": "Build a small office",
                        "zip_code": "78701",
                        "job_slug": "test-job",
                    },
                    headers={"X-API-Key": "test-key"},
                )

            assert resp.status_code == 200
            # Give the background task a moment
            import asyncio

            await asyncio.sleep(0.1)
            mock_enrich.assert_called_once()


@pytest.mark.anyio
async def test_estimate_without_job_slug_no_wiki_call(monkeypatch):
    """Estimate without job_slug should NOT fire wiki hook."""
    monkeypatch.setenv("API_KEY", "test-key")

    mock_result = {
        "project_summary": "Test",
        "location": "78701",
        "line_items": [],
        "subtotal": 100000.0,
        "overhead_pct": 20,
        "overhead_amount": 20000.0,
        "margin_pct": 12,
        "margin_amount": 14400.0,
        "total_bid": 134400.0,
        "confidence": "high",
        "notes": "",
    }

    with patch("backend.api.routes.run_prebid_calc", new=AsyncMock(return_value=mock_result)):
        with patch("backend.agents.wiki_manager.enrich_estimate", new=AsyncMock()) as mock_enrich:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post(
                    "/api/estimate",
                    json={"description": "Build a small office", "zip_code": "78701"},
                    headers={"X-API-Key": "test-key"},
                )

            assert resp.status_code == 200
            mock_enrich.assert_not_called()


@pytest.mark.anyio
async def test_tournament_with_job_slug_fires_wiki_hook(monkeypatch, tmp_path):
    """Tournament with job_slug should fire wiki enrich_tournament in background."""
    monkeypatch.setenv("API_KEY", "test-key")

    import dataclasses
    from unittest.mock import MagicMock

    # Minimal mock of TournamentResult and TournamentEntry
    @dataclasses.dataclass
    class FakeEntry:
        agent_name: str = "balanced"
        total_bid: float = 150000.0
        margin_pct: float = 12.0
        confidence: str = "high"
        temperature: float = 0.7
        sample_index: int = 0
        estimate: dict = dataclasses.field(default_factory=dict)
        error: str = ""

    @dataclasses.dataclass
    class FakeResult:
        tournament_id: int = 1
        entries: list = dataclasses.field(default_factory=list)
        consensus_entries: list = dataclasses.field(default_factory=list)
        accuracy_annotations: dict = dataclasses.field(default_factory=dict)
        accuracy_recommended_agent: str = None
        rerank_active: bool = False

    entry = FakeEntry()
    fake_result = FakeResult(entries=[entry], consensus_entries=[entry])

    with patch("backend.api.routes.run_tournament", new=AsyncMock(return_value=fake_result)):
        with patch("backend.agents.wiki_manager.enrich_tournament", new=AsyncMock()) as mock_enrich:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post(
                    "/api/tournament/run",
                    json={
                        "description": "Build a 10,000 sqft warehouse in Houston TX",
                        "zip_code": "77001",
                        "job_slug": "test-job",
                    },
                    headers={"X-API-Key": "test-key"},
                )

            assert resp.status_code == 200
            body = resp.json()
            assert body["rerank_active"] is False
            import asyncio

            await asyncio.sleep(0.1)
            mock_enrich.assert_called_once()


@pytest.mark.anyio
async def test_tournament_run_preserves_accuracy_annotations_in_response(monkeypatch, tmp_path):
    """Regression: the route serializer must preserve avg_deviation_pct,
    closed_job_count, and is_accuracy_flagged on every consensus entry.
    Protects against refactors that silently drop annotation fields from
    `_serialize_entry`, or forget to pass `annotate=True` for consensus
    entries, or stop wiring `accuracy_annotations` through TournamentResult."""
    monkeypatch.setenv("API_KEY", "test-key")

    # Isolate wiki capture path from the real vault — the route fires
    # _wiki_capture_tournament unconditionally, which auto-stubs a page.
    import backend.agents._wiki_io as _io
    import backend.agents.wiki_manager as wm

    monkeypatch.setattr(_io, "JOBS_DIR", tmp_path / "jobs")
    monkeypatch.setattr(_io, "CLIENTS_DIR", tmp_path / "clients")
    monkeypatch.setattr(wm, "JOBS_DIR", tmp_path / "jobs")

    import dataclasses

    @dataclasses.dataclass
    class FakeEntry:
        agent_name: str = "balanced"
        total_bid: float = 150000.0
        margin_pct: float = 12.0
        confidence: str = "high"
        temperature: float = 0.7
        sample_index: int = 0
        estimate: dict = dataclasses.field(default_factory=dict)
        error: str = ""

    @dataclasses.dataclass
    class FakeResult:
        tournament_id: int = 1
        entries: list = dataclasses.field(default_factory=list)
        consensus_entries: list = dataclasses.field(default_factory=list)
        accuracy_annotations: dict = dataclasses.field(default_factory=dict)
        accuracy_recommended_agent: str = None
        rerank_active: bool = False

    # Two entries — one with full data, one flagged — so a field-drop
    # regression shows up as a concrete value mismatch, not just a missing key.
    entries = [
        FakeEntry(agent_name="balanced", total_bid=151_248.0),
        FakeEntry(agent_name="aggressive", total_bid=85_626.0, confidence="low"),
    ]
    annotations = {
        "balanced": {
            "avg_deviation_pct": 0.55,
            "closed_job_count": 6,
            "is_accuracy_flagged": False,
        },
        "aggressive": {
            "avg_deviation_pct": 10.10,
            "closed_job_count": 5,
            "is_accuracy_flagged": True,
        },
    }
    fake_result = FakeResult(
        entries=entries,
        consensus_entries=entries,
        accuracy_annotations=annotations,
        accuracy_recommended_agent="balanced",
        rerank_active=True,
    )

    with patch("backend.api.routes.run_tournament", new=AsyncMock(return_value=fake_result)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post(
                "/api/tournament/run",
                json={
                    "description": "Build a 10,000 sqft warehouse in Houston TX",
                    "zip_code": "77001",
                },
                headers={"X-API-Key": "test-key"},
            )

    assert resp.status_code == 200
    body = resp.json()

    # Top-level hybrid-rollout signals
    assert body["rerank_active"] is True
    assert body["accuracy_recommended_agent"] == "balanced"

    consensus = {e["agent_name"]: e for e in body["consensus_entries"]}
    assert set(consensus.keys()) == {"balanced", "aggressive"}

    # All three annotation fields must round-trip with correct VALUES — not
    # just be present. A refactor that stubs defaults would still fail this.
    assert consensus["balanced"]["avg_deviation_pct"] == 0.55
    assert consensus["balanced"]["closed_job_count"] == 6
    assert consensus["balanced"]["is_accuracy_flagged"] is False

    assert consensus["aggressive"]["avg_deviation_pct"] == 10.10
    assert consensus["aggressive"]["closed_job_count"] == 5
    assert consensus["aggressive"]["is_accuracy_flagged"] is True

    # Raw entries intentionally do NOT get annotations (only consensus does).
    # Guard the asymmetry so it isn't accidentally flipped.
    raw = body["entries"][0]
    assert "avg_deviation_pct" not in raw
    assert "closed_job_count" not in raw
    assert "is_accuracy_flagged" not in raw


@pytest.mark.anyio
async def test_tournament_run_without_job_slug_auto_creates_wiki_stub(monkeypatch, tmp_path):
    """Regression: when /api/tournament/run is called without a job_slug, the
    capture path must auto-create a stub wiki page and feed it to
    enrich_tournament. This is the wiring that makes the retrieval corpus
    fill up on its own — breaking it silently starves every downstream
    consumer of historical comparables."""
    monkeypatch.setenv("API_KEY", "test-key")

    import dataclasses

    import backend.agents._wiki_io as _io
    import backend.agents.wiki_manager as wm

    jobs_dir = tmp_path / "jobs"
    clients_dir = tmp_path / "clients"
    monkeypatch.setattr(_io, "JOBS_DIR", jobs_dir)
    monkeypatch.setattr(_io, "CLIENTS_DIR", clients_dir)
    monkeypatch.setattr(wm, "JOBS_DIR", jobs_dir)

    @dataclasses.dataclass
    class FakeEntry:
        agent_name: str = "balanced"
        total_bid: float = 150000.0
        margin_pct: float = 12.0
        confidence: str = "high"
        temperature: float = 0.7
        sample_index: int = 0
        estimate: dict = dataclasses.field(default_factory=dict)
        error: str = ""

    @dataclasses.dataclass
    class FakeResult:
        tournament_id: int = 999999
        entries: list = dataclasses.field(default_factory=list)
        consensus_entries: list = dataclasses.field(default_factory=list)
        accuracy_annotations: dict = dataclasses.field(default_factory=dict)
        accuracy_recommended_agent: str = None
        rerank_active: bool = False

    entry = FakeEntry()
    fake_result = FakeResult(entries=[entry], consensus_entries=[entry])

    with patch("backend.api.routes.run_tournament", new=AsyncMock(return_value=fake_result)):
        with patch(
            "backend.agents.wiki_manager.enrich_tournament", new=AsyncMock()
        ) as mock_enrich:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post(
                    "/api/tournament/run",
                    json={
                        "description": "Retrofit a 5,000 sqft medical office in Abilene TX",
                        "zip_code": "79601",
                        "trade_type": "general",
                        "client_id": "capturetest",
                    },
                    headers={"X-API-Key": "test-key"},
                )

            assert resp.status_code == 200

            import asyncio

            # Let the fire-and-forget helper run.
            await asyncio.sleep(0.1)

            # A stub page must have been written.
            written = list(jobs_dir.glob("*.md"))
            assert len(written) == 1, f"expected 1 stub page, got {[p.name for p in written]}"

            meta, body = _io._parse_frontmatter(written[0])
            assert meta["status"] == "prospect"
            assert meta["client"] == "capturetest"
            assert meta["trade"] == "general"
            assert meta["zip"] == "79601"
            assert meta["actual_cost"] is None
            assert "medical office" in body.lower()

            # Client page got auto-created via _ensure_client_page.
            assert (clients_dir / "capturetest.md").exists()

            # enrich_tournament must have been fired with the auto-created slug.
            mock_enrich.assert_called_once()
            args, _ = mock_enrich.call_args
            assert args[0] == written[0].stem  # slug matches the written filename


@pytest.mark.anyio
async def test_judge_historical_mode_cascades_to_linked_wiki_job(monkeypatch, tmp_path):
    """Regression: in HISTORICAL judge mode, the wiki cascade must close the
    linked job page with status=closed and actual_cost=actual_winning_bid.
    This is the second half of the capture loop — without it, judged
    tournaments never become part of the retrievable corpus."""
    import json
    import tempfile
    from pathlib import Path

    import aiosqlite

    import backend.agents._wiki_io as _io
    import backend.agents.wiki_manager as wm

    jobs_dir = tmp_path / "jobs"
    clients_dir = tmp_path / "clients"
    personalities_dir = tmp_path / "personalities"
    monkeypatch.setattr(_io, "JOBS_DIR", jobs_dir)
    monkeypatch.setattr(_io, "CLIENTS_DIR", clients_dir)
    monkeypatch.setattr(_io, "PERSONALITIES_DIR", personalities_dir)
    monkeypatch.setattr(wm, "JOBS_DIR", jobs_dir)

    # Seed a wiki job page that a prior tournament run would have created.
    jobs_dir.mkdir(parents=True)
    slug = "2026-04-11-acme-retrofit-office"
    seed_meta = {
        "status": "tournament-complete",
        "client": "acme",
        "date": "2026-04-11",
        "trade": "general",
        "zip": "78701",
        "tags": ["job", "tournament-complete", "general"],
        "our_bid": None,
        "estimate_total": None,
        "estimate_low": None,
        "estimate_high": None,
        "tournament_id": 1,
        "winner_personality": "balanced",
        "band_low": 148000.0,
        "band_high": 162000.0,
        "actual_cost": None,
        "outcome_date": None,
    }
    _io._write_page(
        jobs_dir / f"{slug}.md",
        seed_meta,
        "# Acme Retrofit Office\n\n## Scope\n\nFull retrofit.\n",
    )

    # Mock the cascade's LLM synthesis so no real API call fires.
    from unittest.mock import AsyncMock

    async def fake_synthesize(context: str, instruction: str) -> str:
        return "Job closed at the historical winning bid."

    import backend.agents._wiki_llm as _llm

    monkeypatch.setattr(_llm, "_synthesize", fake_synthesize)

    # Build a temp SQLite with the capture column present.
    with tempfile.TemporaryDirectory() as tmpdb:
        db_path = str(Path(tmpdb) / "test.db")
        from backend.api.main import _CREATE_TABLES, _run_migrations

        async with aiosqlite.connect(db_path) as db:
            await db.executescript(_CREATE_TABLES)
            await _run_migrations(db)
            await db.execute(
                "INSERT INTO bid_tournaments "
                "(id, client_id, project_description, zip_code, status, wiki_job_slug) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (1, "acme", "Retrofit office", "78701", "pending", slug),
            )
            await db.execute(
                "INSERT INTO tournament_entries "
                "(tournament_id, agent_name, total_bid, line_items_json, won) "
                "VALUES (?, ?, ?, ?, ?)",
                (1, "balanced", 150000.0, json.dumps({"line_items": []}), 0),
            )
            await db.execute(
                "INSERT INTO tournament_entries "
                "(tournament_id, agent_name, total_bid, line_items_json, won) "
                "VALUES (?, ?, ?, ?, ?)",
                (1, "aggressive", 142000.0, json.dumps({"line_items": []}), 0),
            )
            await db.commit()

        with patch("backend.agents.judge.DB_PATH", db_path):
            with patch(
                "backend.agents.judge.asyncio.to_thread", new=AsyncMock(return_value={})
            ):
                # Stub out feedback-loop and downstream hooks to keep the test focused.
                with patch(
                    "backend.agents.judge.verify_line_items", new=AsyncMock(return_value=[])
                ):
                    from backend.agents.judge import judge_tournament

                    result = await judge_tournament(
                        tournament_id=1,
                        actual_winning_bid=147500.0,
                        human_notes="test close",
                    )

        # The cascade is fire-and-forget; give it a beat to write the page.
        import asyncio

        await asyncio.sleep(0.1)

    assert result["wiki_job_slug"] == slug

    # The cascade must have rewritten the page to closed status + actual_cost.
    meta, _ = _io._parse_frontmatter(jobs_dir / f"{slug}.md")
    assert meta["status"] == "closed"
    assert meta["actual_cost"] == 147500.0
    assert meta["outcome_date"] is not None


@pytest.mark.anyio
async def test_bid_strategy_estimate_too_large(monkeypatch):
    """estimate JSON over 500KB should return 422."""
    monkeypatch.setenv("API_KEY", "test-key")
    oversized_estimate = {"data": "x" * 600_000}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post(
            "/api/bid/strategy",
            json={
                "estimate": oversized_estimate,
                "rfp_text": "Provide all labor and materials for a 10,000 sqft commercial buildout.",
            },
            headers={"X-API-Key": "test-key"},
        )
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_bid_strategy_known_competitors_too_many(monkeypatch):
    """known_competitors list over 20 items should return 422."""
    monkeypatch.setenv("API_KEY", "test-key")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post(
            "/api/bid/strategy",
            json={
                "estimate": {"total_bid": 100000},
                "rfp_text": "Provide all labor and materials for a 10,000 sqft commercial buildout.",
                "known_competitors": [f"Contractor {i}" for i in range(21)],
            },
            headers={"X-API-Key": "test-key"},
        )
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_preprocess_pdf_wrong_type(monkeypatch):
    """Non-PDF bytes (missing %PDF- magic bytes) should return 400."""
    monkeypatch.setenv("API_KEY", "test-key")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post(
            "/api/estimate/preprocess-pdf",
            files={"pdf": ("plans.pdf", b"not a pdf file content", "application/pdf")},
            data={"zip_code": "76801", "trade_type": "general"},
            headers={"X-API-Key": "test-key"},
        )
    assert resp.status_code == 400
    assert "PDF" in resp.json()["detail"]


@pytest.mark.anyio
async def test_preprocess_pdf_too_large(monkeypatch):
    """File over 32MB should return 400."""
    monkeypatch.setenv("API_KEY", "test-key")
    oversized = b"%PDF-1.4" + b"x" * (32 * 1024 * 1024 + 1)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post(
            "/api/estimate/preprocess-pdf",
            files={"pdf": ("big.pdf", oversized, "application/pdf")},
            data={"zip_code": "76801", "trade_type": "general"},
            headers={"X-API-Key": "test-key"},
        )
    assert resp.status_code == 400
    assert "32MB" in resp.json()["detail"]


@pytest.mark.anyio
async def test_preprocess_pdf_success(monkeypatch):
    """Valid PDF with mocked Claude call should return a draft string."""
    monkeypatch.setenv("API_KEY", "test-key")
    minimal_pdf = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\n%%EOF"

    with patch(
        "backend.api.routes.preprocess_blueprint",
        new=AsyncMock(return_value="18,000 sqft 3-story commercial office building"),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post(
                "/api/estimate/preprocess-pdf",
                files={"pdf": ("plans.pdf", minimal_pdf, "application/pdf")},
                data={"zip_code": "76801", "trade_type": "general"},
                headers={"X-API-Key": "test-key"},
            )

    assert resp.status_code == 200
    data = resp.json()
    assert "draft" in data
    assert "18,000 sqft" in data["draft"]
