"""Tests for wiki_manager — TakeoffAI LLM Wiki."""

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock


def test_parse_frontmatter_valid(tmp_path):
    from backend.agents.wiki_manager import _parse_frontmatter

    page = tmp_path / "test.md"
    page.write_text("---\nstatus: prospect\nclient: acme\n---\n\n# Title\n\nBody text.")
    meta, body = _parse_frontmatter(page)
    assert meta["status"] == "prospect"
    assert meta["client"] == "acme"
    assert "# Title" in body
    assert "Body text." in body


def test_parse_frontmatter_no_frontmatter(tmp_path):
    from backend.agents.wiki_manager import _parse_frontmatter

    page = tmp_path / "test.md"
    page.write_text("# Just a title\n\nNo frontmatter here.")
    meta, body = _parse_frontmatter(page)
    assert meta == {}
    assert "# Just a title" in body


def test_parse_frontmatter_missing_file(tmp_path):
    from backend.agents.wiki_manager import _parse_frontmatter

    page = tmp_path / "nonexistent.md"
    meta, body = _parse_frontmatter(page)
    assert meta == {}
    assert body == ""


def test_write_page_creates_file(tmp_path):
    from backend.agents.wiki_manager import _write_page

    page = tmp_path / "jobs" / "test-job.md"
    meta = {"status": "prospect", "client": "acme", "date": "2026-04-06"}
    body = "# Test Job\n\n## Scope\nBuild something."
    _write_page(page, meta, body)

    assert page.exists()
    content = page.read_text()
    assert "---" in content
    assert "status: prospect" in content
    assert "# Test Job" in content


def test_write_page_overwrites(tmp_path):
    from backend.agents.wiki_manager import _write_page

    page = tmp_path / "test.md"
    _write_page(page, {"status": "prospect"}, "# Old")
    _write_page(page, {"status": "estimated"}, "# New")

    content = page.read_text()
    assert "status: estimated" in content
    assert "# New" in content
    assert "# Old" not in content


def test_read_page_returns_tuple(tmp_path):
    from backend.agents.wiki_manager import _write_page, _read_page

    page = tmp_path / "test.md"
    _write_page(page, {"status": "prospect", "client": "acme"}, "# Title\n\nBody.")
    meta, body = _read_page(page)
    assert meta["status"] == "prospect"
    assert "Body." in body


def test_slug_generation():
    from backend.agents.wiki_manager import _make_job_slug

    slug = _make_job_slug("acme-construction", "Parking Garage — Downtown Austin", "2026-04-06")
    assert slug == "2026-04-06-acme-construction-parking-garage-downtown-austin"


def test_slug_generation_special_chars():
    from backend.agents.wiki_manager import _make_job_slug

    slug = _make_job_slug("bob's_crew", "10,000 SF Warehouse (Phase 2)", "2026-04-06")
    assert slug == "2026-04-06-bobs-crew-10000-sf-warehouse-phase-2"
    assert "'" not in slug
    assert "," not in slug
    assert "(" not in slug


@pytest.mark.anyio
async def test_synthesize_calls_claude(tmp_path, monkeypatch):
    """_synthesize should call Claude with system + context + instruction and return text."""
    import backend.agents.wiki_manager as wm
    monkeypatch.setattr(wm, "_schema_cache", None)
    monkeypatch.setattr(wm, "SCHEMA_PATH", tmp_path / "SCHEMA.md")
    (tmp_path / "SCHEMA.md").write_text("# Test Schema\nRule 1: be concise.")

    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="## Tournament\nConservative bid $143K.")]

    mock_client = AsyncMock()
    mock_client.messages.create = AsyncMock(return_value=mock_response)
    monkeypatch.setattr(wm, "_anthropic", mock_client)

    result = await wm._synthesize(
        context="Job: parking garage, client: acme",
        instruction="Write the Tournament section.",
    )

    assert "Conservative" in result or "Tournament" in result
    mock_client.messages.create.assert_called_once()
    call_kwargs = mock_client.messages.create.call_args.kwargs
    assert "Test Schema" in call_kwargs["system"]
    assert "parking garage" in call_kwargs["messages"][0]["content"]


@pytest.mark.anyio
async def test_synthesize_missing_schema(tmp_path, monkeypatch):
    """_synthesize should work even if SCHEMA.md is missing."""
    import backend.agents.wiki_manager as wm
    monkeypatch.setattr(wm, "_schema_cache", None)
    monkeypatch.setattr(wm, "SCHEMA_PATH", tmp_path / "nonexistent.md")

    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="Some content.")]

    mock_client = AsyncMock()
    mock_client.messages.create = AsyncMock(return_value=mock_response)
    monkeypatch.setattr(wm, "_anthropic", mock_client)

    result = await wm._synthesize(context="test", instruction="write something")
    assert result == "Some content."


