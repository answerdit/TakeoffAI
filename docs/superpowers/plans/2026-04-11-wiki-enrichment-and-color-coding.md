# Wiki Enrichment and Color Coding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `subs/` and `suppliers/` page types, extend job frontmatter with 8 new fields, and establish a 5-color semantic palette via a CSS snippet + callout taxonomy — all shipped in 3 independently-mergeable phases.

**Architecture:** Preserve the existing 5-file split under `backend/agents/_wiki_*`. New entity writers land in `_wiki_entities.py` (217 → ~380 lines). Cascade hooks extend `_wiki_jobs.py` (357 → ~440 lines). Color coding is 100% consumer-side (CSS snippet + SCHEMA.md rules) — zero backend runtime changes for Phase 1. Stub-first lazy enrichment gated on `WIKI_ENRICH_MIN_LINKS` keeps LLM cost bounded.

**Tech Stack:** Python 3.14, FastAPI, pytest + anyio, Obsidian (vault consumer), plain Dataview (no DataviewJS), Anthropic SDK (mocked in tests).

---

## File Structure

### Phase 1 — Markdown + CSS only

- **Modify:** `wiki/SCHEMA.md` — add sub/supplier sections, callout taxonomy with color mapping, color-conventions section
- **Create:** `wiki/.obsidian/snippets/takeoffai-tags.css` — ~40-line tag color snippet
- **Modify:** `wiki/DASHBOARD.md` — append 4 new Dataview blocks

### Phase 2 — New writers + tests

- **Modify:** `backend/agents/_wiki_io.py` — add `SUBS_DIR`, `SUPPLIERS_DIR`, `_safe_sub_path`, `_safe_supplier_path`
- **Modify:** `backend/agents/_wiki_entities.py` — add 6 new functions (`create_sub_stub`, `ensure_sub_stub`, `enrich_sub`, `create_supplier_stub`, `ensure_supplier_stub`, `enrich_supplier`)
- **Modify:** `backend/agents/wiki_manager.py` — re-export new symbols
- **Modify:** `tests/test_wiki_manager.py` — add test class(es) for new writers

### Phase 3 — Cascade + lint + route

- **Modify:** `backend/agents/_wiki_jobs.py` — extend `create_job`, `enrich_tournament`, `cascade_outcome`; add `_parse_sqft_from_scope`, `_compute_margin_pct`, `_compute_delta_vs_actual_pct`, `_recompute_sub_variance`
- **Modify:** `backend/agents/_wiki_lint.py` — add 4 new checks
- **Modify:** `backend/api/wiki_routes.py` — accept new fields on `POST /api/job/update`
- **Modify:** `tests/test_wiki_manager.py` and `tests/test_wiki_routes.py` — cascade + route tests

---

## Conventions This Plan Follows

- **Testing:** `@pytest.mark.anyio` for async, `tmp_path` fixture, `monkeypatch.setattr(_io, "<DIR>", tmp_path / "...")` for path redirection, `_llm._anthropic` mocked with `AsyncMock`. HTTP-layer tests *also* patch `wm.<DIR>` — see `TakeoffAI/CLAUDE.md` → Testing → Wiki patching pattern.
- **Commits:** After each task. Message format follows existing project style: `feat(wiki): <what>`, `test(wiki): <what>`, `docs(wiki): <what>`.
- **Commands:** Run from `TakeoffAI/` root. All commands use `uv run`.
- **Frontmatter writes:** Always via `_io._write_page(path, meta, body)`. Never write raw markdown.
- **Idempotence:** `ensure_*_stub` functions must be safe to call repeatedly with the same args.

---

# Phase 1 — Schema + CSS (no code risk)

## Task 1: Update SCHEMA.md with new page types and color conventions

**Files:**
- Modify: `wiki/SCHEMA.md`

- [ ] **Step 1: Read current SCHEMA.md to locate insertion points**

Run: `cat wiki/SCHEMA.md`

Locate the end of the "### Material (`wiki/materials/`)" block (insert sub + supplier sections after it) and the end of the existing "## Callouts" section (replace with expanded taxonomy).

- [ ] **Step 2: Insert the Sub page type section**

After the existing `### Material (wiki/materials/)` block, insert:

```markdown
### Sub (`wiki/subs/`)
Profile for a subcontractor or crew the contractor works with.

**Required frontmatter:** sub_id, trade_specialty, first_job, total_jobs
**Optional frontmatter:** company, region, labor_rate_hourly, labor_rate_source (quoted|observed|inferred), typical_markup_pct, reliability (high|medium|low|unknown), last_job_date, avg_cost_variance_pct

**Section order:** Profile, Labor & Markup, Reliability Notes, Jobs Worked, Patterns

**Derived reliability tag** (only applied when total_jobs >= 3):
- `reliability-high` if avg_cost_variance_pct <= 5
- `reliability-medium` if 5 < avg_cost_variance_pct <= 15
- `reliability-low` if avg_cost_variance_pct > 15

### Supplier (`wiki/suppliers/`)
Profile for a materials vendor.

**Required frontmatter:** supplier_id, first_quote, total_materials, active
**Optional frontmatter:** company, region, delivery_radius_mi, materials_sourced, avg_quote_deviation_pct, last_quote_date

**Section order:** Profile, Materials Sourced, Quote History, Deviation Notes, Jobs Referenced
```

- [ ] **Step 3: Update the Tags table with sub/supplier entries**

In the "## Tags" table, add two rows:

```markdown
| Sub | `sub`, `{trade_specialty}` | `reliability-high`, `reliability-medium`, `reliability-low`, `red-flag` |
| Supplier | `supplier`, `active` or `inactive` | `price-flag` |
```

- [ ] **Step 4: Replace the Callouts section with the full taxonomy**

Replace the existing "## Callouts" section with:

```markdown
## Callouts

Use Obsidian callouts for at-a-glance scanning. Every callout type maps to one of five semantic colors.

| Callout | Color | When to use |
|---|---|---|
| `[!success]` | green | job won, margin ≥ target, band width < 15%, low-variance sub |
| `[!tip]` | green | risk assessment — high confidence, tight band |
| `[!info]` | yellow | prospect notes, neutral tournament commentary |
| `[!caution]` | amber | price deviation 5–9%, scope ambiguity, medium reliability |
| `[!warning]` | amber | price deviation ≥ 10% |
| `[!danger]` | red | underbid risk, low reliability, lost-with-gap |
| `[!abstract]` | slate | philosophy / strategy sections |
| `[!failure]` | slate | closed / archived / out-of-scope notes |

### Examples

```markdown
> [!warning] Price Deviation: +18%
> Verified mid-market price is $4.20/LF vs. AI seed of $3.55/LF. Flag for review.

> [!success] Margin Leader
> Won at $24,849 with realized margin of 18.4% — second-best on record for this trade.

> [!danger] Underbid Risk
> Our bid of $X is below band_low of $Y — investigate before submitting.

> [!abstract] Bidding Strategy
> Strategy description and bullet rules here.
\`\`\`
```

- [ ] **Step 5: Append a Color Conventions section**

Append at the end of `wiki/SCHEMA.md`:

```markdown
## Color Conventions

The vault uses a 5-color semantic palette. Every callout and every tag maps to exactly one color.

| Color | Accent / Background | Meaning |
|---|---|---|
| GREEN | #67c23a / #e1f5d8 | success, low risk, won, tight band, high reliability |
| YELLOW | #f0c040 / #fffbe6 | info, neutral, prospect, tournament running |
| AMBER | #e6a23c / #ffebcc | caution, price deviation 5–9%, medium risk, medium reliability |
| RED | #d9363e / #fde3e3 | danger, underbid, price deviation ≥10%, lost, low reliability |
| SLATE | #6272a4 / #e8e8f5 | closed, inactive, archived, strategy/abstract |

### Tag Families

Four orthogonal families; a typical page shows 3–4 tags total:

- **Status**: `won`/`lost`/`prospect`/`tournament-complete`/`closed` → green/red/yellow/yellow/slate
- **Risk**: `red-flag`/`price-flag`/`reliability-high`/`reliability-medium`/`reliability-low` → red/amber/green/amber/red
- **Type**: `job`/`client`/`personality`/`material` → blue; `sub`/`supplier` → teal
- **Trade**: all trade names (`remodel`, `electrical`, `plumbing`, `hvac`, `tile`, etc.) → purple

Tag colors are rendered by the `takeoffai-tags.css` snippet at `wiki/.obsidian/snippets/takeoffai-tags.css`. Enable it once in Obsidian → Settings → Appearance → CSS snippets.
```

- [ ] **Step 6: Commit**

```bash
git add wiki/SCHEMA.md
git commit -m "docs(wiki): add sub/supplier page types and 5-color palette to SCHEMA"
```

---

## Task 2: Create the tag CSS snippet

**Files:**
- Create: `wiki/.obsidian/snippets/takeoffai-tags.css`

- [ ] **Step 1: Create the snippet file with the full tag color map**

Write `wiki/.obsidian/snippets/takeoffai-tags.css`:

```css
/* TakeoffAI — tag color map
 * Maps vault tags to the 5-color semantic palette.
 * Enable in Obsidian → Settings → Appearance → CSS snippets.
 */

/* ── GREEN: success / low risk ──────────────────────────── */
.tag[href="#won"],
.tag[href="#reliability-high"] {
  background-color: #67c23a;
  color: #ffffff;
  border: none;
}

/* ── YELLOW: info / neutral / in-progress ───────────────── */
.tag[href="#prospect"],
.tag[href="#tournament-complete"] {
  background-color: #f0c040;
  color: #333333;
  border: none;
}

/* ── AMBER: caution / medium risk ───────────────────────── */
.tag[href="#price-flag"],
.tag[href="#reliability-medium"] {
  background-color: #e6a23c;
  color: #ffffff;
  border: none;
}

/* ── RED: danger / high risk ────────────────────────────── */
.tag[href="#red-flag"],
.tag[href="#lost"],
.tag[href="#reliability-low"] {
  background-color: #d9363e;
  color: #ffffff;
  border: none;
}

/* ── SLATE: archived / inactive ─────────────────────────── */
.tag[href="#closed"],
.tag[href="#inactive"] {
  background-color: #6272a4;
  color: #ffffff;
  border: none;
}

/* ── TEAL: party type (subs + suppliers) ────────────────── */
.tag[href="#sub"],
.tag[href="#supplier"],
.tag[href="#active"] {
  background-color: #16a085;
  color: #ffffff;
  border: none;
}

/* ── BLUE: internal type ────────────────────────────────── */
.tag[href="#job"],
.tag[href="#client"],
.tag[href="#personality"],
.tag[href="#material"] {
  background-color: #409eff;
  color: #ffffff;
  border: none;
}

/* ── PURPLE: trade family (all trades) ──────────────────── */
.tag[href="#remodel"],
.tag[href="#electrical"],
.tag[href="#plumbing"],
.tag[href="#hvac"],
.tag[href="#tile"],
.tag[href="#flooring"],
.tag[href="#framing"],
.tag[href="#roofing"],
.tag[href="#concrete"],
.tag[href="#general"] {
  background-color: #9b59b6;
  color: #ffffff;
  border: none;
}
```

