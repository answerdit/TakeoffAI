# LLM Wiki + Job Tracking — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a persistent, LLM-maintained Obsidian wiki that tracks jobs through a 6-status bid pipeline, with full cascade updates across client, personality, and material pages.

**Architecture:** Single new agent (`wiki_manager.py`) owns all wiki I/O. New router (`wiki_routes.py`) exposes job CRUD + lint endpoints. Existing estimate/tournament routes get optional `job_slug` fields that fire-and-forget wiki enrichment. All LLM synthesis goes through `_synthesize()` using a configurable `WIKI_MODEL`.

**Tech Stack:** Python 3.11+, FastAPI, Anthropic Claude API, YAML frontmatter (`python-frontmatter`), Obsidian-compatible markdown with `[[wikilinks]]`

**Spec:** `docs/superpowers/specs/2026-04-06-llm-wiki-job-tracking-design.md`

---

## File Structure

| File | Responsibility |
|---|---|
| `backend/config.py` | Add `wiki_model` setting |
| `.env.template` | Add `WIKI_MODEL` variable |
| `wiki/SCHEMA.md` | Wiki conventions doc (injected into LLM system prompt) |
| `wiki/DASHBOARD.md` | Obsidian Dataview overview page |
| `wiki/personalities/*.md` | 5 seeded personality pages |
| `backend/agents/wiki_manager.py` | All wiki operations: create, enrich, cascade, lint |
| `backend/api/wiki_routes.py` | Job CRUD + lint endpoints (new router) |
| `backend/api/main.py` | Include wiki_routes router |
| `backend/api/routes.py` | Add optional `job_slug` to estimate/tournament requests, fire-and-forget hooks |
| `tests/test_wiki_manager.py` | Unit tests for wiki_manager |
| `tests/test_wiki_routes.py` | API tests for job endpoints |

---

### Task 1: Config and Wiki Scaffolding

**Files:**
- Modify: `backend/config.py`
- Modify: `.env.template`
- Create: `wiki/SCHEMA.md`
- Create: `wiki/DASHBOARD.md`
- Create: `wiki/jobs/.gitkeep`
- Create: `wiki/clients/.gitkeep`
- Create: `wiki/materials/.gitkeep`
- Create: `wiki/personalities/.gitkeep`

- [ ] **Step 1: Add wiki_model to config.py**

Open `backend/config.py` and add `wiki_model` to the `Settings` class:

```python
class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    anthropic_api_key: str = ""
    api_key: str = ""
    default_overhead_pct: float = 20.0
    default_margin_pct: float = 12.0
    app_env: str = "development"
    api_port: int = 8000
    claude_model: str = "claude-sonnet-4-6"
    wiki_model: str = "claude-haiku-4-5"
    db_path: str = str(_DATA_DIR / "takeoffai.db")
```

- [ ] **Step 2: Add WIKI_MODEL to .env.template**

Append to `.env.template` after the existing agent defaults section:

```
# ── Wiki ──────────────────────────────────────
WIKI_MODEL=claude-haiku-4-5
```

