"""Tests for wiki_manager — TakeoffAI LLM Wiki."""

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

import backend.agents._wiki_io as _io
import backend.agents._wiki_llm as _llm


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
    from backend.agents.wiki_manager import _write_page, read_page

    page = tmp_path / "test.md"
    _write_page(page, {"status": "prospect", "client": "acme"}, "# Title\n\nBody.")
    meta, body = read_page(page)
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
    from backend.agents.wiki_manager import _synthesize
    monkeypatch.setattr(_llm, "_schema_cache", None)
    monkeypatch.setattr(_llm, "SCHEMA_PATH", tmp_path / "SCHEMA.md")
    (tmp_path / "SCHEMA.md").write_text("# Test Schema\nRule 1: be concise.")

    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="## Tournament\nConservative bid $143K.")]

    mock_client = AsyncMock()
    mock_client.messages.create = AsyncMock(return_value=mock_response)
    monkeypatch.setattr(_llm, "_anthropic", mock_client)

    result = await _synthesize(
        context="Job: parking garage, client: acme",
        instruction="Write the Tournament section.",
    )

    assert "Conservative" in result or "Tournament" in result
    mock_client.messages.create.assert_called_once()
    call_kwargs = mock_client.messages.create.call_args.kwargs
    system = call_kwargs["system"]
    # system is now a list of blocks when schema is present
    if isinstance(system, list):
        system_text = " ".join(block["text"] for block in system)
    else:
        system_text = system
    assert "Test Schema" in system_text
    assert "parking garage" in call_kwargs["messages"][0]["content"]


@pytest.mark.anyio
async def test_synthesize_missing_schema(tmp_path, monkeypatch):
    """_synthesize should work even if SCHEMA.md is missing."""
    from backend.agents.wiki_manager import _synthesize
    monkeypatch.setattr(_llm, "_schema_cache", None)
    monkeypatch.setattr(_llm, "SCHEMA_PATH", tmp_path / "nonexistent.md")

    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="Some content.")]

    mock_client = AsyncMock()
    mock_client.messages.create = AsyncMock(return_value=mock_response)
    monkeypatch.setattr(_llm, "_anthropic", mock_client)

    result = await _synthesize(context="test", instruction="write something")
    assert result == "Some content."


@pytest.mark.anyio
async def test_create_job_writes_page(tmp_path, monkeypatch):
    """create_job should write a job page with prospect status and LLM scope."""
    import backend.agents.wiki_manager as wm
    monkeypatch.setattr(_io, "JOBS_DIR", tmp_path / "jobs")
    monkeypatch.setattr(_io, "CLIENTS_DIR", tmp_path / "clients")

    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="# Acme Parking Garage\n\n## Scope\nA 3-level precast parking structure.")]
    mock_client = AsyncMock()
    mock_client.messages.create = AsyncMock(return_value=mock_response)
    monkeypatch.setattr(_llm, "_anthropic", mock_client)

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
    monkeypatch.setattr(_io, "JOBS_DIR", tmp_path / "jobs")
    monkeypatch.setattr(_io, "CLIENTS_DIR", tmp_path / "clients")

    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="# Job Page\n\n## Scope\nSomething.")]
    mock_client = AsyncMock()
    mock_client.messages.create = AsyncMock(return_value=mock_response)
    monkeypatch.setattr(_llm, "_anthropic", mock_client)

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
    monkeypatch.setattr(_io, "JOBS_DIR", tmp_path / "jobs")

    # Pre-create a prospect job page
    job_path = tmp_path / "jobs" / "2026-04-06-acme-garage.md"
    wm._write_page(job_path, {"status": "prospect", "client": "acme"}, "# Garage\n\n## Scope\nBuild it.")

    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="## Estimate\nTotal bid $159K with high confidence. Key costs: concrete $43K, steel $98K.")]
    mock_client = AsyncMock()
    mock_client.messages.create = AsyncMock(return_value=mock_response)
    monkeypatch.setattr(_llm, "_anthropic", mock_client)

    estimate_data = {
        "total_bid": 159880.0,
        "estimate_low": 143000.0,
        "estimate_high": 176000.0,
        "confidence": "high",
        "line_items": [{"description": "Concrete slab", "subtotal": 43200}],
    }

    await wm.enrich_estimate("2026-04-06-acme-garage", estimate_data)

    meta, body = wm.read_page(job_path)
    assert meta["status"] == "estimated"
    assert meta["estimate_total"] == 159880.0
    assert meta["estimate_low"] == 143000.0
    assert meta["estimate_high"] == 176000.0
    assert "Estimate" in body