- [ ] **Step 2: Verify Obsidian picks it up (manual)**

Open Obsidian → open the `wiki/` vault → Settings → Appearance → CSS snippets → toggle `takeoffai-tags` ON. Open any personality page and confirm the `#personality` tag now renders with a blue pill.

Expected: tag pills visible with palette colors.

- [ ] **Step 3: Commit**

```bash
git add wiki/.obsidian/snippets/takeoffai-tags.css
git commit -m "feat(wiki): add takeoffai-tags.css snippet for 5-color tag palette"
```

---

## Task 3: Add new Dataview blocks to DASHBOARD.md

**Files:**
- Modify: `wiki/DASHBOARD.md`

- [ ] **Step 1: Append the four new Dataview blocks**

At the end of `wiki/DASHBOARD.md`, append:

````markdown

## Sub Roster

```dataview
TABLE trade_specialty AS "Trade", total_jobs AS "Jobs", labor_rate_hourly AS "Rate", avg_cost_variance_pct AS "Variance %", reliability
FROM "subs"
SORT avg_cost_variance_pct ASC
```

## Supplier Watch

```dataview
TABLE total_materials AS "Materials", avg_quote_deviation_pct AS "Deviation %", last_quote_date AS "Last Quote", active
FROM "suppliers"
WHERE avg_quote_deviation_pct > 5
SORT avg_quote_deviation_pct DESC
```

## Won Jobs — Margin Leaderboard

```dataview
TABLE client, trade, our_bid AS "Bid", actual_cost AS "Actual", margin_pct AS "Margin %"
FROM "jobs"
WHERE status = "won" AND margin_pct != null
SORT margin_pct DESC
LIMIT 20
```

## Calibration — Delta vs Actual

```dataview
TABLE date, client, trade, our_bid AS "Bid", actual_cost AS "Actual", delta_vs_actual_pct AS "Δ vs Actual %"
FROM "jobs"
WHERE delta_vs_actual_pct != null
SORT date DESC
LIMIT 20
```
````

- [ ] **Step 2: Commit**

```bash
git add wiki/DASHBOARD.md
git commit -m "docs(wiki): add Sub Roster, Supplier Watch, Margin Leaderboard, Calibration views"
```

- [ ] **Step 3: Verify Phase 1 end-to-end (manual)**

Open `wiki/DASHBOARD.md` in Obsidian. Expected: new sections render (empty until data lands), existing sections unchanged.

Run the existing test suite to confirm nothing regressed:

```bash
uv run pytest tests/test_wiki_manager.py tests/test_wiki_routes.py -v
```

Expected: all existing tests pass (no Phase 1 changes touched code).

---

# Phase 2 — New writers + stubs (code, no cascade changes)

## Task 4: Add SUBS_DIR and SUPPLIERS_DIR path constants + safety helpers

**Files:**
- Modify: `backend/agents/_wiki_io.py:15-29`
- Test: `tests/test_wiki_manager.py`

- [ ] **Step 1: Write failing tests for the new path safety helpers**

Add to `tests/test_wiki_manager.py` (near the top, after existing path tests):

```python
def test_safe_sub_path_rejects_traversal(tmp_path, monkeypatch):
    import backend.agents._wiki_io as _io

    monkeypatch.setattr(_io, "SUBS_DIR", tmp_path / "subs")
    assert _io._safe_sub_path("../etc/passwd") is None
    assert _io._safe_sub_path("rivera-tile") is not None


def test_safe_supplier_path_rejects_traversal(tmp_path, monkeypatch):
    import backend.agents._wiki_io as _io

    monkeypatch.setattr(_io, "SUPPLIERS_DIR", tmp_path / "suppliers")
    assert _io._safe_supplier_path("../../secret") is None
    assert _io._safe_supplier_path("bmc-lumber") is not None
```

- [ ] **Step 2: Run the tests and verify they fail**

```bash
uv run pytest tests/test_wiki_manager.py::test_safe_sub_path_rejects_traversal tests/test_wiki_manager.py::test_safe_supplier_path_rejects_traversal -v
```

Expected: both FAIL with `AttributeError: module 'backend.agents._wiki_io' has no attribute 'SUBS_DIR'` (or `_safe_sub_path`).

- [ ] **Step 3: Add path constants and safety helpers to `_wiki_io.py`**

In `backend/agents/_wiki_io.py`, after line 19 (`PERSONALITIES_DIR = WIKI_DIR / "personalities"`), add:

```python
SUBS_DIR = WIKI_DIR / "subs"
SUPPLIERS_DIR = WIKI_DIR / "suppliers"
```

Then after `_safe_job_path` (around line 29), add:

```python
def _safe_sub_path(sub_id: str) -> Path | None:
    """Return the resolved sub page path, or None if sub_id is unsafe."""
    page_path = (SUBS_DIR / f"{sub_id}.md").resolve()
    if not page_path.is_relative_to(SUBS_DIR.resolve()):
        logger.warning("Rejected unsafe sub_id: %s", sub_id)
        return None
    return page_path


def _safe_supplier_path(supplier_id: str) -> Path | None:
    """Return the resolved supplier page path, or None if supplier_id is unsafe."""
    page_path = (SUPPLIERS_DIR / f"{supplier_id}.md").resolve()
    if not page_path.is_relative_to(SUPPLIERS_DIR.resolve()):
        logger.warning("Rejected unsafe supplier_id: %s", supplier_id)
        return None
    return page_path
```

- [ ] **Step 4: Run the tests and verify they pass**

```bash
uv run pytest tests/test_wiki_manager.py::test_safe_sub_path_rejects_traversal tests/test_wiki_manager.py::test_safe_supplier_path_rejects_traversal -v
```

Expected: both PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/agents/_wiki_io.py tests/test_wiki_manager.py
git commit -m "feat(wiki): add SUBS_DIR and SUPPLIERS_DIR path constants with safety helpers"
```

---

## Task 5: Add `create_sub_stub` and `ensure_sub_stub` (no LLM)

**Files:**
- Modify: `backend/agents/_wiki_entities.py`
- Test: `tests/test_wiki_manager.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_wiki_manager.py`:

```python
def test_create_sub_stub_writes_correct_frontmatter(tmp_path, monkeypatch):
    import backend.agents._wiki_io as _io
    from backend.agents._wiki_entities import create_sub_stub

    monkeypatch.setattr(_io, "SUBS_DIR", tmp_path / "subs")

    path = create_sub_stub("rivera-tile", "tile")

    assert path.exists()
    meta, body = _io._parse_frontmatter(path)
    assert meta["sub_id"] == "rivera-tile"
    assert meta["trade_specialty"] == "tile"
    assert meta["total_jobs"] == 0
    assert meta["first_job"] is None
    assert "sub" in meta["tags"]
    assert "tile" in meta["tags"]
    assert "# rivera-tile" in body or "rivera-tile" in body.lower()


def test_create_sub_stub_rejects_traversal(tmp_path, monkeypatch):
    import backend.agents._wiki_io as _io
    from backend.agents._wiki_entities import create_sub_stub

    monkeypatch.setattr(_io, "SUBS_DIR", tmp_path / "subs")
    result = create_sub_stub("../evil", "tile")
    assert result is None


def test_ensure_sub_stub_is_idempotent(tmp_path, monkeypatch):
    import backend.agents._wiki_io as _io
    from backend.agents._wiki_entities import create_sub_stub, ensure_sub_stub

    monkeypatch.setattr(_io, "SUBS_DIR", tmp_path / "subs")

    path1 = create_sub_stub("rivera-tile", "tile")
    first_mtime = path1.stat().st_mtime

    path2 = ensure_sub_stub("rivera-tile", "tile")
    assert path2 == path1
    assert path2.stat().st_mtime == first_mtime  # not rewritten


def test_ensure_sub_stub_creates_if_missing(tmp_path, monkeypatch):
    import backend.agents._wiki_io as _io
    from backend.agents._wiki_entities import ensure_sub_stub

    monkeypatch.setattr(_io, "SUBS_DIR", tmp_path / "subs")
    path = ensure_sub_stub("new-sub", "electrical")
    assert path is not None
    assert path.exists()
```

- [ ] **Step 2: Run and verify they fail**

```bash
uv run pytest tests/test_wiki_manager.py -k "sub_stub" -v
```

Expected: all 4 FAIL with `ImportError` on `create_sub_stub` / `ensure_sub_stub`.

- [ ] **Step 3: Add `create_sub_stub` and `ensure_sub_stub` to `_wiki_entities.py`**

Append to `backend/agents/_wiki_entities.py`:

```python
def create_sub_stub(sub_id: str, trade_specialty: str) -> "Path | None":
    """
    Create a no-LLM sub page skeleton. Returns the page path, or None if sub_id is unsafe.
    Does nothing (and returns the existing path) if the page already exists.
    """
    page_path = _io._safe_sub_path(sub_id)
    if page_path is None:
        return None
    if page_path.exists():
        return page_path

    meta = {
        "sub_id": sub_id,
        "trade_specialty": trade_specialty,
        "first_job": None,
        "total_jobs": 0,
        "avg_cost_variance_pct": None,
        "tags": ["sub", trade_specialty],
    }
    body = (
        f"# {sub_id}\n\n"
        "## Profile\n"
        f"Subcontractor profile — enriched after {{WIKI_ENRICH_MIN_LINKS}}+ jobs.\n\n"
        "## Labor & Markup\n"
        "No data yet.\n\n"
        "## Reliability Notes\n"
        "No data yet.\n\n"
        "## Jobs Worked\n\n"
        "## Patterns\n"
        "Insufficient data for pattern analysis.\n"
    )
    _io._write_page(page_path, meta, body)
    return page_path


