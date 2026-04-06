"""Tests for wiki_manager — TakeoffAI LLM Wiki."""

import pytest
from pathlib import Path


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