@pytest.mark.anyio
async def test_enrich_estimate_noop_if_no_page(tmp_path, monkeypatch):
    """enrich_estimate should silently do nothing if the job page doesn't exist."""
    import backend.agents.wiki_manager as wm
    monkeypatch.setattr(_io, "JOBS_DIR", tmp_path / "jobs")

    # No exception should be raised
    await wm.enrich_estimate("nonexistent-job", {"total_bid": 100000})


@pytest.mark.anyio
async def test_enrich_tournament_appends_section(tmp_path, monkeypatch):
    """enrich_tournament should append Tournament section and update status."""
    import backend.agents.wiki_manager as wm
    monkeypatch.setattr(_io, "JOBS_DIR", tmp_path / "jobs")

    job_path = tmp_path / "jobs" / "2026-04-06-acme-garage.md"
    wm._write_page(job_path, {"status": "estimated", "client": "acme"}, "# Garage\n\n## Scope\nBuild it.\n\n## Estimate\nTotal $159K.")

    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="## Tournament\nFive agents bid. Band: $143K-$181K. Market Beater lowest at $151K.")]
    mock_client = AsyncMock()
    mock_client.messages.create = AsyncMock(return_value=mock_response)
    monkeypatch.setattr(_llm, "_anthropic", mock_client)

    tournament_data = {
        "tournament_id": 42,
        "consensus_entries": [
            {"agent_name": "conservative", "total_bid": 170000, "confidence": "high"},
            {"agent_name": "market_beater", "total_bid": 151500, "confidence": "high"},
            {"agent_name": "aggressive", "total_bid": 143200, "confidence": "medium"},
        ],
    }

    await wm.enrich_tournament("2026-04-06-acme-garage", tournament_data)

    meta, body = wm.read_page(job_path)
    assert meta["status"] == "tournament-complete"
    assert meta["tournament_id"] == 42
    assert meta["band_low"] == 143200
    assert meta["band_high"] == 170000
    assert "Tournament" in body
    assert meta["winner_personality"] == "aggressive"


@pytest.mark.anyio
async def test_record_bid_decision(tmp_path, monkeypatch):
    """record_bid_decision should append Bid Decision section and set our_bid."""
    import backend.agents.wiki_manager as wm
    monkeypatch.setattr(_io, "JOBS_DIR", tmp_path / "jobs")

    job_path = tmp_path / "jobs" / "2026-04-06-acme-garage.md"
    wm._write_page(
        job_path,
        {"status": "tournament-complete", "client": "acme", "tournament_id": 42},
        "# Garage\n\n## Scope\nBuild it.\n\n## Tournament\nBids ranged $143K-$181K.",
    )

    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="## Bid Decision\nGoing with $159K Balanced consensus.")]
    mock_client = AsyncMock()
    mock_client.messages.create = AsyncMock(return_value=mock_response)
    monkeypatch.setattr(_llm, "_anthropic", mock_client)

    await wm.record_bid_decision("2026-04-06-acme-garage", our_bid=159880.0, notes="Balanced consensus")

    meta, body = wm.read_page(job_path)
    assert meta["status"] == "bid-submitted"
    assert meta["our_bid"] == 159880.0
    assert "Bid Decision" in body


@pytest.mark.anyio
async def test_record_bid_decision_noop_if_no_page(tmp_path, monkeypatch):
    """record_bid_decision should silently do nothing if the job page doesn't exist."""
    import backend.agents.wiki_manager as wm
    monkeypatch.setattr(_io, "JOBS_DIR", tmp_path / "jobs")
    # No exception should be raised
    await wm.record_bid_decision("nonexistent-job", our_bid=50000.0)