def ensure_sub_stub(sub_id: str, trade_specialty: str) -> "Path | None":
    """Idempotent stub creator. Safe to call repeatedly with the same args."""
    page_path = _io._safe_sub_path(sub_id)
    if page_path is None:
        return None
    if page_path.exists():
        return page_path
    return create_sub_stub(sub_id, trade_specialty)
```

Add `from pathlib import Path` import if not already present (check line 1-15 of the file).

- [ ] **Step 4: Run the tests and verify they pass**

```bash
uv run pytest tests/test_wiki_manager.py -k "sub_stub" -v
```

Expected: all 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/agents/_wiki_entities.py tests/test_wiki_manager.py
git commit -m "feat(wiki): add create_sub_stub and ensure_sub_stub (no-LLM skeleton)"
```

---

## Task 6: Add `create_supplier_stub` and `ensure_supplier_stub`

**Files:**
- Modify: `backend/agents/_wiki_entities.py`
- Test: `tests/test_wiki_manager.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_wiki_manager.py`:

```python
def test_create_supplier_stub_writes_correct_frontmatter(tmp_path, monkeypatch):
    import backend.agents._wiki_io as _io
    from backend.agents._wiki_entities import create_supplier_stub

    monkeypatch.setattr(_io, "SUPPLIERS_DIR", tmp_path / "suppliers")

    path = create_supplier_stub("bmc-lumber", materials=["2x4-stud", "osb-sheathing"])

    assert path.exists()
    meta, body = _io._parse_frontmatter(path)
    assert meta["supplier_id"] == "bmc-lumber"
    assert meta["total_materials"] == 2
    assert meta["active"] is True
    assert meta["materials_sourced"] == ["2x4-stud", "osb-sheathing"]
    assert "supplier" in meta["tags"]
    assert "active" in meta["tags"]


def test_create_supplier_stub_no_materials(tmp_path, monkeypatch):
    import backend.agents._wiki_io as _io
    from backend.agents._wiki_entities import create_supplier_stub

    monkeypatch.setattr(_io, "SUPPLIERS_DIR", tmp_path / "suppliers")
    path = create_supplier_stub("bmc-lumber")
    meta, _ = _io._parse_frontmatter(path)
    assert meta["total_materials"] == 0
    assert meta["materials_sourced"] == []


def test_ensure_supplier_stub_is_idempotent(tmp_path, monkeypatch):
    import backend.agents._wiki_io as _io
    from backend.agents._wiki_entities import create_supplier_stub, ensure_supplier_stub

    monkeypatch.setattr(_io, "SUPPLIERS_DIR", tmp_path / "suppliers")

    path1 = create_supplier_stub("bmc-lumber")
    first_mtime = path1.stat().st_mtime
    path2 = ensure_supplier_stub("bmc-lumber")
    assert path2 == path1
    assert path2.stat().st_mtime == first_mtime
```

- [ ] **Step 2: Run and verify they fail**

```bash
uv run pytest tests/test_wiki_manager.py -k "supplier_stub" -v
```

Expected: 3 FAIL on ImportError.

- [ ] **Step 3: Add `create_supplier_stub` and `ensure_supplier_stub` to `_wiki_entities.py`**

Append to `backend/agents/_wiki_entities.py`:

```python
def create_supplier_stub(
    supplier_id: str, materials: list[str] | None = None
) -> "Path | None":
    """
    Create a no-LLM supplier page skeleton. Returns the page path, or None if unsafe.
    Does nothing (and returns existing path) if the page already exists.
    """
    page_path = _io._safe_supplier_path(supplier_id)
    if page_path is None:
        return None
    if page_path.exists():
        return page_path

    materials = materials or []
    meta = {
        "supplier_id": supplier_id,
        "first_quote": date.today().isoformat(),
        "total_materials": len(materials),
        "active": True,
        "materials_sourced": materials,
        "avg_quote_deviation_pct": None,
        "tags": ["supplier", "active"],
    }
    body = (
        f"# {supplier_id}\n\n"
        "## Profile\n"
        "Supplier profile — enriched after sufficient quote history accumulates.\n\n"
        "## Materials Sourced\n"
        + ("\n".join(f"- [[materials/{m}]]" for m in materials) if materials else "None yet.\n")
        + "\n\n## Quote History\n"
        "No data yet.\n\n"
        "## Deviation Notes\n"
        "No data yet.\n\n"
        "## Jobs Referenced\n"
    )
    _io._write_page(page_path, meta, body)
    return page_path


def ensure_supplier_stub(
    supplier_id: str, materials: list[str] | None = None
) -> "Path | None":
    """Idempotent supplier stub creator."""
    page_path = _io._safe_supplier_path(supplier_id)
    if page_path is None:
        return None
    if page_path.exists():
        return page_path
    return create_supplier_stub(supplier_id, materials)
```

- [ ] **Step 4: Run the tests and verify they pass**

```bash
uv run pytest tests/test_wiki_manager.py -k "supplier_stub" -v
```

Expected: all 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/agents/_wiki_entities.py tests/test_wiki_manager.py
git commit -m "feat(wiki): add create_supplier_stub and ensure_supplier_stub"
```

---

## Task 7: Add `enrich_sub` with gating on `WIKI_ENRICH_MIN_LINKS`

**Files:**
- Modify: `backend/agents/_wiki_entities.py`
- Modify: `backend/config.py` (add env var if not present)
- Test: `tests/test_wiki_manager.py`

- [ ] **Step 1: Add `WIKI_ENRICH_MIN_LINKS` to config**

Read `backend/config.py` first:

```bash
grep -n "class Settings" backend/config.py
grep -n "wiki" backend/config.py
```

Then add to the `Settings` class (near other wiki settings):

```python
    WIKI_ENRICH_MIN_LINKS: int = 3
```

- [ ] **Step 2: Write failing tests**

Add to `tests/test_wiki_manager.py`:

```python
@pytest.mark.anyio
async def test_enrich_sub_below_threshold_does_not_call_llm(tmp_path, monkeypatch):
    import backend.agents._wiki_io as _io
    import backend.agents._wiki_llm as _llm
    from backend.agents._wiki_entities import create_sub_stub, enrich_sub

    monkeypatch.setattr(_io, "SUBS_DIR", tmp_path / "subs")
    mock_synthesize = AsyncMock(return_value="synthesized body")
    monkeypatch.setattr(_llm, "_synthesize", mock_synthesize)
    monkeypatch.setattr("backend.config.settings.WIKI_ENRICH_MIN_LINKS", 3)

    create_sub_stub("rivera-tile", "tile")
    # total_jobs is 0, below threshold
    result = await enrich_sub("rivera-tile")

    assert result is False
    mock_synthesize.assert_not_called()


@pytest.mark.anyio
async def test_enrich_sub_at_threshold_calls_llm(tmp_path, monkeypatch):
    import backend.agents._wiki_io as _io
    import backend.agents._wiki_llm as _llm
    from backend.agents._wiki_entities import create_sub_stub, enrich_sub

    monkeypatch.setattr(_io, "SUBS_DIR", tmp_path / "subs")
    mock_synthesize = AsyncMock(return_value="# rivera-tile\n\n## Profile\nEnriched narrative.\n")
    monkeypatch.setattr(_llm, "_synthesize", mock_synthesize)
    monkeypatch.setattr("backend.config.settings.WIKI_ENRICH_MIN_LINKS", 3)

    path = create_sub_stub("rivera-tile", "tile")
    # Manually bump total_jobs to hit the threshold
    meta, body = _io._parse_frontmatter(path)
    meta["total_jobs"] = 3
    _io._write_page(path, meta, body)

    result = await enrich_sub("rivera-tile")

    assert result is True
    mock_synthesize.assert_called_once()
    # Verify the synthesized body was written
    _, new_body = _io._parse_frontmatter(path)
    assert "Enriched narrative" in new_body


@pytest.mark.anyio
async def test_enrich_sub_missing_page_returns_false(tmp_path, monkeypatch):
    import backend.agents._wiki_io as _io
    from backend.agents._wiki_entities import enrich_sub

    monkeypatch.setattr(_io, "SUBS_DIR", tmp_path / "subs")
    result = await enrich_sub("nonexistent")
    assert result is False
```

- [ ] **Step 3: Run and verify they fail**

```bash
uv run pytest tests/test_wiki_manager.py -k "enrich_sub" -v
```

Expected: all 3 FAIL on ImportError.

- [ ] **Step 4: Add `enrich_sub` to `_wiki_entities.py`**

Append to `backend/agents/_wiki_entities.py`:

```python
async def enrich_sub(sub_id: str) -> bool:
    """
    Run LLM synthesis on a sub page, gated on WIKI_ENRICH_MIN_LINKS.
    Returns True if the page was enriched, False otherwise.
    """
    from backend.config import settings

    page_path = _io._safe_sub_path(sub_id)
    if page_path is None or not page_path.exists():
        return False

    meta, body = await asyncio.to_thread(_io.read_page, page_path)
    if meta.get("total_jobs", 0) < settings.WIKI_ENRICH_MIN_LINKS:
        return False

    updated_body = await _llm._synthesize(
        context=(
            f"Current sub page:\n{body}\n\n"
            f"Sub data: {json.dumps(meta, default=str)}"
        ),
        instruction=(
            "Rewrite this subcontractor wiki page body. Maintain the section order:\n"
            "- ## Profile (narrative summary)\n"
            "- ## Labor & Markup (observed rates, markup patterns)\n"
            "- ## Reliability Notes (variance history, incident notes)\n"
            "- ## Jobs Worked (wikilinks in [[jobs/slug]] form)\n"
            "- ## Patterns (observed tendencies, risk flags)\n"
            "Use [!success] callouts for low variance, [!caution] for medium, [!danger] for high. "
            "Do not include frontmatter."
        ),
    )

    await asyncio.to_thread(_io._write_page, page_path, meta, updated_body)
    return True