- [ ] **Step 3: Verify .gitignore does NOT ignore wiki/**

Read `.gitignore`. The `wiki/` directory should NOT be listed. Currently `.gitignore` does not mention `wiki/` so no change needed. If it is listed, remove it.

- [ ] **Step 4: Create wiki/SCHEMA.md**

Create `wiki/SCHEMA.md`:

```markdown
# TakeoffAI Wiki — Schema & Conventions

This document defines the structure and rules for the TakeoffAI knowledge base.
It is injected into the LLM system prompt for all wiki page synthesis.

---

## Page Types

### Job (`wiki/jobs/`)
Tracks a single project through the bid pipeline.

**Required frontmatter:** status, client, date, trade, zip
**Optional frontmatter:** our_bid, estimate_total, estimate_low, estimate_high, tournament_id, winner_personality, band_low, band_high, actual_cost, outcome_date

**Section order:** Scope, Estimate, Tournament, Bid Decision, Outcome, Price Flags, Links

**Status values:** prospect, estimated, tournament-complete, bid-submitted, won, lost, closed

### Client (`wiki/clients/`)
Narrative profile for a contractor client.

**Required frontmatter:** client_id, first_job, total_jobs, wins, losses
**Optional frontmatter:** company, region

**Section order:** Profile, Win/Loss Summary, ELO Standings, Recent Jobs, Patterns

### Personality (`wiki/personalities/`)
Performance history for a bidding personality.

**Required frontmatter:** personality, total_tournaments, wins, win_rate
**Optional frontmatter:** current_prompt_hash, last_evolution

**Section order:** Philosophy, Performance, Recent Results, Evolution History

### Material (`wiki/materials/`)
Price tracking for flagged construction materials.

**Required frontmatter:** material, category, last_verified
**Optional frontmatter:** seed_low, seed_high, verified_mid, deviation_pct

**Section order:** Current Pricing, Deviation History, Job Impact

---

## Rules

1. **Frontmatter is structured data.** All monetary values are raw numbers (no `$` prefix). Dates use ISO 8601 format (YYYY-MM-DD or full datetime).
2. **Links use folder-prefixed wikilinks.** Write `[[clients/acme-construction]]` not `[[acme-construction]]`.
3. **Job slugs are kebab-case.** Format: `YYYY-MM-DD-{client}-{short-description}`. Example: `2026-04-06-acme-parking-garage`.
4. **Section ordering is fixed.** Scope always first, Links always last, chronological sections in between.
5. **Body text is narrative.** Write for a contractor reviewing their bidding history. Be specific about dollar amounts, percentages, agent names, and dates. Avoid generic language.
6. **Cross-reference liberally.** Link to related job, client, personality, and material pages wherever relevant.
```

- [ ] **Step 5: Create wiki/DASHBOARD.md**

Create `wiki/DASHBOARD.md`:

````markdown
# TakeoffAI Dashboard

## Active Jobs
```dataview
TABLE status, client, trade, our_bid
FROM "jobs"
WHERE status != "closed" AND status != "lost"
SORT date DESC
```

## Recent Outcomes
```dataview
TABLE status, client, our_bid, actual_cost
FROM "jobs"
WHERE status = "won" OR status = "lost" OR status = "closed"
SORT outcome_date DESC
LIMIT 10
```

## Price Flags
```dataview
TABLE material, deviation_pct, last_verified
FROM "materials"
WHERE deviation_pct > 5
SORT deviation_pct DESC
```

## Personality Standings
```dataview
TABLE wins, win_rate, last_evolution
FROM "personalities"
SORT win_rate DESC
```
````

- [ ] **Step 6: Create empty directory markers**

Create empty `.gitkeep` files in `wiki/jobs/`, `wiki/clients/`, `wiki/materials/`, and `wiki/personalities/` to ensure the directories are tracked by git.

- [ ] **Step 7: Run existing tests to verify no regressions**

Run: `cd /Users/bevo/Documents/answerD.it/TakeoffAI && uv run pytest tests/ -x -q`
Expected: All 75 tests pass.

- [ ] **Step 8: Commit**

```bash
git add backend/config.py .env.template wiki/
git commit -m "feat(wiki): add WIKI_MODEL config, scaffold wiki directory with SCHEMA.md and DASHBOARD.md"
```

---

### Task 2: wiki_manager.py — Core Helpers

**Files:**
- Create: `backend/agents/wiki_manager.py`
- Create: `tests/test_wiki_manager.py`

- [ ] **Step 1: Write failing tests for frontmatter helpers**

Create `tests/test_wiki_manager.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/bevo/Documents/answerD.it/TakeoffAI && uv run pytest tests/test_wiki_manager.py -x -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'backend.agents.wiki_manager'`

- [ ] **Step 3: Implement core helpers in wiki_manager.py**

Create `backend/agents/wiki_manager.py`:

```python
"""
Wiki Manager — TakeoffAI
LLM-maintained Obsidian knowledge base for job tracking and institutional memory.

All wiki I/O goes through this module. No other code writes to the wiki/ directory.
"""

import asyncio
import logging
import os
import re
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import yaml
from anthropic import AsyncAnthropic

from backend.config import settings

logger = logging.getLogger(__name__)

WIKI_DIR = Path(__file__).parent.parent.parent / "wiki"
JOBS_DIR = WIKI_DIR / "jobs"
CLIENTS_DIR = WIKI_DIR / "clients"
MATERIALS_DIR = WIKI_DIR / "materials"
PERSONALITIES_DIR = WIKI_DIR / "personalities"
SCHEMA_PATH = WIKI_DIR / "SCHEMA.md"

WIKI_MODEL = os.getenv("WIKI_MODEL", settings.wiki_model)

_anthropic = AsyncAnthropic()

# ── Frontmatter helpers ──────────────────────────────────────────────────────


def _parse_frontmatter(path: Path) -> tuple[dict, str]:
    """
    Parse a markdown file with optional YAML frontmatter.
    Returns (metadata_dict, body_string). If file doesn't exist or has no
    frontmatter, returns ({}, "") or ({}, body).
    """
    if not path.exists():
        return {}, ""
    content = path.read_text(encoding="utf-8")
    if not content.startswith("---"):
        return {}, content
    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}, content
    try:
        meta = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        return {}, content
    body = parts[2].lstrip("\n")
    return meta, body


def _write_page(path: Path, meta: dict, body: str) -> None:
    """Write a markdown file with YAML frontmatter. Creates parent dirs if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    frontmatter = yaml.dump(meta, default_flow_style=False, sort_keys=False).strip()
    content = f"---\n{frontmatter}\n---\n\n{body}\n"
    path.write_text(content, encoding="utf-8")


def _read_page(path: Path) -> tuple[dict, str]:
    """Alias for _parse_frontmatter — reads a wiki page into (meta, body)."""
    return _parse_frontmatter(path)


def _make_job_slug(client_id: str, project_name: str, date_str: str) -> str:
    """
    Generate a kebab-case job slug: YYYY-MM-DD-{client}-{short-description}.
    Strips special characters, collapses whitespace.
    """
    raw = f"{date_str}-{client_id}-{project_name}"
    # Remove anything that's not alphanumeric, space, or dash
    cleaned = re.sub(r"[^a-zA-Z0-9\s-]", "", raw)
    # Collapse whitespace and dashes into single dashes
    slug = re.sub(r"[\s-]+", "-", cleaned).strip("-").lower()
    return slug
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/bevo/Documents/answerD.it/TakeoffAI && uv run pytest tests/test_wiki_manager.py -x -v`
Expected: All 8 tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/agents/wiki_manager.py tests/test_wiki_manager.py
git commit -m "feat(wiki): wiki_manager core — frontmatter parse/write helpers and slug generation"
```

---

### Task 3: wiki_manager.py — _synthesize() and SCHEMA Loading

**Files:**
- Modify: `backend/agents/wiki_manager.py`
- Modify: `tests/test_wiki_manager.py`

- [ ] **Step 1: Write failing test for _synthesize**

Add to `tests/test_wiki_manager.py`:

```python
from unittest.mock import AsyncMock, patch, MagicMock


@pytest.mark.anyio
async def test_synthesize_calls_claude(tmp_path, monkeypatch):
    """_synthesize should call Claude with system + context + instruction and return text."""
    import backend.agents.wiki_manager as wm
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
    monkeypatch.setattr(wm, "SCHEMA_PATH", tmp_path / "nonexistent.md")

    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="Some content.")]

    mock_client = AsyncMock()
    mock_client.messages.create = AsyncMock(return_value=mock_response)
    monkeypatch.setattr(wm, "_anthropic", mock_client)

    result = await wm._synthesize(context="test", instruction="write something")
    assert result == "Some content."
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/bevo/Documents/answerD.it/TakeoffAI && uv run pytest tests/test_wiki_manager.py::test_synthesize_calls_claude -x -v`
Expected: FAIL with `AttributeError: module has no attribute '_synthesize'`

- [ ] **Step 3: Implement _synthesize and schema loading**

Add to `backend/agents/wiki_manager.py` after the existing helpers:

```python
# ── Schema loading (cached) ──────────────────────────────────────────────────

_schema_cache: Optional[str] = None


def _load_schema() -> str:
    """Load SCHEMA.md content, cached after first read."""
    global _schema_cache
    if _schema_cache is not None:
        return _schema_cache
    if SCHEMA_PATH.exists():
        _schema_cache = SCHEMA_PATH.read_text(encoding="utf-8")
    else:
        _schema_cache = ""
    return _schema_cache


# ── LLM synthesis ────────────────────────────────────────────────────────────

_SYSTEM_BASE = (
    "You are a knowledge base writer for TakeoffAI, a construction bidding system. "
    "Write clear, specific markdown for contractors reviewing their bidding history. "
    "Include exact dollar amounts, percentages, agent names, and dates. "
    "Use [[folder/page-slug]] wikilinks for cross-references. "
    "Return ONLY the markdown body content — no frontmatter, no code fences."
)


async def _synthesize(
    context: str,
    instruction: str,
) -> str:
    """
    Single LLM call to generate wiki page content.
    Returns markdown string (body only, no frontmatter).
    """
    schema = _load_schema()
    system = f"{_SYSTEM_BASE}\n\n{schema}" if schema else _SYSTEM_BASE

    response = await _anthropic.messages.create(
        model=WIKI_MODEL,
        max_tokens=2048,
        system=system,
        messages=[{
            "role": "user",
            "content": f"Context:\n{context}\n\nInstruction:\n{instruction}",
        }],
    )
    return response.content[0].text.strip()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/bevo/Documents/answerD.it/TakeoffAI && uv run pytest tests/test_wiki_manager.py -x -v`
Expected: All 10 tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/agents/wiki_manager.py tests/test_wiki_manager.py
git commit -m "feat(wiki): _synthesize() LLM call with SCHEMA.md injection"
```

---

### Task 4: wiki_manager.py — create_job()

**Files:**
- Modify: `backend/agents/wiki_manager.py`
- Modify: `tests/test_wiki_manager.py`

- [ ] **Step 1: Write failing tests for create_job**

Add to `tests/test_wiki_manager.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/bevo/Documents/answerD.it/TakeoffAI && uv run pytest tests/test_wiki_manager.py::test_create_job_writes_page -x -v`
Expected: FAIL with `AttributeError: module has no attribute 'create_job'`

- [ ] **Step 3: Implement create_job**

Add to `backend/agents/wiki_manager.py`:

```python
# ── Public functions ─────────────────────────────────────────────────────────


async def create_job(
    client_id: str,
    project_name: str,
    description: str,
    zip_code: str,
    trade_type: str = "general",
) -> dict:
    """
    Create a new job wiki page at prospect status.
    Also creates the client page if it doesn't exist yet.
    Returns dict with job_slug and status.
    """
    today = date.today().isoformat()
    slug = _make_job_slug(client_id, project_name, today)
    page_path = JOBS_DIR / f"{slug}.md"

    # LLM writes the initial page body (title + scope)
    body = await _synthesize(
        context=(
            f"Project: {project_name}\n"
            f"Client: {client_id}\n"
            f"Description: {description}\n"
            f"Location ZIP: {zip_code}\n"
            f"Trade: {trade_type}"
        ),
        instruction=(
            "Write the initial wiki page for this job. Include:\n"
            "1. A markdown H1 title combining the project name and location\n"
            "2. A ## Scope section summarizing the project description\n"
            "3. A ## Links section with a wikilink to the client page: [[clients/{client_id}]]\n"
            "Do not include frontmatter."
        ).format(client_id=client_id),
    )

    meta = {
        "status": "prospect",
        "client": client_id,
        "date": today,
        "trade": trade_type,
        "zip": zip_code,
        "our_bid": None,
        "estimate_total": None,
        "estimate_low": None,
        "estimate_high": None,
        "tournament_id": None,
        "winner_personality": None,
        "band_low": None,
        "band_high": None,
        "actual_cost": None,
        "outcome_date": None,
    }

    _write_page(page_path, meta, body)

    # Ensure client page exists
    _ensure_client_page(client_id)

    return {"job_slug": slug, "status": "prospect"}


def _ensure_client_page(client_id: str) -> None:
    """Create a minimal client page if one doesn't exist yet."""
    client_path = CLIENTS_DIR / f"{client_id}.md"
    if client_path.exists():
        # Increment total_jobs counter
        meta, body = _parse_frontmatter(client_path)
        meta["total_jobs"] = meta.get("total_jobs", 0) + 1
        _write_page(client_path, meta, body)
        return

    meta = {
        "client_id": client_id,
        "first_job": date.today().isoformat(),
        "total_jobs": 1,
        "wins": 0,
        "losses": 0,
    }
    body = (
        f"# {client_id}\n\n"
        "## Profile\nNew client — profile will be enriched as jobs progress.\n\n"
        "## Recent Jobs\n\n"
        "## Patterns\nInsufficient data for pattern analysis.\n"
    )
    _write_page(client_path, meta, body)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/bevo/Documents/answerD.it/TakeoffAI && uv run pytest tests/test_wiki_manager.py -x -v`
Expected: All 12 tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/agents/wiki_manager.py tests/test_wiki_manager.py
git commit -m "feat(wiki): create_job() — creates job + client pages at prospect status"
```

---

### Task 5: wiki_manager.py — enrich_estimate() and enrich_tournament()

**Files:**
- Modify: `backend/agents/wiki_manager.py`
- Modify: `tests/test_wiki_manager.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_wiki_manager.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/bevo/Documents/answerD.it/TakeoffAI && uv run pytest tests/test_wiki_manager.py::test_enrich_estimate_appends_section -x -v`
Expected: FAIL with `AttributeError: module has no attribute 'enrich_estimate'`

- [ ] **Step 3: Implement enrich_estimate and enrich_tournament**

Add to `backend/agents/wiki_manager.py`:

```python
async def enrich_estimate(job_slug: str, estimate_data: dict) -> None:
    """
    Append Estimate section to a job page and update status to 'estimated'.
    No-op if the job page doesn't exist (fire-and-forget safe).
    """
    page_path = JOBS_DIR / f"{job_slug}.md"
    if not page_path.exists():
        logger.debug("enrich_estimate: job page %s not found, skipping", job_slug)
        return

    meta, body = _read_page(page_path)

    # Update frontmatter
    meta["status"] = "estimated"
    meta["estimate_total"] = estimate_data.get("total_bid")
    meta["estimate_low"] = estimate_data.get("estimate_low")
    meta["estimate_high"] = estimate_data.get("estimate_high")

    # LLM writes the Estimate section
    import json
    section = await _synthesize(
        context=(
            f"Existing page:\n{body}\n\n"
            f"Estimate data:\n{json.dumps(estimate_data, indent=2, default=str)}"
        ),
        instruction=(
            "Write a ## Estimate section to append to this job page. Summarize:\n"
            "- Total bid amount and confidence level\n"
            "- Key line items and where costs are concentrated\n"
            "- The estimate range (low to high) and what it means for risk\n"
            "Do not repeat the Scope section. Do not include frontmatter."
        ),
    )

    # Append section before ## Links if present, otherwise at end
    body = _append_section(body, section)
    _write_page(page_path, meta, body)


async def enrich_tournament(job_slug: str, tournament_data: dict) -> None:
    """
    Append Tournament section to a job page and update status to 'tournament-complete'.
    No-op if the job page doesn't exist.
    """
    page_path = JOBS_DIR / f"{job_slug}.md"
    if not page_path.exists():
        logger.debug("enrich_tournament: job page %s not found, skipping", job_slug)
        return

    meta, body = _read_page(page_path)

    entries = tournament_data.get("consensus_entries", [])
    bids = [e["total_bid"] for e in entries if e.get("total_bid")]

    meta["status"] = "tournament-complete"
    meta["tournament_id"] = tournament_data.get("tournament_id")
    meta["band_low"] = min(bids) if bids else None
    meta["band_high"] = max(bids) if bids else None

    # Find winner (lowest bid)
    if entries:
        winner = min(entries, key=lambda e: e.get("total_bid", float("inf")))
        meta["winner_personality"] = winner.get("agent_name")

    import json
    section = await _synthesize(
        context=(
            f"Existing page:\n{body}\n\n"
            f"Tournament data:\n{json.dumps(tournament_data, indent=2, default=str)}"
        ),
        instruction=(
            "Write a ## Tournament section to append to this job page. Summarize:\n"
            "- How many agents bid and the overall band (min to max)\n"
            "- Each agent's bid and confidence, noting agreements and divergences\n"
            "- Which agent came in lowest and what strategy drove that\n"
            "- Include [[personalities/agent-name]] wikilinks for each agent\n"
            "Do not repeat earlier sections. Do not include frontmatter."
        ),
    )

    body = _append_section(body, section)
    _write_page(page_path, meta, body)
```

Also add the `_append_section` helper before the public functions:

```python
def _append_section(body: str, new_section: str) -> str:
    """
    Append a new section to the page body.
    Inserts before ## Links if that section exists, otherwise appends at end.
    """
    links_marker = "\n## Links"
    if links_marker in body:
        idx = body.index(links_marker)
        return body[:idx].rstrip() + "\n\n" + new_section.strip() + "\n" + body[idx:]
    return body.rstrip() + "\n\n" + new_section.strip() + "\n"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/bevo/Documents/answerD.it/TakeoffAI && uv run pytest tests/test_wiki_manager.py -x -v`
Expected: All 15 tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/agents/wiki_manager.py tests/test_wiki_manager.py
git commit -m "feat(wiki): enrich_estimate() and enrich_tournament() — progressive job page enrichment"
```

---

### Task 6: wiki_manager.py — record_bid_decision() and cascade_outcome()

**Files:**
- Modify: `backend/agents/wiki_manager.py`
- Modify: `tests/test_wiki_manager.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_wiki_manager.py`:

```python
@pytest.mark.anyio
async def test_record_bid_decision(tmp_path, monkeypatch):
    """record_bid_decision should append Bid Decision section and set our_bid."""
    import backend.agents.wiki_manager as wm
    monkeypatch.setattr(wm, "JOBS_DIR", tmp_path / "jobs")

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
    monkeypatch.setattr(wm, "_anthropic", mock_client)

    await wm.record_bid_decision("2026-04-06-acme-garage", our_bid=159880.0, notes="Balanced consensus")

    meta, body = wm._read_page(job_path)
    assert meta["status"] == "bid-submitted"
    assert meta["our_bid"] == 159880.0
    assert "Bid Decision" in body


@pytest.mark.anyio
async def test_cascade_outcome_updates_multiple_pages(tmp_path, monkeypatch):
    """cascade_outcome should update job, client, and personality pages."""
    import backend.agents.wiki_manager as wm
    monkeypatch.setattr(wm, "WIKI_DIR", tmp_path)
    monkeypatch.setattr(wm, "JOBS_DIR", tmp_path / "jobs")
    monkeypatch.setattr(wm, "CLIENTS_DIR", tmp_path / "clients")
    monkeypatch.setattr(wm, "PERSONALITIES_DIR", tmp_path / "personalities")
    monkeypatch.setattr(wm, "MATERIALS_DIR", tmp_path / "materials")

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
    monkeypatch.setattr(wm, "_anthropic", mock_client)

    await wm.cascade_outcome(
        job_slug="2026-04-06-acme-garage",
        status="won",
        actual_cost=None,
        notes="Client accepted our bid",
    )

    # Job page updated
    meta, body = wm._read_page(job_path)
    assert meta["status"] == "won"
    assert "Outcome" in body

    # Client page updated
    c_meta, _ = wm._read_page(client_path)
    assert c_meta["wins"] == 1

    # Personality page created
    personality_path = tmp_path / "personalities" / "balanced.md"
    assert personality_path.exists()


@pytest.mark.anyio
async def test_cascade_outcome_closed_with_actual_cost(tmp_path, monkeypatch):
    """cascade_outcome with status=closed should set actual_cost in frontmatter."""
    import backend.agents.wiki_manager as wm
    monkeypatch.setattr(wm, "WIKI_DIR", tmp_path)
    monkeypatch.setattr(wm, "JOBS_DIR", tmp_path / "jobs")
    monkeypatch.setattr(wm, "CLIENTS_DIR", tmp_path / "clients")
    monkeypatch.setattr(wm, "PERSONALITIES_DIR", tmp_path / "personalities")
    monkeypatch.setattr(wm, "MATERIALS_DIR", tmp_path / "materials")

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
    monkeypatch.setattr(wm, "_anthropic", mock_client)

    await wm.cascade_outcome(
        job_slug="2026-04-06-acme-garage",
        status="closed",
        actual_cost=148000.0,
    )

    meta, _ = wm._read_page(job_path)
    assert meta["status"] == "closed"
    assert meta["actual_cost"] == 148000.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/bevo/Documents/answerD.it/TakeoffAI && uv run pytest tests/test_wiki_manager.py::test_record_bid_decision -x -v`
Expected: FAIL with `AttributeError: module has no attribute 'record_bid_decision'`

- [ ] **Step 3: Implement record_bid_decision and cascade_outcome**

Add to `backend/agents/wiki_manager.py`:

```python
async def record_bid_decision(
    job_slug: str,
    our_bid: float,
    notes: str = "",
) -> None:
    """Append Bid Decision section and update status to bid-submitted."""
    page_path = JOBS_DIR / f"{job_slug}.md"
    if not page_path.exists():
        logger.debug("record_bid_decision: job page %s not found, skipping", job_slug)
        return

    meta, body = _read_page(page_path)
    meta["status"] = "bid-submitted"
    meta["our_bid"] = our_bid

    section = await _synthesize(
        context=(
            f"Existing page:\n{body}\n\n"
            f"Bid decision: ${our_bid:,.2f}\n"
            f"Notes: {notes}"
        ),
        instruction=(
            "Write a ## Bid Decision section. Summarize:\n"
            "- Which bid amount was chosen and why\n"
            "- How it relates to the tournament band\n"
            "- Risk assessment for this number\n"
            "Do not repeat earlier sections."
        ),
    )

    body = _append_section(body, section)
    _write_page(page_path, meta, body)


async def cascade_outcome(
    job_slug: str,
    status: str,
    actual_cost: Optional[float] = None,
    notes: str = "",
) -> None:
    """
    Full cascade on outcome (won/lost/closed).
    Step 1: Update job page
    Step 2: Update client page
    Step 3: Update personality pages
    Step 4: Update material pages (if flagged — checked via frontmatter)
    """
    page_path = JOBS_DIR / f"{job_slug}.md"
    if not page_path.exists():
        logger.warning("cascade_outcome: job page %s not found", job_slug)
        return

    meta, body = _read_page(page_path)

    # ── Step 1: Update job page ──────────────────────────────────────────
    meta["status"] = status
    meta["outcome_date"] = date.today().isoformat()
    if actual_cost is not None:
        meta["actual_cost"] = actual_cost

    import json as _json
    context_data = {
        "status": status,
        "our_bid": meta.get("our_bid"),
        "actual_cost": actual_cost,
        "notes": notes,
    }
    section = await _synthesize(
        context=f"Existing page:\n{body}\n\nOutcome data:\n{_json.dumps(context_data, default=str)}",
        instruction=(
            f"Write or update the ## Outcome section for status={status}. Include:\n"
            "- The result (won/lost/closed)\n"
            "- If actual_cost is provided, analyze deviation from our_bid\n"
            "- Lessons learned or patterns observed\n"
            "Do not repeat earlier sections."
        ),
    )
    body = _append_section(body, section)
    _write_page(page_path, meta, body)

    # ── Step 2: Update client page ───────────────────────────────────────
    client_id = meta.get("client")
    if client_id:
        try:
            await _update_client_page_on_outcome(client_id, job_slug, meta, status)
        except Exception:
            logger.exception("cascade: failed to update client page for %s", client_id)

    # ── Step 3: Update personality pages ─────────────────────────────────
    personality = meta.get("winner_personality")
    if personality:
        try:
            await _update_personality_page(personality, job_slug, meta, status)
        except Exception:
            logger.exception("cascade: failed to update personality page %s", personality)

    # ── Step 4: Material pages (future — triggered by PriceVerifier) ─────
    # update_material_page is called separately by PriceVerifier, not here


async def _update_client_page_on_outcome(
    client_id: str,
    job_slug: str,
    job_meta: dict,
    status: str,
) -> None:
    """Update client wiki page with outcome from a job."""
    client_path = CLIENTS_DIR / f"{client_id}.md"
    if not client_path.exists():
        _ensure_client_page(client_id)

    meta, body = _read_page(client_path)

    if status == "won":
        meta["wins"] = meta.get("wins", 0) + 1
    elif status == "lost":
        meta["losses"] = meta.get("losses", 0) + 1

    import json as _json
    updated_body = await _synthesize(
        context=(
            f"Current client page:\n{body}\n\n"
            f"New outcome: job [[jobs/{job_slug}]] status={status}\n"
            f"Job details: {_json.dumps(job_meta, default=str)}"
        ),
        instruction=(
            "Rewrite this client page body with the new outcome incorporated. Maintain:\n"
            "- ## Profile section\n"
            "- ## Win/Loss Summary with updated narrative\n"
            "- ## Recent Jobs with [[jobs/slug]] wikilink for the new job\n"
            "- ## Patterns section with any updated observations\n"
            "Keep existing job links. Add the new one."
        ),
    )

    _write_page(client_path, meta, updated_body)


async def _update_personality_page(
    personality: str,
    job_slug: str,
    job_meta: dict,
    status: str,
) -> None:
    """Update or create a personality wiki page with outcome from a job."""
    # Normalize personality name for filename (underscore → dash)
    filename = personality.replace("_", "-")
    page_path = PERSONALITIES_DIR / f"{filename}.md"

    if not page_path.exists():
        # Seed from PERSONALITY_PROMPTS
        _seed_personality_page(personality)

    meta, body = _read_page(page_path)

    if status == "won":
        meta["wins"] = meta.get("wins", 0) + 1
    meta["total_tournaments"] = meta.get("total_tournaments", 0) + 1
    total = meta.get("total_tournaments", 1)
    meta["win_rate"] = round(meta.get("wins", 0) / total, 4) if total > 0 else 0.0

    import json as _json
    updated_body = await _synthesize(
        context=(
            f"Current personality page:\n{body}\n\n"
            f"New result: job [[jobs/{job_slug}]] status={status}\n"
            f"Job details: {_json.dumps(job_meta, default=str)}"
        ),
        instruction=(
            "Update this personality page with the new job result. Add a short note to "
            "## Recent Results with the job wikilink, bid amount, and outcome. "
            "Update ## Performance if any new patterns are visible. "
            "Keep all existing content."
        ),
    )

    _write_page(page_path, meta, updated_body)


def _seed_personality_page(personality: str) -> None:
    """Create a personality page seeded from PERSONALITY_PROMPTS."""
    from backend.agents.tournament import PERSONALITY_PROMPTS

    filename = personality.replace("_", "-")
    page_path = PERSONALITIES_DIR / f"{filename}.md"
    prompt_text = PERSONALITY_PROMPTS.get(personality, "No prompt defined.")
    display_name = personality.replace("_", " ").title()

    meta = {
        "personality": personality,
        "total_tournaments": 0,
        "wins": 0,
        "win_rate": 0.0,
    }
    body = (
        f"# {display_name}\n\n"
        f"## Philosophy\n{prompt_text}\n\n"
        "## Performance\nNo data yet.\n\n"
        "## Recent Results\n\n"
        "## Evolution History\n"
    )
    _write_page(page_path, meta, body)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/bevo/Documents/answerD.it/TakeoffAI && uv run pytest tests/test_wiki_manager.py -x -v`
Expected: All 18 tests pass.

- [ ] **Step 5: Run full test suite**

Run: `cd /Users/bevo/Documents/answerD.it/TakeoffAI && uv run pytest tests/ -x -q`
Expected: All tests pass (existing 75 + new wiki tests).

- [ ] **Step 6: Commit**

```bash
git add backend/agents/wiki_manager.py tests/test_wiki_manager.py
git commit -m "feat(wiki): record_bid_decision(), cascade_outcome() — full cascade across job/client/personality pages"
```

---

### Task 7: wiki_manager.py — update_material_page() and lint()

**Files:**
- Modify: `backend/agents/wiki_manager.py`
- Modify: `tests/test_wiki_manager.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_wiki_manager.py`:

```python
@pytest.mark.anyio
async def test_update_material_page_creates_new(tmp_path, monkeypatch):
    """update_material_page should create a new material page if one doesn't exist."""
    import backend.agents.wiki_manager as wm
    monkeypatch.setattr(wm, "MATERIALS_DIR", tmp_path / "materials")

    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="# Concrete\n\n## Current Pricing\nVerified at $5.80/sqft.")]
    mock_client = AsyncMock()
    mock_client.messages.create = AsyncMock(return_value=mock_response)
    monkeypatch.setattr(wm, "_anthropic", mock_client)

    await wm.update_material_page(
        item="concrete",
        unit="sqft",
        ai_unit_cost=6.50,
        verified_mid=5.80,
        deviation_pct=12.07,
        category="structural",
    )

    # Find the created page (name may include date)
    pages = list((tmp_path / "materials").glob("*.md"))
    assert len(pages) == 1
    meta, body = wm._read_page(pages[0])
    assert meta["material"] == "concrete"
    assert meta["deviation_pct"] == 12.07
    assert "Pricing" in body


@pytest.mark.anyio
async def test_update_material_page_updates_existing(tmp_path, monkeypatch):
    """update_material_page should update an existing material page."""
    import backend.agents.wiki_manager as wm
    monkeypatch.setattr(wm, "MATERIALS_DIR", tmp_path / "materials")

    # Pre-create material page
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
    monkeypatch.setattr(wm, "_anthropic", mock_client)

    await wm.update_material_page(
        item="concrete",
        unit="sqft",
        ai_unit_cost=6.50,
        verified_mid=5.80,
        deviation_pct=12.07,
        category="structural",
    )

    meta, _ = wm._read_page(page_path)
    assert meta["deviation_pct"] == 12.07
    assert meta["verified_mid"] == 5.80


def test_lint_finds_broken_links(tmp_path, monkeypatch):
    """lint should detect wikilinks to nonexistent pages."""
    import backend.agents.wiki_manager as wm
    monkeypatch.setattr(wm, "WIKI_DIR", tmp_path)
    monkeypatch.setattr(wm, "JOBS_DIR", tmp_path / "jobs")
    monkeypatch.setattr(wm, "CLIENTS_DIR", tmp_path / "clients")
    monkeypatch.setattr(wm, "MATERIALS_DIR", tmp_path / "materials")
    monkeypatch.setattr(wm, "PERSONALITIES_DIR", tmp_path / "personalities")

    # Create a job page with a broken client link
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
    monkeypatch.setattr(wm, "WIKI_DIR", tmp_path)
    monkeypatch.setattr(wm, "JOBS_DIR", tmp_path / "jobs")
    monkeypatch.setattr(wm, "CLIENTS_DIR", tmp_path / "clients")
    monkeypatch.setattr(wm, "MATERIALS_DIR", tmp_path / "materials")
    monkeypatch.setattr(wm, "PERSONALITIES_DIR", tmp_path / "personalities")

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
    monkeypatch.setattr(wm, "WIKI_DIR", tmp_path)
    monkeypatch.setattr(wm, "JOBS_DIR", tmp_path / "jobs")
    monkeypatch.setattr(wm, "CLIENTS_DIR", tmp_path / "clients")
    monkeypatch.setattr(wm, "MATERIALS_DIR", tmp_path / "materials")
    monkeypatch.setattr(wm, "PERSONALITIES_DIR", tmp_path / "personalities")

    (tmp_path / "jobs").mkdir()
    # Job page missing required 'status' field
    job_path = tmp_path / "jobs" / "bad-job.md"
    wm._write_page(job_path, {"client": "acme"}, "# Bad Job")

    report = wm.lint()
    assert len(report["frontmatter_errors"]) >= 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/bevo/Documents/answerD.it/TakeoffAI && uv run pytest tests/test_wiki_manager.py::test_update_material_page_creates_new -x -v`
Expected: FAIL with `AttributeError: module has no attribute 'update_material_page'`

- [ ] **Step 3: Implement update_material_page and lint**

Add to `backend/agents/wiki_manager.py`:

```python
async def update_material_page(
    item: str,
    unit: str,
    ai_unit_cost: float,
    verified_mid: float,
    deviation_pct: float,
    category: str = "general",
) -> None:
    """
    Create or update a material wiki page from PriceVerifier data.
    """
    # Normalize item name to kebab-case filename
    filename = re.sub(r"[^a-zA-Z0-9\s-]", "", item)
    filename = re.sub(r"[\s-]+", "-", filename).strip("-").lower()
    page_path = MATERIALS_DIR / f"{filename}.md"

    today = date.today().isoformat()

    if page_path.exists():
        meta, body = _read_page(page_path)
        meta["last_verified"] = today
        meta["verified_mid"] = verified_mid
        meta["deviation_pct"] = deviation_pct
    else:
        meta = {
            "material": item.lower(),
            "category": category,
            "last_verified": today,
            "seed_low": None,
            "seed_high": None,
            "verified_mid": verified_mid,
            "deviation_pct": deviation_pct,
        }
        body = ""

    import json as _json
    context_data = {
        "item": item,
        "unit": unit,
        "ai_unit_cost": ai_unit_cost,
        "verified_mid": verified_mid,
        "deviation_pct": deviation_pct,
    }
    updated_body = await _synthesize(
        context=(
            f"Current page:\n{body}\n\n"
            f"New verification data:\n{_json.dumps(context_data)}"
        ),
        instruction=(
            "Write or update this material page. Include:\n"
            "- ## Current Pricing — verified price, AI price, deviation\n"
            "- ## Deviation History — add this data point to the trend\n"
            "- ## Job Impact — note which jobs used this material (if known)\n"
            "Keep existing content and add the new data point."
        ),
    )

    _write_page(page_path, meta, updated_body)


# ── Lint ─────────────────────────────────────────────────────────────────────

_REQUIRED_FRONTMATTER = {
    "jobs": ["status", "client"],
    "clients": ["client_id"],
    "personalities": ["personality"],
    "materials": ["material"],
}

_STALE_STATUSES = {"prospect", "estimated", "tournament-complete"}
_STALE_DAYS = 30


def lint() -> dict:
    """
    Run wiki health checks. Returns structured report dict.
    Checks: broken links, orphan pages, stale jobs, frontmatter validation.
    Does NOT auto-fix anything.
    """
    all_pages: dict[str, Path] = {}  # relative path (no ext) → full path
    all_links: list[tuple[str, str]] = []  # (source_page, link_target)
    inbound: set[str] = set()

    broken_links = []
    orphan_pages = []
    stale_jobs = []
    frontmatter_errors = []

    # Collect all pages
    for subdir in [JOBS_DIR, CLIENTS_DIR, MATERIALS_DIR, PERSONALITIES_DIR]:
        if not subdir.exists():
            continue
        for p in subdir.glob("*.md"):
            rel = f"{subdir.name}/{p.stem}"
            all_pages[rel] = p

    # Parse each page
    for rel, path in all_pages.items():
        meta, body = _parse_frontmatter(path)
        page_type = path.parent.name

        # Frontmatter validation
        required = _REQUIRED_FRONTMATTER.get(page_type, [])
        for field in required:
            if field not in meta:
                frontmatter_errors.append({
                    "page": rel,
                    "error": f"missing required field: {field}",
                })

        # Status validation for jobs
        if page_type == "jobs":
            valid_statuses = {"prospect", "estimated", "tournament-complete", "bid-submitted", "won", "lost", "closed"}
            if meta.get("status") and meta["status"] not in valid_statuses:
                frontmatter_errors.append({
                    "page": rel,
                    "error": f"invalid status: {meta['status']}",
                })

            # Stale check
            if meta.get("status") in _STALE_STATUSES and meta.get("date"):
                try:
                    job_date = date.fromisoformat(str(meta["date"]))
                    days = (date.today() - job_date).days
                    if days > _STALE_DAYS:
                        stale_jobs.append({
                            "slug": path.stem,
                            "status": meta["status"],
                            "days_stale": days,
                        })
                except (ValueError, TypeError):
                    pass

        # Extract wikilinks
        for match in re.finditer(r"\[\[([^\]]+)\]\]", body):
            link_target = match.group(1)
            all_links.append((rel, link_target))
            inbound.add(link_target)

    # Broken links: link targets that don't match any page
    for source, target in all_links:
        if target not in all_pages:
            broken_links.append({"page": source, "link": target})

    # Orphan pages: pages with no inbound links (exclude SCHEMA, DASHBOARD)
    for rel in all_pages:
        if rel not in inbound:
            orphan_pages.append(rel)

    return {
        "orphan_pages": orphan_pages,
        "broken_links": broken_links,
        "stale_jobs": stale_jobs,
        "frontmatter_errors": frontmatter_errors,
        "contradictions": [],  # LLM contradiction scan deferred to future iteration
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/bevo/Documents/answerD.it/TakeoffAI && uv run pytest tests/test_wiki_manager.py -x -v`
Expected: All 23 tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/agents/wiki_manager.py tests/test_wiki_manager.py
git commit -m "feat(wiki): update_material_page() and lint() — material tracking and health checks"
```

---

### Task 8: Wiki API Routes — Job CRUD + Lint Endpoint

**Files:**
- Create: `backend/api/wiki_routes.py`
- Create: `tests/test_wiki_routes.py`
- Modify: `backend/api/main.py`

- [ ] **Step 1: Write failing tests for the new endpoints**

Create `tests/test_wiki_routes.py`:

```python
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

    # Pre-create a job page
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/bevo/Documents/answerD.it/TakeoffAI && uv run pytest tests/test_wiki_routes.py -x -v`
Expected: FAIL (router not found or import error)

- [ ] **Step 3: Create wiki_routes.py**

Create `backend/api/wiki_routes.py`:

```python
"""
TakeoffAI — Wiki & Job Tracking route definitions.
Job pipeline CRUD and wiki lint endpoint.
"""

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from slowapi import Limiter
from slowapi.util import get_remote_address

from backend.agents import wiki_manager

logger = logging.getLogger(__name__)

limiter = Limiter(key_func=get_remote_address, default_limits=["60/minute"])

wiki_router = APIRouter()


# ── Request models ───────────────────────────────────────────────────────────

class JobCreateRequest(BaseModel):
    client_id: str = Field(..., min_length=1, description="Client identifier")
    project_name: str = Field(..., min_length=3, description="Human-readable project name")
    description: str = Field(..., min_length=10, description="Project description")
    zip_code: str = Field(..., min_length=5, max_length=10, description="Project ZIP code")
    trade_type: str = Field(default="general", description="Primary trade type")


class JobUpdateRequest(BaseModel):
    job_slug: str = Field(..., min_length=1, description="Job slug from create response")
    status: str = Field(..., description="New status: bid-submitted, won, lost, or closed")
    our_bid: Optional[float] = Field(default=None, ge=0, description="Bid amount (required for bid-submitted)")
    actual_cost: Optional[float] = Field(default=None, ge=0, description="Actual cost (required for closed)")
    notes: Optional[str] = Field(default="", description="Optional notes")


# ── Endpoints ────────────────────────────────────────────────────────────────

@wiki_router.post("/job/create")
@limiter.limit("10/minute")
async def job_create(request: Request, req: JobCreateRequest):
    """Create a new job at prospect status."""
    try:
        result = await wiki_manager.create_job(
            client_id=req.client_id,
            project_name=req.project_name,
            description=req.description,
            zip_code=req.zip_code,
            trade_type=req.trade_type,
        )
        return result
    except Exception as exc:
        logger.exception("job_create failed")
        raise HTTPException(status_code=500, detail="Internal server error") from exc


@wiki_router.post("/job/update")
@limiter.limit("10/minute")
async def job_update(request: Request, req: JobUpdateRequest):
    """Advance a job's status. Triggers wiki cascade for won/lost/closed."""
    valid_update_statuses = {"bid-submitted", "won", "lost", "closed"}
    if req.status not in valid_update_statuses:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status '{req.status}'. Must be one of: {', '.join(sorted(valid_update_statuses))}",
        )

    if req.status == "bid-submitted" and req.our_bid is None:
        raise HTTPException(status_code=400, detail="our_bid is required for bid-submitted status")

    if req.status == "closed" and req.actual_cost is None:
        raise HTTPException(status_code=400, detail="actual_cost is required for closed status")

    # Check job exists
    page_path = wiki_manager.JOBS_DIR / f"{req.job_slug}.md"
    if not page_path.exists():
        raise HTTPException(status_code=404, detail=f"Job '{req.job_slug}' not found")

    try:
        if req.status == "bid-submitted":
            await wiki_manager.record_bid_decision(
                job_slug=req.job_slug,
                our_bid=req.our_bid,
                notes=req.notes or "",
            )
        elif req.status in ("won", "lost", "closed"):
            await wiki_manager.cascade_outcome(
                job_slug=req.job_slug,
                status=req.status,
                actual_cost=req.actual_cost,
                notes=req.notes or "",
            )

        # Return updated frontmatter
        meta, _ = wiki_manager._read_page(page_path)
        return meta
    except Exception as exc:
        logger.exception("job_update failed")
        raise HTTPException(status_code=500, detail="Internal server error") from exc


@wiki_router.get("/job/{slug}")
async def job_get(slug: str):
    """Return a job's frontmatter as JSON."""
    page_path = wiki_manager.JOBS_DIR / f"{slug}.md"
    if not page_path.exists():
        raise HTTPException(status_code=404, detail=f"Job '{slug}' not found")

    meta, _ = wiki_manager._read_page(page_path)
    meta["job_slug"] = slug
    return meta


@wiki_router.get("/jobs")
async def jobs_list(status: Optional[str] = None):
    """List all jobs. Optional status filter: 'active' excludes closed and lost."""
    if not wiki_manager.JOBS_DIR.exists():
        return []

    results = []
    for path in wiki_manager.JOBS_DIR.glob("*.md"):
        meta, _ = wiki_manager._read_page(path)
        meta["job_slug"] = path.stem

        if status == "active" and meta.get("status") in ("closed", "lost"):
            continue
        elif status and status != "active" and meta.get("status") != status:
            continue

        results.append(meta)

    return results


@wiki_router.get("/wiki/lint")
async def wiki_lint():
    """Run wiki health check. Returns structured report."""
    try:
        report = wiki_manager.lint()
        return report
    except Exception as exc:
        logger.exception("wiki_lint failed")
        raise HTTPException(status_code=500, detail="Internal server error") from exc
```

- [ ] **Step 4: Register wiki_router in main.py**

In `backend/api/main.py`, add after the existing router imports:

```python
from backend.api.wiki_routes import wiki_router
```

And after the existing `app.include_router` lines, add:

```python
app.include_router(wiki_router, prefix="/api", dependencies=[Depends(verify_api_key)])
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /Users/bevo/Documents/answerD.it/TakeoffAI && uv run pytest tests/test_wiki_routes.py -x -v`
Expected: All 8 tests pass.

- [ ] **Step 6: Run full test suite**

Run: `cd /Users/bevo/Documents/answerD.it/TakeoffAI && uv run pytest tests/ -x -q`
Expected: All tests pass.

- [ ] **Step 7: Commit**

```bash
git add backend/api/wiki_routes.py backend/api/main.py tests/test_wiki_routes.py
git commit -m "feat(wiki): job CRUD + lint API routes — POST create/update, GET job/jobs/lint"
```

---

### Task 9: Fire-and-Forget Wiki Hooks in Existing Routes

**Files:**
- Modify: `backend/api/routes.py`
- Modify: `tests/test_routes.py`

- [ ] **Step 1: Write failing test for optional job_slug on estimate**

Add to `tests/test_routes.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/bevo/Documents/answerD.it/TakeoffAI && uv run pytest tests/test_routes.py::test_estimate_with_job_slug_fires_wiki_hook -x -v`
Expected: FAIL (job_slug not accepted as field)

- [ ] **Step 3: Add job_slug to EstimateRequest and TournamentRunRequest**

In `backend/api/routes.py`, modify the request models:

Add `job_slug` field to `EstimateRequest`:

```python
class EstimateRequest(BaseModel):
    description: str = Field(..., min_length=10, description="Plain-English project description")
    zip_code: str = Field(..., min_length=5, max_length=10, description="Project zip code for regional cost index")
    trade_type: str = Field(default="general", description="Primary trade (general, electrical, plumbing, etc.)")
    overhead_pct: float = Field(default=None, ge=0, le=100, description="Overhead % to apply")
    margin_pct: float = Field(default=None, ge=0, le=100, description="Target margin %")
    job_slug: Optional[str] = Field(default=None, description="Job slug to enrich wiki page (optional)")
```

Add `job_slug` field to `TournamentRunRequest`:

```python
class TournamentRunRequest(BaseModel):
    description: str = Field(..., min_length=10, description="Plain-English project description")
    zip_code: str = Field(..., min_length=5, max_length=10, description="Project zip code")
    trade_type: str = Field(default="general", description="Primary trade type")
    overhead_pct: float = Field(default=None, ge=0, le=100, description="Overhead %")
    margin_pct: float = Field(default=None, ge=0, le=100, description="Target margin %")
    client_id: Optional[str] = Field(default=None, description="Client ID for profile-aware bidding")
    n_agents: int = Field(default=5, ge=1, le=5, description="Number of agent personalities to run")
    n_samples: int = Field(default=2, ge=1, le=5, description="Samples per personality x temperature cell (1-5)")
    job_slug: Optional[str] = Field(default=None, description="Job slug to enrich wiki page (optional)")
```

- [ ] **Step 4: Add fire-and-forget wiki hooks to estimate and tournament_run**

Modify the `estimate` endpoint in `backend/api/routes.py`:

```python
@router.post("/estimate")
@limiter.limit("30/minute")
async def estimate(request: Request, req: EstimateRequest):
    """Run PreBidCalc agent — returns a line-item cost estimate."""
    try:
        result = await run_prebid_calc(
            description=req.description,
            zip_code=req.zip_code,
            trade_type=req.trade_type,
            overhead_pct=req.resolved_overhead(),
            margin_pct=req.resolved_margin(),
        )

        # Fire-and-forget wiki enrichment if job_slug provided
        if req.job_slug:
            asyncio.ensure_future(_wiki_enrich_estimate(req.job_slug, result))

        return result
    except Exception as exc:
        logging.exception("estimate failed")
        raise HTTPException(status_code=500, detail="Internal server error") from exc
```

Modify the `tournament_run` endpoint:

```python
@router.post("/tournament/run")
@limiter.limit("10/minute")
async def tournament_run(request: Request, req: TournamentRunRequest):
    """Run a bid tournament — N agents estimate the same project in parallel."""
    try:
        result = await run_tournament(
            description=req.description,
            zip_code=req.zip_code,
            trade_type=req.trade_type,
            overhead_pct=req.resolved_overhead(),
            margin_pct=req.resolved_margin(),
            client_id=req.client_id,
            n_agents=req.n_agents,
            n_samples=req.n_samples,
        )

        def _serialize_entry(e):
            return {
                "agent_name": e.agent_name,
                "total_bid": e.total_bid,
                "margin_pct": e.margin_pct,
                "confidence": e.confidence,
                "temperature": e.temperature,
                "sample_index": e.sample_index,
                "estimate": e.estimate,
                "error": e.error,
            }

        response = {
            "tournament_id": result.tournament_id,
            "entries": [_serialize_entry(e) for e in result.entries],
            "consensus_entries": [_serialize_entry(e) for e in result.consensus_entries],
        }

        # Fire-and-forget wiki enrichment if job_slug provided
        if req.job_slug:
            asyncio.ensure_future(_wiki_enrich_tournament(req.job_slug, response))

        return response
    except Exception as exc:
        logging.exception("tournament_run failed")
        raise HTTPException(status_code=500, detail="Internal server error") from exc
```

Add the fire-and-forget helper functions at the bottom of routes.py (before any endpoint definitions or after imports — just needs to be importable in the endpoints):

```python
# ── Wiki fire-and-forget helpers ─────────────────────────────────────────────

async def _wiki_enrich_estimate(job_slug: str, estimate_data: dict) -> None:
    """Fire-and-forget: enrich wiki job page with estimate data."""
    try:
        from backend.agents.wiki_manager import enrich_estimate
        await enrich_estimate(job_slug, estimate_data)
    except Exception:
        logging.exception("wiki enrich_estimate failed for %s (non-fatal)", job_slug)


async def _wiki_enrich_tournament(job_slug: str, tournament_data: dict) -> None:
    """Fire-and-forget: enrich wiki job page with tournament data."""
    try:
        from backend.agents.wiki_manager import enrich_tournament
        await enrich_tournament(job_slug, tournament_data)
    except Exception:
        logging.exception("wiki enrich_tournament failed for %s (non-fatal)", job_slug)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /Users/bevo/Documents/answerD.it/TakeoffAI && uv run pytest tests/test_routes.py -x -v`
Expected: All tests pass.

- [ ] **Step 6: Run full test suite**

Run: `cd /Users/bevo/Documents/answerD.it/TakeoffAI && uv run pytest tests/ -x -q`
Expected: All tests pass.

- [ ] **Step 7: Commit**

```bash
git add backend/api/routes.py tests/test_routes.py
git commit -m "feat(wiki): fire-and-forget wiki hooks — optional job_slug on estimate and tournament endpoints"
```

---

### Task 10: Seed Personality Pages

**Files:**
- Create: `wiki/personalities/conservative.md`
- Create: `wiki/personalities/balanced.md`
- Create: `wiki/personalities/aggressive.md`
- Create: `wiki/personalities/historical-match.md`
- Create: `wiki/personalities/market-beater.md`

- [ ] **Step 1: Write a test that seeding works**

Add to `tests/test_wiki_manager.py`:

```python
def test_seed_personality_page(tmp_path, monkeypatch):
    """_seed_personality_page should create a page from PERSONALITY_PROMPTS."""
    import backend.agents.wiki_manager as wm
    monkeypatch.setattr(wm, "PERSONALITIES_DIR", tmp_path / "personalities")

    wm._seed_personality_page("conservative")

    page = tmp_path / "personalities" / "conservative.md"
    assert page.exists()
    meta, body = wm._parse_frontmatter(page)
    assert meta["personality"] == "conservative"
    assert meta["wins"] == 0
    assert "CONSERVATIVE" in body
    assert "## Philosophy" in body
    assert "## Performance" in body
```

- [ ] **Step 2: Run test to verify it passes (implementation already exists from Task 6)**

Run: `cd /Users/bevo/Documents/answerD.it/TakeoffAI && uv run pytest tests/test_wiki_manager.py::test_seed_personality_page -x -v`
Expected: PASS

- [ ] **Step 3: Create the 5 personality pages**

Run a one-time Python script to seed the pages:

```bash
cd /Users/bevo/Documents/answerD.it/TakeoffAI && uv run python -c "
from backend.agents.wiki_manager import _seed_personality_page, PERSONALITIES_DIR
PERSONALITIES_DIR.mkdir(parents=True, exist_ok=True)
for p in ['conservative', 'balanced', 'aggressive', 'historical_match', 'market_beater']:
    _seed_personality_page(p)
    print(f'Seeded: {p}')
"
```

- [ ] **Step 4: Verify the 5 pages exist**

```bash
ls -la wiki/personalities/
```

Expected: `conservative.md`, `balanced.md`, `aggressive.md`, `historical-match.md`, `market-beater.md`

- [ ] **Step 5: Remove .gitkeep files from populated directories**

```bash
rm -f wiki/personalities/.gitkeep
```

- [ ] **Step 6: Commit**

```bash
git add wiki/personalities/
git commit -m "feat(wiki): seed 5 personality pages from PERSONALITY_PROMPTS"
```

---

### Task 11: Install PyYAML Dependency

**Files:**
- Modify: `pyproject.toml`

The `wiki_manager.py` module uses `import yaml` (PyYAML) for frontmatter parsing. This dependency needs to be added to the project.

- [ ] **Step 1: Check if pyyaml is already installed**

Run: `cd /Users/bevo/Documents/answerD.it/TakeoffAI && uv run python -c "import yaml; print(yaml.__version__)"`

If this succeeds, skip to Step 4. If it fails with ImportError, continue to Step 2.

- [ ] **Step 2: Add pyyaml to pyproject.toml**

Run: `cd /Users/bevo/Documents/answerD.it/TakeoffAI && uv add pyyaml`

- [ ] **Step 3: Verify import works**

Run: `cd /Users/bevo/Documents/answerD.it/TakeoffAI && uv run python -c "import yaml; print(yaml.__version__)"`
Expected: Prints a version number (e.g., `6.0.1`)

- [ ] **Step 4: Run full test suite**

Run: `cd /Users/bevo/Documents/answerD.it/TakeoffAI && uv run pytest tests/ -x -q`
Expected: All tests pass.

- [ ] **Step 5: Commit (only if pyproject.toml changed)**

```bash
git add pyproject.toml uv.lock
git commit -m "deps: add pyyaml for wiki frontmatter parsing"
```

---

### Task 12: Integration Test — Full Job Lifecycle

**Files:**
- Modify: `tests/test_wiki_manager.py`

- [ ] **Step 1: Write integration test**

Add to `tests/test_wiki_manager.py`:

```python
@pytest.mark.anyio
async def test_full_job_lifecycle(tmp_path, monkeypatch):
    """End-to-end: prospect → estimated → tournament-complete → bid-submitted → won → closed."""
    import backend.agents.wiki_manager as wm
    monkeypatch.setattr(wm, "WIKI_DIR", tmp_path)
    monkeypatch.setattr(wm, "JOBS_DIR", tmp_path / "jobs")
    monkeypatch.setattr(wm, "CLIENTS_DIR", tmp_path / "clients")
    monkeypatch.setattr(wm, "PERSONALITIES_DIR", tmp_path / "personalities")
    monkeypatch.setattr(wm, "MATERIALS_DIR", tmp_path / "materials")

    # Mock all LLM calls
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="## Section\nLLM-generated content.")]
    mock_client = AsyncMock()
    mock_client.messages.create = AsyncMock(return_value=mock_response)
    monkeypatch.setattr(wm, "_anthropic", mock_client)

    # 1. Create job (prospect)
    result = await wm.create_job(
        client_id="lifecycle-test",
        project_name="Full Lifecycle Job",
        description="A test project to verify the complete job pipeline works end-to-end",
        zip_code="78701",
        trade_type="general",
    )
    slug = result["job_slug"]
    meta, _ = wm._read_page(tmp_path / "jobs" / f"{slug}.md")
    assert meta["status"] == "prospect"

    # 2. Enrich with estimate
    await wm.enrich_estimate(slug, {
        "total_bid": 150000.0,
        "estimate_low": 135000.0,
        "estimate_high": 165000.0,
        "confidence": "high",
        "line_items": [],
    })
    meta, _ = wm._read_page(tmp_path / "jobs" / f"{slug}.md")
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
    meta, _ = wm._read_page(tmp_path / "jobs" / f"{slug}.md")
    assert meta["status"] == "tournament-complete"
    assert meta["tournament_id"] == 99
    assert meta["band_low"] == 135000
    assert meta["band_high"] == 170000

    # 4. Record bid decision
    await wm.record_bid_decision(slug, our_bid=150000.0, notes="Going with Balanced")
    meta, _ = wm._read_page(tmp_path / "jobs" / f"{slug}.md")
    assert meta["status"] == "bid-submitted"
    assert meta["our_bid"] == 150000.0

    # 5. Cascade: won
    await wm.cascade_outcome(slug, status="won", notes="Client accepted")
    meta, _ = wm._read_page(tmp_path / "jobs" / f"{slug}.md")
    assert meta["status"] == "won"

    # Client page should show win
    client_meta, _ = wm._read_page(tmp_path / "clients" / "lifecycle-test.md")
    assert client_meta["wins"] == 1

    # 6. Cascade: closed with actual cost
    await wm.cascade_outcome(slug, status="closed", actual_cost=142000.0)
    meta, _ = wm._read_page(tmp_path / "jobs" / f"{slug}.md")
    assert meta["status"] == "closed"
    assert meta["actual_cost"] == 142000.0

    # Verify LLM was called multiple times (at least once per stage)
    assert mock_client.messages.create.call_count >= 6
```

- [ ] **Step 2: Run integration test**

Run: `cd /Users/bevo/Documents/answerD.it/TakeoffAI && uv run pytest tests/test_wiki_manager.py::test_full_job_lifecycle -x -v`
Expected: PASS

- [ ] **Step 3: Run full test suite**

Run: `cd /Users/bevo/Documents/answerD.it/TakeoffAI && uv run pytest tests/ -x -q`
Expected: All tests pass.

- [ ] **Step 4: Commit**

```bash
git add tests/test_wiki_manager.py
git commit -m "test(wiki): full job lifecycle integration test — prospect through closed"
```

---

## Self-Review

**Spec coverage:**
- [x] wiki/ directory structure — Task 1
- [x] SCHEMA.md — Task 1
- [x] DASHBOARD.md — Task 1
- [x] wiki_model config — Task 1
- [x] Frontmatter helpers — Task 2
- [x] _synthesize() — Task 3
- [x] create_job() — Task 4
- [x] enrich_estimate() / enrich_tournament() — Task 5
- [x] record_bid_decision() / cascade_outcome() — Task 6
- [x] update_material_page() / lint() — Task 7
- [x] Job CRUD + lint API routes — Task 8
- [x] Fire-and-forget hooks on estimate/tournament — Task 9
- [x] Seeded personality pages — Task 10
- [x] PyYAML dependency — Task 11
- [x] Full lifecycle integration test — Task 12

**Placeholder scan:** No TBDs or TODOs. All code blocks are complete.

**Type consistency:** `_parse_frontmatter` → `tuple[dict, str]`, `_write_page(path, meta, body)`, `_read_page` → alias for `_parse_frontmatter`. Consistent across all tasks. `_make_job_slug` returns `str`, used in `create_job` and passed through API responses. `lint()` returns `dict` with keys matching the spec exactly.