@pytest.mark.anyio
async def test_cascade_outcome_updates_multiple_pages(tmp_path, monkeypatch):
    """cascade_outcome should update job, client, and personality pages."""
    import backend.agents.wiki_manager as wm
    monkeypatch.setattr(_io, "JOBS_DIR", tmp_path / "jobs")
    monkeypatch.setattr(_io, "CLIENTS_DIR", tmp_path / "clients")
    monkeypatch.setattr(_io, "PERSONALITIES_DIR", tmp_path / "personalities")
    monkeypatch.setattr(_io, "MATERIALS_DIR", tmp_path / "materials")

    # Pre-create job page
    job_path = tmp_path / "jobs" / "2026-04-06-acme-garage.md"
    wm._write_page(
        job_path,
        {
            "status": "bid-submitted", "client": "acme", "our_bid": 159880.0,
            "tournament_id": 42, "winner_personality": "balanced",
        },
        "# Garage\n\n## Scope\nBuild it.\n\n## Bid Decision\nGoing with Balanced.",
    )

    # Pre-create client page
    client_path = tmp_path / "clients" / "acme.md"
    wm._write_page(
        client_path,
        {"client_id": "acme", "total_jobs": 1, "wins": 0, "losses": 0, "first_job": "2026-04-06"},
        "# Acme\n\n## Profile\nNew client.\n\n## Recent Jobs\n",
    )

    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="## Outcome\nWon the bid at $159K.")]
    mock_client = AsyncMock()
    mock_client.messages.create = AsyncMock(return_value=mock_response)
    monkeypatch.setattr(_llm, "_anthropic", mock_client)

    await wm.cascade_outcome(
        job_slug="2026-04-06-acme-garage",
        status="won",
        actual_cost=None,
        notes="Client accepted our bid",
    )

    # Job page updated
    meta, body = wm.read_page(job_path)
    assert meta["status"] == "won"
    assert "Outcome" in body
    from datetime import date as _date
    assert meta["outcome_date"] == _date.today().isoformat()

    # Client page updated
    c_meta, _ = wm.read_page(client_path)
    assert c_meta["wins"] == 1

    # Personality page created
    personality_path = tmp_path / "personalities" / "balanced.md"
    assert personality_path.exists()


@pytest.mark.anyio
async def test_cascade_outcome_closed_with_actual_cost(tmp_path, monkeypatch):
    """cascade_outcome with status=closed should set actual_cost in frontmatter."""
    import backend.agents.wiki_manager as wm
    monkeypatch.setattr(_io, "JOBS_DIR", tmp_path / "jobs")
    monkeypatch.setattr(_io, "CLIENTS_DIR", tmp_path / "clients")
    monkeypatch.setattr(_io, "PERSONALITIES_DIR", tmp_path / "personalities")
    monkeypatch.setattr(_io, "MATERIALS_DIR", tmp_path / "materials")

    job_path = tmp_path / "jobs" / "2026-04-06-acme-garage.md"
    wm._write_page(
        job_path,
        {"status": "won", "client": "acme", "our_bid": 159880.0, "tournament_id": 42, "winner_personality": "balanced"},
        "# Garage\n\n## Outcome\nWon.",
    )
    client_path = tmp_path / "clients" / "acme.md"
    wm._write_page(client_path, {"client_id": "acme", "total_jobs": 1, "wins": 1, "losses": 0, "first_job": "2026-04-06"}, "# Acme")

    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="## Outcome\nClosed at $148K actual cost. 7.4% margin captured.")]
    mock_client = AsyncMock()
    mock_client.messages.create = AsyncMock(return_value=mock_response)
    monkeypatch.setattr(_llm, "_anthropic", mock_client)

    await wm.cascade_outcome(
        job_slug="2026-04-06-acme-garage",
        status="closed",
        actual_cost=148000.0,
    )

    meta, _ = wm.read_page(job_path)
    assert meta["status"] == "closed"
    assert meta["actual_cost"] == 148000.0


@pytest.mark.anyio
async def test_update_material_page_creates_new(tmp_path, monkeypatch):
    """update_material_page should create a new material page if one doesn't exist."""
    import backend.agents.wiki_manager as wm
    monkeypatch.setattr(_io, "MATERIALS_DIR", tmp_path / "materials")

    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="# Concrete\n\n## Current Pricing\nVerified at $5.80/sqft.")]
    mock_client = AsyncMock()
    mock_client.messages.create = AsyncMock(return_value=mock_response)
    monkeypatch.setattr(_llm, "_anthropic", mock_client)

    await wm.update_material_page(
        item="concrete",
        unit="sqft",
        ai_unit_cost=6.50,
        verified_mid=5.80,
        deviation_pct=12.07,
        category="structural",
    )

    pages = list((tmp_path / "materials").glob("*.md"))
    assert len(pages) == 1
    meta, body = wm.read_page(pages[0])
    assert meta["material"] == "concrete"
    assert meta["deviation_pct"] == 12.07
    assert "Pricing" in body