```

- [ ] **Step 5: Run the tests and verify they pass**

```bash
uv run pytest tests/test_wiki_manager.py -k "enrich_sub" -v
```

Expected: all 3 PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/agents/_wiki_entities.py backend/config.py tests/test_wiki_manager.py
git commit -m "feat(wiki): add enrich_sub with WIKI_ENRICH_MIN_LINKS gating"
```

---

## Task 8: Add `enrich_supplier` with gating

**Files:**
- Modify: `backend/agents/_wiki_entities.py`
- Test: `tests/test_wiki_manager.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_wiki_manager.py`:

```python
@pytest.mark.anyio
async def test_enrich_supplier_below_threshold_skips_llm(tmp_path, monkeypatch):
    import backend.agents._wiki_io as _io
    import backend.agents._wiki_llm as _llm
    from backend.agents._wiki_entities import create_supplier_stub, enrich_supplier

    monkeypatch.setattr(_io, "SUPPLIERS_DIR", tmp_path / "suppliers")
    mock_synthesize = AsyncMock(return_value="body")
    monkeypatch.setattr(_llm, "_synthesize", mock_synthesize)
    monkeypatch.setattr("backend.config.settings.WIKI_ENRICH_MIN_LINKS", 3)

    create_supplier_stub("bmc-lumber", materials=["2x4-stud"])
    result = await enrich_supplier("bmc-lumber")

    assert result is False
    mock_synthesize.assert_not_called()


@pytest.mark.anyio
async def test_enrich_supplier_at_threshold_calls_llm(tmp_path, monkeypatch):
    import backend.agents._wiki_io as _io
    import backend.agents._wiki_llm as _llm
    from backend.agents._wiki_entities import create_supplier_stub, enrich_supplier

    monkeypatch.setattr(_io, "SUPPLIERS_DIR", tmp_path / "suppliers")
    mock_synthesize = AsyncMock(return_value="# bmc-lumber\n\n## Profile\nEnriched.\n")
    monkeypatch.setattr(_llm, "_synthesize", mock_synthesize)
    monkeypatch.setattr("backend.config.settings.WIKI_ENRICH_MIN_LINKS", 3)

    path = create_supplier_stub(
        "bmc-lumber", materials=["2x4-stud", "osb-sheathing", "trim-base"]
    )

    result = await enrich_supplier("bmc-lumber")
    assert result is True
    mock_synthesize.assert_called_once()
```

- [ ] **Step 2: Run and verify they fail**

```bash
uv run pytest tests/test_wiki_manager.py -k "enrich_supplier" -v
```

Expected: FAIL on ImportError.

- [ ] **Step 3: Add `enrich_supplier` to `_wiki_entities.py`**

Append to `backend/agents/_wiki_entities.py`:

```python
async def enrich_supplier(supplier_id: str) -> bool:
    """
    Run LLM synthesis on a supplier page, gated on WIKI_ENRICH_MIN_LINKS.
    Threshold applies to total_materials.
    Returns True if enriched, False otherwise.
    """
    from backend.config import settings

    page_path = _io._safe_supplier_path(supplier_id)
    if page_path is None or not page_path.exists():
        return False

    meta, body = await asyncio.to_thread(_io.read_page, page_path)
    if meta.get("total_materials", 0) < settings.WIKI_ENRICH_MIN_LINKS:
        return False

    updated_body = await _llm._synthesize(
        context=(
            f"Current supplier page:\n{body}\n\n"
            f"Supplier data: {json.dumps(meta, default=str)}"
        ),
        instruction=(
            "Rewrite this supplier wiki page body. Maintain the section order:\n"
            "- ## Profile\n"
            "- ## Materials Sourced (wikilinks to [[materials/slug]])\n"
            "- ## Quote History (recent quotes, deviations)\n"
            "- ## Deviation Notes (flagged items)\n"
            "- ## Jobs Referenced (wikilinks to [[jobs/slug]])\n"
            "Use [!warning] callouts for deviation >= 10%, [!caution] for 5-9%. "
            "Do not include frontmatter."
        ),
    )

    await asyncio.to_thread(_io._write_page, page_path, meta, updated_body)
    return True
```

- [ ] **Step 4: Run the tests and verify they pass**

```bash
uv run pytest tests/test_wiki_manager.py -k "enrich_supplier" -v
```

Expected: both PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/agents/_wiki_entities.py tests/test_wiki_manager.py
git commit -m "feat(wiki): add enrich_supplier with WIKI_ENRICH_MIN_LINKS gating"
```

---

## Task 9: Re-export new writers through `wiki_manager.py` facade

**Files:**
- Modify: `backend/agents/wiki_manager.py`
- Test: `tests/test_wiki_manager.py`

- [ ] **Step 1: Write a failing test that imports from the facade**

Add to `tests/test_wiki_manager.py`:

```python
def test_wiki_manager_reexports_sub_writers():
    from backend.agents import wiki_manager as wm

    assert hasattr(wm, "create_sub_stub")
    assert hasattr(wm, "ensure_sub_stub")
    assert hasattr(wm, "enrich_sub")
    assert hasattr(wm, "create_supplier_stub")
    assert hasattr(wm, "ensure_supplier_stub")
    assert hasattr(wm, "enrich_supplier")
    assert hasattr(wm, "SUBS_DIR")
    assert hasattr(wm, "SUPPLIERS_DIR")
```

- [ ] **Step 2: Run the test and verify it fails**

```bash
uv run pytest tests/test_wiki_manager.py::test_wiki_manager_reexports_sub_writers -v
```

Expected: FAIL with `AssertionError`.

- [ ] **Step 3: Add re-exports to `wiki_manager.py`**

In `backend/agents/wiki_manager.py`, update the imports and `__all__`:

```python
from backend.agents._wiki_entities import (
    _seed_personality_page,
    create_sub_stub,
    create_supplier_stub,
    enrich_sub,
    enrich_supplier,
    ensure_sub_stub,
    ensure_supplier_stub,
    update_material_page,
)
from backend.agents._wiki_io import (
    CLIENTS_DIR,
    JOBS_DIR,
    MATERIALS_DIR,
    PERSONALITIES_DIR,
    SUBS_DIR,
    SUPPLIERS_DIR,
    WIKI_DIR,
    _make_job_slug,
    _parse_frontmatter,
    _write_page,
    read_page,
)
```

And extend `__all__`:

```python
__all__ = [
    # Public API
    "JOBS_DIR",
    "SUBS_DIR",
    "SUPPLIERS_DIR",
    "read_page",
    "create_job",
    "create_job_stub",
    "create_sub_stub",
    "ensure_sub_stub",
    "enrich_sub",
    "create_supplier_stub",
    "ensure_supplier_stub",
    "enrich_supplier",
    "enrich_estimate",
    "enrich_scope_from_blueprint",
    "enrich_tournament",
    "record_bid_decision",
    "cascade_outcome",
    "update_material_page",
    "lint",
    # Private symbols re-exported for tests
    "WIKI_DIR",
    "CLIENTS_DIR",
    "MATERIALS_DIR",
    "PERSONALITIES_DIR",
    "_parse_frontmatter",
    "_write_page",
    "_make_job_slug",
    "_synthesize",
    "_seed_personality_page",
]
```

- [ ] **Step 4: Run the test and verify it passes**

```bash
uv run pytest tests/test_wiki_manager.py::test_wiki_manager_reexports_sub_writers -v
```

Expected: PASS.

- [ ] **Step 5: Run the full wiki test file to confirm no regressions**

```bash
uv run pytest tests/test_wiki_manager.py -v
```

Expected: all tests PASS (both new and existing).

- [ ] **Step 6: Commit**

```bash
git add backend/agents/wiki_manager.py tests/test_wiki_manager.py
git commit -m "feat(wiki): re-export sub/supplier writers through wiki_manager facade"
```

---

# Phase 3 — Cascade wiring + lint + route

## Task 10: Add computation helpers for margin and delta

**Files:**
- Modify: `backend/agents/_wiki_jobs.py`
- Test: `tests/test_wiki_manager.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_wiki_manager.py`:

```python
def test_compute_margin_pct_revenue_based():
    from backend.agents._wiki_jobs import _compute_margin_pct

    # bid $100k, cost $80k → margin 20%
    assert _compute_margin_pct(our_bid=100000, estimate_total=80000) == 20.0
    # Rounded to 1 decimal
    assert _compute_margin_pct(our_bid=100000, estimate_total=82345) == 17.7


def test_compute_margin_pct_missing_inputs_returns_none():
    from backend.agents._wiki_jobs import _compute_margin_pct

    assert _compute_margin_pct(our_bid=None, estimate_total=80000) is None
    assert _compute_margin_pct(our_bid=100000, estimate_total=None) is None
    assert _compute_margin_pct(our_bid=0, estimate_total=80000) is None


def test_compute_delta_vs_actual_pct_positive():
    from backend.agents._wiki_jobs import _compute_delta_vs_actual_pct

    # bid $22k, actual $20k → +10% over
    assert _compute_delta_vs_actual_pct(our_bid=22000, actual_cost=20000) == 10.0
    # bid $18k, actual $20k → -10% under
    assert _compute_delta_vs_actual_pct(our_bid=18000, actual_cost=20000) == -10.0


def test_compute_delta_vs_actual_pct_missing_inputs():
    from backend.agents._wiki_jobs import _compute_delta_vs_actual_pct

    assert _compute_delta_vs_actual_pct(our_bid=None, actual_cost=20000) is None
    assert _compute_delta_vs_actual_pct(our_bid=22000, actual_cost=None) is None
    assert _compute_delta_vs_actual_pct(our_bid=22000, actual_cost=0) is None