@pytest.mark.anyio
async def test_create_job_writes_page(tmp_path, monkeypatch):
    """create_job should write a job page with prospect status and LLM scope."""
    import backend.agents.wiki_manager as wm
    monkeypatch.setattr(wm, "WIKI_DIR", tmp_path)
    monkeypatch.setattr(wm, "JOBS_DIR", tmp_path / "jobs")
    monkeypatch.setattr(wm, "CLIENTS_DIR", tmp_path / "clients")

    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="# Acme Parking Garage\n\n## Scope\nA 3-level precast parking structure.")]
    mock_client = AsyncMock()
    mock_client.messages.create = AsyncMock(return_value=mock_response)
    monkeypatch.setattr(wm, "_anthropic", mock_client)

    result = await wm.create_job(
        client_id="acme",
        project_name="Parking Garage",
        description="3-level precast parking structure, 420 spaces",
        zip_code="78701",
        trade_type="concrete",
    )

    assert result["status"] == "prospect"
    assert "job_slug" in result

    # Verify page was written
    page_path = (tmp_path / "jobs" / f"{result['job_slug']}.md")
    assert page_path.exists()
    meta, body = wm._parse_frontmatter(page_path)
    assert meta["status"] == "prospect"
    assert meta["client"] == "acme"
    assert meta["zip"] == "78701"
    assert "Scope" in body

    nullable_fields = [
        "our_bid", "estimate_total", "estimate_low", "estimate_high",
        "tournament_id", "winner_personality", "band_low", "band_high",
        "actual_cost", "outcome_date",
    ]
    for field in nullable_fields:
        assert field in meta
        assert meta[field] is None


@pytest.mark.anyio
async def test_create_job_creates_client_page_if_missing(tmp_path, monkeypatch):
    """create_job should create a client page if one doesn't exist yet."""
    import backend.agents.wiki_manager as wm
    monkeypatch.setattr(wm, "WIKI_DIR", tmp_path)
    monkeypatch.setattr(wm, "JOBS_DIR", tmp_path / "jobs")
    monkeypatch.setattr(wm, "CLIENTS_DIR", tmp_path / "clients")

    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="# Job Page\n\n## Scope\nSomething.")]
    mock_client = AsyncMock()
    mock_client.messages.create = AsyncMock(return_value=mock_response)
    monkeypatch.setattr(wm, "_anthropic", mock_client)

    await wm.create_job(
        client_id="newclient",
        project_name="First Job",
        description="A brand new project for a new client",
        zip_code="76801",
        trade_type="general",
    )

    client_page = tmp_path / "clients" / "newclient.md"
    assert client_page.exists()
    meta, _ = wm._parse_frontmatter(client_page)
    assert meta["client_id"] == "newclient"
    assert meta["total_jobs"] == 1


@pytest.mark.anyio
async def test_enrich_estimate_appends_section(tmp_path, monkeypatch):
    """enrich_estimate should append Estimate section and update status."""
    import backend.agents.wiki_manager as wm
    monkeypatch.setattr(wm, "JOBS_DIR", tmp_path / "jobs")

    # Pre-create a prospect job page
    job_path = tmp_path / "jobs" / "2026-04-06-acme-garage.md"
    wm._write_page(job_path, {"status": "prospect", "client": "acme"}, "# Garage\n\n## Scope\nBuild it.")

    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="## Estimate\nTotal bid $159K with high confidence. Key costs: concrete $43K, steel $98K.")]
    mock_client = AsyncMock()
    mock_client.messages.create = AsyncMock(return_value=mock_response)
    monkeypatch.setattr(wm, "_anthropic", mock_client)

    estimate_data = {
        "total_bid": 159880.0,
        "estimate_low": 143000.0,
        "estimate_high": 176000.0,
        "confidence": "high",
        "line_items": [{"description": "Concrete slab", "subtotal": 43200}],
    }

    await wm.enrich_estimate("2026-04-06-acme-garage", estimate_data)

    meta, body = wm._read_page(job_path)
    assert meta["status"] == "estimated"
    assert meta["estimate_total"] == 159880.0
    assert meta["estimate_low"] == 143000.0
    assert "Estimate" in body


@pytest.mark.anyio
async def test_enrich_estimate_noop_if_no_page(tmp_path, monkeypatch):
    """enrich_estimate should silently do nothing if the job page doesn't exist."""
    import backend.agents.wiki_manager as wm
    monkeypatch.setattr(wm, "JOBS_DIR", tmp_path / "jobs")

    # No exception should be raised
    await wm.enrich_estimate("nonexistent-job", {"total_bid": 100000})


@pytest.mark.anyio
async def test_enrich_tournament_appends_section(tmp_path, monkeypatch):
    """enrich_tournament should append Tournament section and update status."""
    import backend.agents.wiki_manager as wm
    monkeypatch.setattr(wm, "JOBS_DIR", tmp_path / "jobs")

    job_path = tmp_path / "jobs" / "2026-04-06-acme-garage.md"
    wm._write_page(job_path, {"status": "estimated", "client": "acme"}, "# Garage\n\n## Scope\nBuild it.\n\n## Estimate\nTotal $159K.")

    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="## Tournament\nFive agents bid. Band: $143K-$181K. Market Beater lowest at $151K.")]
    mock_client = AsyncMock()
    mock_client.messages.create = AsyncMock(return_value=mock_response)
    monkeypatch.setattr(wm, "_anthropic", mock_client)

    tournament_data = {
        "tournament_id": 42,
        "consensus_entries": [
            {"agent_name": "conservative", "total_bid": 170000, "confidence": "high"},
            {"agent_name": "market_beater", "total_bid": 151500, "confidence": "high"},
            {"agent_name": "aggressive", "total_bid": 143200, "confidence": "medium"},
        ],
    }

    await wm.enrich_tournament("2026-04-06-acme-garage", tournament_data)

    meta, body = wm._read_page(job_path)
    assert meta["status"] == "tournament-complete"
    assert meta["tournament_id"] == 42
    assert meta["band_low"] == 143200
    assert meta["band_high"] == 170000
    assert "Tournament" in body