@pytest.mark.anyio
async def test_update_material_page_updates_existing(tmp_path, monkeypatch):
    """update_material_page should update an existing material page."""
    import backend.agents.wiki_manager as wm
    monkeypatch.setattr(_io, "MATERIALS_DIR", tmp_path / "materials")

    page_path = tmp_path / "materials" / "concrete.md"
    wm._write_page(
        page_path,
        {"material": "concrete", "category": "structural", "last_verified": "2026-04-01", "deviation_pct": 5.0, "verified_mid": 5.50},
        "# Concrete\n\n## Current Pricing\nOld data.",
    )

    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="# Concrete\n\n## Current Pricing\nUpdated to $5.80.")]
    mock_client = AsyncMock()
    mock_client.messages.create = AsyncMock(return_value=mock_response)
    monkeypatch.setattr(_llm, "_anthropic", mock_client)

    await wm.update_material_page(
        item="concrete",
        unit="sqft",
        ai_unit_cost=6.50,
        verified_mid=5.80,
        deviation_pct=12.07,
        category="structural",
    )

    meta, _ = wm.read_page(page_path)
    assert meta["deviation_pct"] == 12.07
    assert meta["verified_mid"] == 5.80
    from datetime import date as _date
    assert meta["last_verified"] == _date.today().isoformat()
    assert meta["material"] == "concrete"   # existing field preserved
    assert meta["category"] == "structural" # existing field preserved


def test_lint_finds_broken_links(tmp_path, monkeypatch):
    """lint should detect wikilinks to nonexistent pages."""
    import backend.agents.wiki_manager as wm
    monkeypatch.setattr(_io, "JOBS_DIR", tmp_path / "jobs")
    monkeypatch.setattr(_io, "CLIENTS_DIR", tmp_path / "clients")
    monkeypatch.setattr(_io, "MATERIALS_DIR", tmp_path / "materials")
    monkeypatch.setattr(_io, "PERSONALITIES_DIR", tmp_path / "personalities")

    (tmp_path / "jobs").mkdir()
    job_path = tmp_path / "jobs" / "test-job.md"
    wm._write_page(
        job_path,
        {"status": "prospect", "client": "ghost"},
        "# Test\n\n## Links\n- [[clients/ghost]]",
    )

    report = wm.lint()
    assert len(report["broken_links"]) >= 1
    assert any("ghost" in bl["link"] for bl in report["broken_links"])


def test_lint_finds_stale_jobs(tmp_path, monkeypatch):
    """lint should flag jobs stuck in estimated for >30 days."""
    import backend.agents.wiki_manager as wm
    monkeypatch.setattr(_io, "JOBS_DIR", tmp_path / "jobs")
    monkeypatch.setattr(_io, "CLIENTS_DIR", tmp_path / "clients")
    monkeypatch.setattr(_io, "MATERIALS_DIR", tmp_path / "materials")
    monkeypatch.setattr(_io, "PERSONALITIES_DIR", tmp_path / "personalities")

    (tmp_path / "jobs").mkdir()
    job_path = tmp_path / "jobs" / "old-job.md"
    wm._write_page(
        job_path,
        {"status": "estimated", "client": "acme", "date": "2026-01-01"},
        "# Old Job\n\n## Scope\nStale.",
    )

    report = wm.lint()
    assert len(report["stale_jobs"]) >= 1


def test_lint_validates_frontmatter(tmp_path, monkeypatch):
    """lint should flag missing required frontmatter fields."""
    import backend.agents.wiki_manager as wm
    monkeypatch.setattr(_io, "JOBS_DIR", tmp_path / "jobs")
    monkeypatch.setattr(_io, "CLIENTS_DIR", tmp_path / "clients")
    monkeypatch.setattr(_io, "MATERIALS_DIR", tmp_path / "materials")
    monkeypatch.setattr(_io, "PERSONALITIES_DIR", tmp_path / "personalities")

    (tmp_path / "jobs").mkdir()
    job_path = tmp_path / "jobs" / "bad-job.md"
    wm._write_page(job_path, {"client": "acme"}, "# Bad Job")

    report = wm.lint()
    assert len(report["frontmatter_errors"]) >= 1