def test_parse_sqft_from_scope_common_forms():
    from backend.agents._wiki_jobs import _parse_sqft_from_scope

    assert _parse_sqft_from_scope("Kitchen remodel, 120 sqft, replace cabinets") == 120
    assert _parse_sqft_from_scope("Build a 2400 sf garage") == 2400
    assert _parse_sqft_from_scope("1,800 square feet of hardwood floor") == 1800
    assert _parse_sqft_from_scope("small bathroom") is None
```

- [ ] **Step 2: Run and verify they fail**

```bash
uv run pytest tests/test_wiki_manager.py -k "compute_margin or compute_delta or parse_sqft" -v
```

Expected: FAIL on ImportError.

- [ ] **Step 3: Add the helpers to `_wiki_jobs.py`**

Add to `backend/agents/_wiki_jobs.py` (after the imports, before `create_job`):

```python
def _compute_margin_pct(
    our_bid: Optional[float], estimate_total: Optional[float]
) -> Optional[float]:
    """
    Revenue-based gross margin: (our_bid - estimate_total) / our_bid × 100.
    Returns None if either input is missing or our_bid is zero.
    """
    if our_bid is None or estimate_total is None or our_bid == 0:
        return None
    return round((our_bid - estimate_total) / our_bid * 100, 1)


def _compute_delta_vs_actual_pct(
    our_bid: Optional[float], actual_cost: Optional[float]
) -> Optional[float]:
    """
    Calibration signal: (our_bid - actual_cost) / actual_cost × 100.
    Positive = overbid, negative = underbid.
    Returns None if either input is missing or actual_cost is zero.
    """
    if our_bid is None or actual_cost is None or actual_cost == 0:
        return None
    return round((our_bid - actual_cost) / actual_cost * 100, 1)


_SQFT_PATTERNS = [
    re.compile(r"([\d,]+)\s*sqft", re.IGNORECASE),
    re.compile(r"([\d,]+)\s*sq\s*ft", re.IGNORECASE),
    re.compile(r"([\d,]+)\s*sf\b", re.IGNORECASE),
    re.compile(r"([\d,]+)\s*square\s*feet", re.IGNORECASE),
]


def _parse_sqft_from_scope(description: str) -> Optional[int]:
    """
    Regex-first sqft extraction. Returns None if no match.
    LLM fallback is intentionally not implemented here — the regex covers
    the common forms, and LLM fallback belongs in a higher-level enrich step.
    """
    if not description:
        return None
    for pattern in _SQFT_PATTERNS:
        match = pattern.search(description)
        if match:
            try:
                return int(match.group(1).replace(",", ""))
            except ValueError:
                continue
    return None
```

- [ ] **Step 4: Run the tests and verify they pass**

```bash
uv run pytest tests/test_wiki_manager.py -k "compute_margin or compute_delta or parse_sqft" -v
```

Expected: all 5 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/agents/_wiki_jobs.py tests/test_wiki_manager.py
git commit -m "feat(wiki): add margin/delta/sqft computation helpers for job frontmatter"
```

---

## Task 11: Extend `create_job` with sqft, market_segment, and candidate_subs

**Files:**
- Modify: `backend/agents/_wiki_jobs.py:20-75`
- Test: `tests/test_wiki_manager.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_wiki_manager.py`:

```python
@pytest.mark.anyio
async def test_create_job_parses_sqft_and_sets_market_segment(tmp_path, monkeypatch):
    import backend.agents._wiki_io as _io
    import backend.agents._wiki_llm as _llm
    from backend.agents._wiki_jobs import create_job

    monkeypatch.setattr(_io, "JOBS_DIR", tmp_path / "jobs")
    monkeypatch.setattr(_io, "CLIENTS_DIR", tmp_path / "clients")
    monkeypatch.setattr(_llm, "_synthesize", AsyncMock(return_value="body"))

    result = await create_job(
        client_id="smith",
        project_name="kitchen remodel",
        description="Kitchen remodel, 120 sqft, cabinets and quartz counters",
        zip_code="78701",
        trade_type="remodel",
        market_segment="residential",
    )

    page = _io.JOBS_DIR / f"{result['job_slug']}.md"
    meta, _ = _io._parse_frontmatter(page)
    assert meta["sqft"] == 120
    assert meta["market_segment"] == "residential"
    assert meta["candidate_subs"] == []


@pytest.mark.anyio
async def test_create_job_with_candidate_subs_creates_sub_stubs(tmp_path, monkeypatch):
    import backend.agents._wiki_io as _io
    import backend.agents._wiki_llm as _llm
    from backend.agents._wiki_jobs import create_job

    monkeypatch.setattr(_io, "JOBS_DIR", tmp_path / "jobs")
    monkeypatch.setattr(_io, "CLIENTS_DIR", tmp_path / "clients")
    monkeypatch.setattr(_io, "SUBS_DIR", tmp_path / "subs")
    monkeypatch.setattr(_llm, "_synthesize", AsyncMock(return_value="body"))

    result = await create_job(
        client_id="smith",
        project_name="bath remodel",
        description="Bathroom remodel 60 sqft",
        zip_code="78701",
        trade_type="remodel",
        candidate_subs=[("rivera-tile", "tile"), ("acme-plumbing", "plumbing")],
    )

    # Both sub stubs should exist
    assert (_io.SUBS_DIR / "rivera-tile.md").exists()
    assert (_io.SUBS_DIR / "acme-plumbing.md").exists()

    # Job page should list them
    page = _io.JOBS_DIR / f"{result['job_slug']}.md"
    meta, _ = _io._parse_frontmatter(page)
    assert meta["candidate_subs"] == ["rivera-tile", "acme-plumbing"]
```

- [ ] **Step 2: Run and verify they fail**

```bash
uv run pytest tests/test_wiki_manager.py -k "create_job_parses_sqft or create_job_with_candidate" -v
```

Expected: FAIL — new kwargs not accepted.

- [ ] **Step 3: Extend `create_job` signature and body**

Replace the `create_job` function in `backend/agents/_wiki_jobs.py`:

```python
async def create_job(
    client_id: str,
    project_name: str,
    description: str,
    zip_code: str,
    trade_type: str = "general",
    market_segment: str = "residential",
    candidate_subs: list[tuple[str, str]] | None = None,
) -> dict:
    """
    Create a new job wiki page at prospect status.
    Also creates the client page if it doesn't exist yet.

    candidate_subs: list of (sub_id, trade_specialty) tuples to pre-seed as stubs.
    Returns dict with job_slug and status.
    """
    today = date.today().isoformat()
    slug = _io._make_job_slug(client_id, project_name, today)
    page_path = _io.JOBS_DIR / f"{slug}.md"

    body = await _llm._synthesize(
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

    candidate_subs = candidate_subs or []
    candidate_sub_ids: list[str] = []
    for sub_id, specialty in candidate_subs:
        _ent.ensure_sub_stub(sub_id, specialty)
        candidate_sub_ids.append(sub_id)

    meta = {
        "status": "prospect",
        "client": client_id,
        "date": today,
        "trade": trade_type,
        "zip": zip_code,
        "market_segment": market_segment,
        "sqft": _parse_sqft_from_scope(description),
        "tags": ["job", "prospect", trade_type],
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
        "candidate_subs": candidate_sub_ids,
        "assigned_sub": None,
        "quoted_suppliers": [],
        "actual_supplier": None,
        "margin_pct": None,
        "delta_vs_actual_pct": None,
    }

    await asyncio.to_thread(_io._write_page, page_path, meta, body)
    _ent._ensure_client_page(client_id)

    return {"job_slug": slug, "status": "prospect"}
```

- [ ] **Step 4: Run the tests and verify they pass**

```bash
uv run pytest tests/test_wiki_manager.py -k "create_job_parses_sqft or create_job_with_candidate" -v
```

Expected: both PASS.

- [ ] **Step 5: Run the full wiki test file for regressions**

```bash
uv run pytest tests/test_wiki_manager.py -v
```

Expected: all tests PASS. Pay attention to any existing `create_job` tests that may need to accept the new default kwargs.

- [ ] **Step 6: Commit**

```bash
git add backend/agents/_wiki_jobs.py tests/test_wiki_manager.py
git commit -m "feat(wiki): extend create_job with sqft, market_segment, and candidate_subs"
```

---

## Task 12: Extend `enrich_tournament` with `quoted_suppliers`

**Files:**
- Modify: `backend/agents/_wiki_jobs.py` (the `enrich_tournament` function)
- Test: `tests/test_wiki_manager.py`

- [ ] **Step 1: Find and read the current `enrich_tournament` signature**

```bash
grep -n "async def enrich_tournament" backend/agents/_wiki_jobs.py
```

Read the function block (roughly lines 200-262).

- [ ] **Step 2: Write a failing test**

Add to `tests/test_wiki_manager.py`:

```python
@pytest.mark.anyio
async def test_enrich_tournament_creates_supplier_stubs(tmp_path, monkeypatch):
    import backend.agents._wiki_io as _io
    import backend.agents._wiki_llm as _llm
    from backend.agents._wiki_jobs import enrich_tournament

    monkeypatch.setattr(_io, "JOBS_DIR", tmp_path / "jobs")
    monkeypatch.setattr(_io, "SUPPLIERS_DIR", tmp_path / "suppliers")
    monkeypatch.setattr(_llm, "_synthesize", AsyncMock(return_value="tournament section"))

    # Seed a job page
    _io.JOBS_DIR.mkdir(parents=True, exist_ok=True)
    page_path = _io.JOBS_DIR / "2026-04-11-test-job.md"
    _io._write_page(
        page_path,
        {
            "status": "prospect",
            "client": "test",
            "date": "2026-04-11",
            "trade": "remodel",
            "zip": "78701",
            "tags": ["job", "prospect", "remodel"],
            "quoted_suppliers": [],
        },
        "# Test\n\n## Scope\nTest scope.\n\n## Links\n",
    )

    await enrich_tournament(
        job_slug="2026-04-11-test-job",
        tournament_data={"entries": [{"agent_name": "balanced", "total_bid": 25000}]},
        quoted_suppliers=["bmc-lumber", "home-depot-austin"],
    )

    # Supplier stubs exist
    assert (_io.SUPPLIERS_DIR / "bmc-lumber.md").exists()
    assert (_io.SUPPLIERS_DIR / "home-depot-austin.md").exists()

    # Job frontmatter updated
    meta, _ = _io._parse_frontmatter(page_path)
    assert meta["quoted_suppliers"] == ["bmc-lumber", "home-depot-austin"]
```