def test_seed_personality_page(tmp_path, monkeypatch):
    """_seed_personality_page should create a page from PERSONALITY_PROMPTS."""
    import backend.agents.wiki_manager as wm
    monkeypatch.setattr(_io, "PERSONALITIES_DIR", tmp_path / "personalities")

    wm._seed_personality_page("conservative")

    page = tmp_path / "personalities" / "conservative.md"
    assert page.exists()
    meta, body = wm._parse_frontmatter(page)
    assert meta["personality"] == "conservative"
    assert meta["wins"] == 0
    assert "CONSERVATIVE" in body
    assert "## Philosophy" in body
    assert "## Performance" in body


@pytest.mark.anyio
async def test_full_job_lifecycle(tmp_path, monkeypatch):
    """End-to-end: prospect → estimated → tournament-complete → bid-submitted → won → closed."""
    import backend.agents.wiki_manager as wm
    monkeypatch.setattr(_io, "JOBS_DIR", tmp_path / "jobs")
    monkeypatch.setattr(_io, "CLIENTS_DIR", tmp_path / "clients")
    monkeypatch.setattr(_io, "PERSONALITIES_DIR", tmp_path / "personalities")
    monkeypatch.setattr(_io, "MATERIALS_DIR", tmp_path / "materials")

    # Mock all LLM calls
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="## Section\nLLM-generated content.")]
    mock_client = AsyncMock()
    mock_client.messages.create = AsyncMock(return_value=mock_response)
    monkeypatch.setattr(_llm, "_anthropic", mock_client)

    # 1. Create job (prospect)
    result = await wm.create_job(
        client_id="lifecycle-test",
        project_name="Full Lifecycle Job",
        description="A test project to verify the complete job pipeline works end-to-end",
        zip_code="78701",
        trade_type="general",
    )
    slug = result["job_slug"]
    meta, _ = wm.read_page(tmp_path / "jobs" / f"{slug}.md")
    assert meta["status"] == "prospect"

    # 2. Enrich with estimate
    await wm.enrich_estimate(slug, {
        "total_bid": 150000.0,
        "estimate_low": 135000.0,
        "estimate_high": 165000.0,
        "confidence": "high",
        "line_items": [],
    })
    meta, _ = wm.read_page(tmp_path / "jobs" / f"{slug}.md")
    assert meta["status"] == "estimated"
    assert meta["estimate_total"] == 150000.0

    # 3. Enrich with tournament
    await wm.enrich_tournament(slug, {
        "tournament_id": 99,
        "consensus_entries": [
            {"agent_name": "conservative", "total_bid": 170000, "confidence": "high"},
            {"agent_name": "balanced", "total_bid": 150000, "confidence": "high"},
            {"agent_name": "aggressive", "total_bid": 135000, "confidence": "medium"},
        ],
    })
    meta, _ = wm.read_page(tmp_path / "jobs" / f"{slug}.md")
    assert meta["status"] == "tournament-complete"
    assert meta["tournament_id"] == 99
    assert meta["band_low"] == 135000
    assert meta["band_high"] == 170000

    # 4. Record bid decision
    await wm.record_bid_decision(slug, our_bid=150000.0, notes="Going with Balanced")
    meta, _ = wm.read_page(tmp_path / "jobs" / f"{slug}.md")
    assert meta["status"] == "bid-submitted"
    assert meta["our_bid"] == 150000.0

    # 5. Cascade: won
    await wm.cascade_outcome(slug, status="won", notes="Client accepted")
    meta, _ = wm.read_page(tmp_path / "jobs" / f"{slug}.md")
    assert meta["status"] == "won"

    # Client page should show win
    client_meta, _ = wm.read_page(tmp_path / "clients" / "lifecycle-test.md")
    assert client_meta["wins"] == 1

    # 6. Cascade: closed with actual cost
    await wm.cascade_outcome(slug, status="closed", actual_cost=142000.0)
    meta, _ = wm.read_page(tmp_path / "jobs" / f"{slug}.md")
    assert meta["status"] == "closed"
    assert meta["actual_cost"] == 142000.0

    # Verify LLM was called multiple times (at least once per stage)
    assert mock_client.messages.create.call_count >= 6