- [ ] **Step 3: Run and verify it fails**

```bash
uv run pytest tests/test_wiki_manager.py::test_enrich_tournament_creates_supplier_stubs -v
```

Expected: FAIL — unexpected kwarg `quoted_suppliers`.

- [ ] **Step 4: Update `enrich_tournament` signature and body**

In `backend/agents/_wiki_jobs.py`, locate `async def enrich_tournament(` and update the signature + body. Replace the existing signature line with:

```python
async def enrich_tournament(
    job_slug: str,
    tournament_data: dict,
    quoted_suppliers: list[str] | None = None,
) -> None:
```

Inside the existing try-block, before the line that calls `_llm._synthesize(...)` for the tournament section, add:

```python
    quoted_suppliers = quoted_suppliers or []
    for supplier_id in quoted_suppliers:
        _ent.ensure_supplier_stub(supplier_id)
    if quoted_suppliers:
        meta["quoted_suppliers"] = quoted_suppliers
```

This goes inside the `try:` block after `meta, body = await asyncio.to_thread(_io.read_page, page_path)` and before the winner-selection / synthesis logic.

- [ ] **Step 5: Run the test and verify it passes**

```bash
uv run pytest tests/test_wiki_manager.py::test_enrich_tournament_creates_supplier_stubs -v
```

Expected: PASS.

- [ ] **Step 6: Run full wiki test file**

```bash
uv run pytest tests/test_wiki_manager.py -v
```

Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/agents/_wiki_jobs.py tests/test_wiki_manager.py
git commit -m "feat(wiki): accept quoted_suppliers in enrich_tournament"
```

---

## Task 13: Extend `cascade_outcome` with sub/supplier assignment, margin, delta, and recompute

**Files:**
- Modify: `backend/agents/_wiki_jobs.py` (the `cascade_outcome` function)
- Test: `tests/test_wiki_manager.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_wiki_manager.py`:

```python
@pytest.mark.anyio
async def test_cascade_outcome_sets_margin_and_delta(tmp_path, monkeypatch):
    import backend.agents._wiki_io as _io
    import backend.agents._wiki_llm as _llm
    from backend.agents._wiki_jobs import cascade_outcome

    monkeypatch.setattr(_io, "JOBS_DIR", tmp_path / "jobs")
    monkeypatch.setattr(_io, "CLIENTS_DIR", tmp_path / "clients")
    monkeypatch.setattr(_io, "PERSONALITIES_DIR", tmp_path / "personalities")
    monkeypatch.setattr(_llm, "_synthesize", AsyncMock(return_value="outcome section"))

    _io.JOBS_DIR.mkdir(parents=True, exist_ok=True)
    page_path = _io.JOBS_DIR / "2026-04-11-x-job.md"
    _io._write_page(
        page_path,
        {
            "status": "bid-submitted",
            "client": "acme",
            "date": "2026-04-11",
            "trade": "remodel",
            "zip": "78701",
            "our_bid": 22000.0,
            "estimate_total": 18000.0,
            "tags": ["job", "bid-submitted", "remodel"],
        },
        "# Test\n",
    )

    await cascade_outcome(
        job_slug="2026-04-11-x-job",
        status="closed",
        actual_cost=20000.0,
    )

    meta, _ = _io._parse_frontmatter(page_path)
    assert meta["margin_pct"] == 18.2  # (22000-18000)/22000 = 18.18...
    assert meta["delta_vs_actual_pct"] == 10.0  # (22000-20000)/20000
    assert meta["status"] == "closed"


@pytest.mark.anyio
async def test_cascade_outcome_assigns_sub_and_supplier(tmp_path, monkeypatch):
    import backend.agents._wiki_io as _io
    import backend.agents._wiki_llm as _llm
    from backend.agents._wiki_jobs import cascade_outcome

    monkeypatch.setattr(_io, "JOBS_DIR", tmp_path / "jobs")
    monkeypatch.setattr(_io, "CLIENTS_DIR", tmp_path / "clients")
    monkeypatch.setattr(_io, "PERSONALITIES_DIR", tmp_path / "personalities")
    monkeypatch.setattr(_io, "SUBS_DIR", tmp_path / "subs")
    monkeypatch.setattr(_io, "SUPPLIERS_DIR", tmp_path / "suppliers")
    monkeypatch.setattr(_llm, "_synthesize", AsyncMock(return_value="body"))

    _io.JOBS_DIR.mkdir(parents=True, exist_ok=True)
    page_path = _io.JOBS_DIR / "2026-04-11-y-job.md"
    _io._write_page(
        page_path,
        {
            "status": "bid-submitted",
            "client": "acme",
            "date": "2026-04-11",
            "trade": "tile",
            "zip": "78701",
            "our_bid": 12000.0,
            "estimate_total": 10000.0,
            "tags": ["job", "bid-submitted", "tile"],
        },
        "# Test\n",
    )

    await cascade_outcome(
        job_slug="2026-04-11-y-job",
        status="won",
        actual_cost=11500.0,
        assigned_sub=("rivera-tile", "tile"),
        actual_supplier="bmc-lumber",
    )

    meta, _ = _io._parse_frontmatter(page_path)
    assert meta["assigned_sub"] == "rivera-tile"
    assert meta["actual_supplier"] == "bmc-lumber"
    assert (_io.SUBS_DIR / "rivera-tile.md").exists()
    assert (_io.SUPPLIERS_DIR / "bmc-lumber.md").exists()
```

- [ ] **Step 2: Run and verify they fail**

```bash
uv run pytest tests/test_wiki_manager.py -k "cascade_outcome_sets_margin or cascade_outcome_assigns" -v
```

Expected: FAIL — unexpected kwargs.

- [ ] **Step 3: Extend `cascade_outcome`**

In `backend/agents/_wiki_jobs.py`, replace the `cascade_outcome` function with:

```python
async def cascade_outcome(
    job_slug: str,
    status: str,
    actual_cost: Optional[float] = None,
    notes: str = "",
    assigned_sub: Optional[tuple[str, str]] = None,
    actual_supplier: Optional[str] = None,
) -> None:
    """
    Full cascade on outcome (won/lost/closed).
    Step 1 (job page) is required — raises on failure, cascade aborts.
    Steps 2–5 (client, personality, sub, supplier) are best-effort.

    assigned_sub: (sub_id, trade_specialty) tuple; stub created if missing.
    actual_supplier: supplier_id; stub created if missing.
    """
    page_path = _io._safe_job_path(job_slug)
    if page_path is None:
        return
    if not page_path.exists():
        logger.warning("cascade_outcome: job page %s not found", job_slug)
        return

    meta, body = await asyncio.to_thread(_io.read_page, page_path)

    # ── Step 1: Update job page ──────────────────────────────────────────
    meta["status"] = status
    meta["outcome_date"] = date.today().isoformat()
    if actual_cost is not None:
        meta["actual_cost"] = actual_cost

    margin = _compute_margin_pct(meta.get("our_bid"), meta.get("estimate_total"))
    if margin is not None:
        meta["margin_pct"] = margin

    delta = _compute_delta_vs_actual_pct(meta.get("our_bid"), actual_cost)
    if delta is not None:
        meta["delta_vs_actual_pct"] = delta

    if assigned_sub is not None:
        sub_id, _specialty = assigned_sub
        meta["assigned_sub"] = sub_id

    if actual_supplier is not None:
        meta["actual_supplier"] = actual_supplier

    context_data = {
        "status": status,
        "our_bid": meta.get("our_bid"),
        "actual_cost": actual_cost,
        "margin_pct": meta.get("margin_pct"),
        "delta_vs_actual_pct": meta.get("delta_vs_actual_pct"),
        "notes": notes,
    }
    section = await _llm._synthesize(
        context=f"Existing page:\n{body}\n\nOutcome data:\n{json.dumps(context_data, default=str)}",
        instruction=(
            f"Write or update the ## Outcome section for status={status}. Include:\n"
            "- The result (won/lost/closed)\n"
            "- If actual_cost is provided, analyze deviation from our_bid\n"
            "- Use [!success] callout if margin_pct is present and positive, "
            "[!danger] if delta_vs_actual_pct is negative (underbid)\n"
            "- Lessons learned or patterns observed\n"
            "Do not repeat earlier sections."
        ),
    )
    body = _io._append_section(body, section)
    await asyncio.to_thread(_io._write_page, page_path, meta, body)

    # ── Step 2: Update client page ───────────────────────────────────────
    client_id = meta.get("client")
    if client_id:
        try:
            await _ent._update_client_page_on_outcome(client_id, job_slug, meta, status)
        except Exception:
            logger.exception("cascade: failed to update client page for %s", client_id)

    # ── Step 3: Update personality pages ─────────────────────────────────
    personality = meta.get("winner_personality")
    if personality:
        try:
            await _ent._update_personality_page(personality, job_slug, meta, status)
        except Exception:
            logger.exception("cascade: failed to update personality page %s", personality)

    # ── Step 4: Ensure sub stub + enrich if over threshold ──────────────
    if assigned_sub is not None:
        sub_id, specialty = assigned_sub
        try:
            _ent.ensure_sub_stub(sub_id, specialty)
            await _recompute_sub_variance(sub_id)
            await _ent.enrich_sub(sub_id)
        except Exception:
            logger.exception("cascade: failed to update sub %s", sub_id)

    # ── Step 5: Ensure supplier stub + enrich if over threshold ─────────
    if actual_supplier is not None:
        try:
            _ent.ensure_supplier_stub(actual_supplier)
            await _ent.enrich_supplier(actual_supplier)
        except Exception:
            logger.exception("cascade: failed to update supplier %s", actual_supplier)
```

- [ ] **Step 4: Add `_recompute_sub_variance` helper**

Add to `backend/agents/_wiki_jobs.py` (next to the other `_compute_*` helpers near the top):

```python
async def _recompute_sub_variance(sub_id: str) -> None:
    """
    Scan all closed jobs with this assigned_sub, compute avg |delta_vs_actual_pct|,
    and write it back to the sub page's frontmatter along with total_jobs and reliability tag.
    Best-effort — logs and returns on any failure.
    """
    try:
        sub_path = _io._safe_sub_path(sub_id)
        if sub_path is None or not sub_path.exists():
            return

        deltas: list[float] = []
        jobs_worked: list[str] = []
        for job_file in _io.JOBS_DIR.glob("*.md"):
            job_meta, _ = _io._parse_frontmatter(job_file)
            if job_meta.get("assigned_sub") != sub_id:
                continue
            jobs_worked.append(job_file.stem)
            d = job_meta.get("delta_vs_actual_pct")
            if d is not None:
                deltas.append(abs(float(d)))

        if not jobs_worked:
            return

        avg_variance = round(sum(deltas) / len(deltas), 1) if deltas else None

        meta, body = _io._parse_frontmatter(sub_path)
        meta["total_jobs"] = len(jobs_worked)
        meta["avg_cost_variance_pct"] = avg_variance
        meta["last_job_date"] = date.today().isoformat()

        # Derive reliability tag (requires >= 3 jobs for statistical significance)
        tags = [t for t in meta.get("tags", []) if not t.startswith("reliability-") and t != "red-flag"]
        if len(jobs_worked) >= 3 and avg_variance is not None:
            if avg_variance <= 5:
                tags.append("reliability-high")
            elif avg_variance <= 15:
                tags.append("reliability-medium")
            else:
                tags.append("reliability-low")
                tags.append("red-flag")
        meta["tags"] = tags

        _io._write_page(sub_path, meta, body)
    except Exception:
        logger.exception("_recompute_sub_variance: failed for sub %s", sub_id)
```

- [ ] **Step 5: Run the tests and verify they pass**

```bash
uv run pytest tests/test_wiki_manager.py -k "cascade_outcome_sets_margin or cascade_outcome_assigns" -v
```

Expected: both PASS.

- [ ] **Step 6: Run full wiki test file**

```bash
uv run pytest tests/test_wiki_manager.py tests/test_wiki_routes.py -v
```

Expected: all PASS. If any existing `cascade_outcome` test breaks, it's because the new optional kwargs shifted the signature — existing tests should still work since all new params have defaults.

- [ ] **Step 7: Commit**

```bash
git add backend/agents/_wiki_jobs.py tests/test_wiki_manager.py
git commit -m "feat(wiki): cascade_outcome computes margin/delta and cascades to sub/supplier"
```

---

## Task 14: Add reliability-derivation test for `_recompute_sub_variance`

**Files:**
- Test: `tests/test_wiki_manager.py`

- [ ] **Step 1: Write a test that seeds multiple closed jobs and verifies reliability tag**

Add to `tests/test_wiki_manager.py`:

```python
@pytest.mark.anyio
async def test_recompute_sub_variance_derives_reliability_tag(tmp_path, monkeypatch):
    import backend.agents._wiki_io as _io
    from backend.agents._wiki_entities import create_sub_stub
    from backend.agents._wiki_jobs import _recompute_sub_variance

    monkeypatch.setattr(_io, "JOBS_DIR", tmp_path / "jobs")
    monkeypatch.setattr(_io, "SUBS_DIR", tmp_path / "subs")

    create_sub_stub("rivera-tile", "tile")

    # Seed three closed jobs assigned to rivera-tile with low variance
    _io.JOBS_DIR.mkdir(parents=True, exist_ok=True)
    for i, delta in enumerate([2.0, -3.5, 4.0]):
        path = _io.JOBS_DIR / f"2026-04-{10+i:02d}-j{i}.md"
        _io._write_page(
            path,
            {
                "status": "closed",
                "client": "x",
                "assigned_sub": "rivera-tile",
                "delta_vs_actual_pct": delta,
                "tags": ["job", "closed"],
            },
            "# t\n",
        )

    await _recompute_sub_variance("rivera-tile")

    sub_meta, _ = _io._parse_frontmatter(_io.SUBS_DIR / "rivera-tile.md")
    assert sub_meta["total_jobs"] == 3
    assert sub_meta["avg_cost_variance_pct"] == 3.2  # (2+3.5+4)/3 rounded
    assert "reliability-high" in sub_meta["tags"]
    assert "red-flag" not in sub_meta["tags"]


@pytest.mark.anyio
async def test_recompute_sub_variance_flags_high_variance_sub(tmp_path, monkeypatch):
    import backend.agents._wiki_io as _io
    from backend.agents._wiki_entities import create_sub_stub
    from backend.agents._wiki_jobs import _recompute_sub_variance

    monkeypatch.setattr(_io, "JOBS_DIR", tmp_path / "jobs")
    monkeypatch.setattr(_io, "SUBS_DIR", tmp_path / "subs")

    create_sub_stub("problem-sub", "framing")

    _io.JOBS_DIR.mkdir(parents=True, exist_ok=True)
    for i, delta in enumerate([18.0, -22.0, 25.0]):
        path = _io.JOBS_DIR / f"2026-04-{10+i:02d}-j{i}.md"
        _io._write_page(
            path,
            {
                "status": "closed",
                "client": "x",
                "assigned_sub": "problem-sub",
                "delta_vs_actual_pct": delta,
                "tags": ["job", "closed"],
            },
            "# t\n",
        )

    await _recompute_sub_variance("problem-sub")

    sub_meta, _ = _io._parse_frontmatter(_io.SUBS_DIR / "problem-sub.md")
    assert "reliability-low" in sub_meta["tags"]
    assert "red-flag" in sub_meta["tags"]
```

- [ ] **Step 2: Run and verify they pass**

```bash
uv run pytest tests/test_wiki_manager.py -k "recompute_sub_variance" -v
```

Expected: both PASS (implementation was already added in Task 13).

- [ ] **Step 3: Commit**

```bash
git add tests/test_wiki_manager.py
git commit -m "test(wiki): verify reliability tag derivation from variance history"
```

---

## Task 15: Add 4 new lint checks to `_wiki_lint.py`

**Files:**
- Modify: `backend/agents/_wiki_lint.py`
- Test: `tests/test_wiki_manager.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_wiki_manager.py`:

```python
def test_lint_flags_closed_job_missing_assigned_sub(tmp_path, monkeypatch):
    import backend.agents._wiki_io as _io
    from backend.agents._wiki_lint import lint

    monkeypatch.setattr(_io, "JOBS_DIR", tmp_path / "jobs")
    monkeypatch.setattr(_io, "CLIENTS_DIR", tmp_path / "clients")
    monkeypatch.setattr(_io, "MATERIALS_DIR", tmp_path / "materials")
    monkeypatch.setattr(_io, "PERSONALITIES_DIR", tmp_path / "personalities")
    monkeypatch.setattr(_io, "SUBS_DIR", tmp_path / "subs")
    monkeypatch.setattr(_io, "SUPPLIERS_DIR", tmp_path / "suppliers")

    _io.JOBS_DIR.mkdir(parents=True, exist_ok=True)
    _io._write_page(
        _io.JOBS_DIR / "closed-job.md",
        {
            "status": "closed",
            "client": "x",
            "date": "2026-04-01",
            "actual_cost": 20000,
            "tags": ["job", "closed"],
        },
        "# t\n",
    )

    report = lint()
    errors = [e for e in report["frontmatter_errors"] if e["page"] == "jobs/closed-job"]
    assert any("assigned_sub" in e["error"] for e in errors)
    assert any("margin_pct" in e["error"] for e in errors)


def test_lint_flags_sub_over_threshold_not_enriched(tmp_path, monkeypatch):
    import backend.agents._wiki_io as _io
    from backend.agents._wiki_lint import lint

    for attr in ["JOBS_DIR", "CLIENTS_DIR", "MATERIALS_DIR", "PERSONALITIES_DIR", "SUBS_DIR", "SUPPLIERS_DIR"]:
        monkeypatch.setattr(_io, attr, tmp_path / attr.lower().replace("_dir", ""))

    _io.SUBS_DIR.mkdir(parents=True, exist_ok=True)
    # Stub-only body (no enrichment markers)
    _io._write_page(
        _io.SUBS_DIR / "unenriched.md",
        {
            "sub_id": "unenriched",
            "trade_specialty": "tile",
            "total_jobs": 5,
            "first_job": "2026-01-01",
            "tags": ["sub", "tile"],
        },
        "# unenriched\n\n## Profile\nSubcontractor profile — enriched after 3+ jobs.\n",
    )

    report = lint()
    warnings = report.get("enrichment_pending", [])
    assert any(w["page"] == "subs/unenriched" for w in warnings)


def test_lint_flags_supplier_high_deviation_missing_tag(tmp_path, monkeypatch):
    import backend.agents._wiki_io as _io
    from backend.agents._wiki_lint import lint

    for attr in ["JOBS_DIR", "CLIENTS_DIR", "MATERIALS_DIR", "PERSONALITIES_DIR", "SUBS_DIR", "SUPPLIERS_DIR"]:
        monkeypatch.setattr(_io, attr, tmp_path / attr.lower().replace("_dir", ""))

    _io.SUPPLIERS_DIR.mkdir(parents=True, exist_ok=True)
    _io._write_page(
        _io.SUPPLIERS_DIR / "stale-tag.md",
        {
            "supplier_id": "stale-tag",
            "first_quote": "2026-01-01",
            "total_materials": 4,
            "active": True,
            "avg_quote_deviation_pct": 14.0,  # > 10 → should have price-flag
            "tags": ["supplier", "active"],  # missing price-flag
        },
        "# stale-tag\n",
    )

    report = lint()
    errors = [e for e in report["frontmatter_errors"] if e["page"] == "suppliers/stale-tag"]
    assert any("price-flag" in e["error"] for e in errors)
```

- [ ] **Step 2: Run and verify they fail**

```bash
uv run pytest tests/test_wiki_manager.py -k "lint_flags" -v
```

Expected: 3 FAIL — lint doesn't know about the new checks.

- [ ] **Step 3: Extend `_wiki_lint.py`**

Update `backend/agents/_wiki_lint.py`. At the top, add to `_REQUIRED_FRONTMATTER`:

```python
_REQUIRED_FRONTMATTER = {
    "jobs": ["status", "client"],
    "clients": ["client_id"],
    "personalities": ["personality"],
    "materials": ["material"],
    "subs": ["sub_id", "trade_specialty"],
    "suppliers": ["supplier_id", "active"],
}
```

Extend the directory scan loop to include subs + suppliers. Change line 39:

```python
for subdir in [
    _io.JOBS_DIR,
    _io.CLIENTS_DIR,
    _io.MATERIALS_DIR,
    _io.PERSONALITIES_DIR,
    _io.SUBS_DIR,
    _io.SUPPLIERS_DIR,
]:
```

Add new check blocks inside the `for rel, path in all_pages.items():` loop, after the existing page_type branches. Insert this block:

```python
        if page_type == "jobs":
            # ...existing job checks stay as-is...
            # NEW: closed/won jobs should carry assigned_sub + margin_pct
            if meta.get("status") in {"won", "closed"}:
                if not meta.get("assigned_sub"):
                    frontmatter_errors.append(
                        {"page": rel, "error": "missing assigned_sub on won/closed job"}
                    )
                if meta.get("margin_pct") is None:
                    frontmatter_errors.append(
                        {"page": rel, "error": "missing margin_pct on won/closed job"}
                    )

        if page_type == "suppliers":
            dev = meta.get("avg_quote_deviation_pct")
            tags = set(meta.get("tags", []) or [])
            if dev is not None and dev > 10 and "price-flag" not in tags:
                frontmatter_errors.append(
                    {"page": rel, "error": "missing price-flag tag for deviation > 10%"}
                )
```

Also add a new output field `enrichment_pending` and populate it after the main loop:

```python
    enrichment_pending = []
    try:
        from backend.config import settings
        threshold = settings.WIKI_ENRICH_MIN_LINKS
    except Exception:
        threshold = 3

    for rel, path in all_pages.items():
        page_type = path.parent.name
        if page_type not in ("subs", "suppliers"):
            continue
        meta, body = _io._parse_frontmatter(path)
        count_field = "total_jobs" if page_type == "subs" else "total_materials"
        if meta.get(count_field, 0) >= threshold:
            # Stub has a telltale phrase — if it's still there, page wasn't enriched
            if "enriched after" in body.lower():
                enrichment_pending.append({"page": rel, "threshold": threshold})
```

And update the returned report:

```python
    return {
        "orphan_pages": orphan_pages,
        "broken_links": broken_links,
        "stale_jobs": stale_jobs,
        "frontmatter_errors": frontmatter_errors,
        "enrichment_pending": enrichment_pending,
        "contradictions": [],
    }
```

- [ ] **Step 4: Run the tests and verify they pass**

```bash
uv run pytest tests/test_wiki_manager.py -k "lint_flags" -v
```

Expected: all 3 PASS.

- [ ] **Step 5: Run full lint test coverage**

```bash
uv run pytest tests/test_wiki_manager.py -k "lint" -v
```

Expected: all PASS including any pre-existing lint tests.

- [ ] **Step 6: Commit**

```bash
git add backend/agents/_wiki_lint.py tests/test_wiki_manager.py
git commit -m "feat(wiki): add lint checks for sub/supplier fields and enrichment gaps"
```

---

## Task 16: Extend `POST /api/job/update` to accept new body fields

**Files:**
- Modify: `backend/api/wiki_routes.py`
- Test: `tests/test_wiki_routes.py`

- [ ] **Step 1: Locate the update-job route**

```bash
grep -n "api/job/update\|def update_job" backend/api/wiki_routes.py
```

Read the surrounding function and its Pydantic model (if any).

- [ ] **Step 2: Write a failing HTTP-layer test**

Add to `tests/test_wiki_routes.py`:

```python
@pytest.mark.anyio
async def test_job_update_with_assigned_sub_creates_stub(client, tmp_path, monkeypatch):
    import backend.agents._wiki_io as _io
    import backend.agents._wiki_llm as _llm
    import backend.agents.wiki_manager as wm

    for attr in ["JOBS_DIR", "CLIENTS_DIR", "PERSONALITIES_DIR", "SUBS_DIR", "SUPPLIERS_DIR"]:
        monkeypatch.setattr(_io, attr, tmp_path / attr.lower().replace("_dir", ""))
    # HTTP-layer patching: must also patch wm
    monkeypatch.setattr(wm, "JOBS_DIR", tmp_path / "jobs")
    monkeypatch.setattr(wm, "SUBS_DIR", tmp_path / "subs")
    monkeypatch.setattr(wm, "SUPPLIERS_DIR", tmp_path / "suppliers")

    monkeypatch.setattr(_llm, "_synthesize", AsyncMock(return_value="body"))

    # Seed a bid-submitted job
    _io.JOBS_DIR.mkdir(parents=True, exist_ok=True)
    _io._write_page(
        _io.JOBS_DIR / "2026-04-11-acme-kitchen.md",
        {
            "status": "bid-submitted",
            "client": "acme",
            "date": "2026-04-11",
            "trade": "tile",
            "zip": "78701",
            "our_bid": 12000.0,
            "estimate_total": 10000.0,
            "tags": ["job", "bid-submitted", "tile"],
        },
        "# t\n",
    )

    resp = client.post(
        "/api/job/update",
        json={
            "job_slug": "2026-04-11-acme-kitchen",
            "status": "won",
            "actual_cost": 11500.0,
            "assigned_sub": {"sub_id": "rivera-tile", "trade_specialty": "tile"},
            "actual_supplier": "bmc-lumber",
        },
        headers={"X-API-Key": "test-key"},
    )

    assert resp.status_code == 200
    assert (_io.SUBS_DIR / "rivera-tile.md").exists()
    assert (_io.SUPPLIERS_DIR / "bmc-lumber.md").exists()

    job_meta, _ = _io._parse_frontmatter(_io.JOBS_DIR / "2026-04-11-acme-kitchen.md")
    assert job_meta["assigned_sub"] == "rivera-tile"
    assert job_meta["actual_supplier"] == "bmc-lumber"
```

Note: the `client` fixture comes from `conftest.py` — if the test file uses a different API-key header, match that convention.

- [ ] **Step 3: Run and verify it fails**

```bash
uv run pytest tests/test_wiki_routes.py::test_job_update_with_assigned_sub_creates_stub -v
```

Expected: FAIL — route doesn't accept `assigned_sub` / `actual_supplier`.

- [ ] **Step 4: Extend the Pydantic request model and route handler**

In `backend/api/wiki_routes.py`, find the request model for `POST /api/job/update`. Add these fields (using the existing model's style):

```python
class JobUpdateRequest(BaseModel):
    job_slug: str
    status: str
    actual_cost: Optional[float] = None
    notes: str = ""
    assigned_sub: Optional[dict] = None  # {"sub_id": str, "trade_specialty": str}
    actual_supplier: Optional[str] = None
    candidate_subs: Optional[list[dict]] = None
    quoted_suppliers: Optional[list[str]] = None
```

(If the existing model has a different name, preserve it and add the fields.)

In the route handler body, where `cascade_outcome(...)` is called, transform and forward:

```python
    assigned_sub_tuple = None
    if req.assigned_sub:
        assigned_sub_tuple = (
            req.assigned_sub["sub_id"],
            req.assigned_sub.get("trade_specialty", "general"),
        )

    await wm.cascade_outcome(
        job_slug=req.job_slug,
        status=req.status,
        actual_cost=req.actual_cost,
        notes=req.notes,
        assigned_sub=assigned_sub_tuple,
        actual_supplier=req.actual_supplier,
    )
```

- [ ] **Step 5: Run the test and verify it passes**

```bash
uv run pytest tests/test_wiki_routes.py::test_job_update_with_assigned_sub_creates_stub -v
```

Expected: PASS.

- [ ] **Step 6: Run the full route test file for regressions**

```bash
uv run pytest tests/test_wiki_routes.py -v
```

Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/api/wiki_routes.py tests/test_wiki_routes.py
git commit -m "feat(api): accept assigned_sub, actual_supplier on POST /api/job/update"
```

---

## Task 17: Final regression run + manual vault check

**Files:** none modified

- [ ] **Step 1: Run the full test suite**

```bash
uv run pytest -v
```

Expected: all tests PASS across `test_wiki_manager.py`, `test_wiki_routes.py`, `test_routes.py`, `test_tournament.py`, and the rest.

- [ ] **Step 2: Lint check against the live vault**

Start a Python REPL with the backend on the path:

```bash
uv run python -c "from backend.agents.wiki_manager import lint; import json; print(json.dumps(lint(), indent=2))"
```

Expected output should show:
- The smoketest job flagged with `missing assigned_sub on won/closed job` and `missing margin_pct on won/closed job` (validates the new lint rules against real data).
- Zero new `broken_links` or `frontmatter_errors` caused by the implementation.

- [ ] **Step 3: Manual Obsidian check**

Open `wiki/` in Obsidian. Verify:
- The `takeoffai-tags.css` snippet is toggled on.
- Tags on the existing personality pages render in the palette colors.
- `DASHBOARD.md` shows the four new Dataview blocks (empty for subs/suppliers until data lands; one row for the existing smoketest job in the Margin Leaderboard if its frontmatter is now populated).

- [ ] **Step 4: Final commit (if any stray changes from manual verification)**

```bash
git status
# If clean, skip. Otherwise:
git add -u
git commit -m "chore(wiki): final verification adjustments"
```

---

## Linting and formatting

After each phase, run the project linters:

```bash
uv run black backend tests
uv run isort backend tests
uv run flake8 backend tests
```

Fix any issues the linters report before moving to the next phase.

---

## Rollback strategy

Each phase is a sequence of independent commits. If a problem is discovered:

- **Phase 1 only:** `git revert` the 3 Phase 1 commits. The CSS snippet and dashboard additions are fully reversible — no data migration risk.
- **Phase 2 only:** revert the 6 Phase 2 commits. New writers have no runtime callers yet, so reverting is safe.
- **Phase 3:** revert commits 13-17 individually. The `cascade_outcome` extension is the only one that affects live cascades — if it causes an issue, revert Task 13's commit specifically and the route will still accept (but ignore) the new fields.
