# Wiki Enrichment and Color Coding — Design

**Date:** 2026-04-11
**Status:** Draft (awaiting review)
**Area:** `backend/agents/_wiki_*`, `wiki/SCHEMA.md`, `wiki/.obsidian/snippets/`

## Context

The TakeoffAI Obsidian vault at `wiki/` is managed exclusively through `backend/agents/wiki_manager.py` (a facade over five private sub-modules). Today it supports four page types — `jobs`, `clients`, `personalities`, `materials` — with `materials/` empty, one smoketest job, one smoketest client, and five personality pages. `SCHEMA.md` is injected into every LLM synthesis call via `_wiki_llm.py`.

Two gaps limit the vault's value:

1. **Thin forensic graph.** When `actual_cost` diverges from a bid, there is no way to trace *which sub* ran the labor or *which supplier* priced the materials. The calibration signal that would make personalities smarter about variance is missing.
2. **Flat visual surface.** Callouts exist in a few places, but there is no consistent color system across the vault. Jobs, subs, suppliers, and risk states all render identically in the graph view and the tag pane.

This spec enriches the data model (new page types + new job frontmatter) and establishes a semantic color system (callouts + tag CSS). Both changes are scoped to the wiki + backend — no frontend work.

## Goals

- Add two new page types — `wiki/subs/` and `wiki/suppliers/` — that link bidirectionally with jobs at both bid time and outcome time.
- Extend the job frontmatter with eight fields that enable calibration analysis and market-segment rollups.
- Establish a five-color semantic palette (green / yellow / amber / red / slate) applied consistently across callouts and tag CSS.
- Ship a CSS snippet at `wiki/.obsidian/snippets/takeoffai-tags.css` so existing pages gain color with zero backend changes.
- Match the existing stub-first + lazy-enrichment pattern so LLM token cost stays bounded.

## Non-goals

- **Frontend work.** No changes to `frontend/dist/`. A future spec may surface sub/supplier pickers.
- **Auto-inferring subs from scope text.** Caller passes `assigned_sub` explicitly via the HTTP layer. LLM extraction is deferred.
- **Back-populating `materials/`.** The empty folder stays empty in this spec. A separate spec can back-populate from `material_costs.csv`.
- **DataviewJS.** Plain Dataview is sufficient. Row/cell coloring via DataviewJS is deferred.
- **Per-vault theme overrides.** One global CSS snippet.

---

## Design

### 1. Schema additions (`wiki/SCHEMA.md`)

Two new page type definitions are added. Both follow the same pattern as existing types (required frontmatter, optional frontmatter, tag list, section order).

#### Sub (`wiki/subs/`)

Profile for a subcontractor or crew the contractor works with.

- **Required frontmatter:** `sub_id`, `trade_specialty`, `first_job`, `total_jobs`
- **Optional frontmatter:** `company`, `region`, `labor_rate_hourly`, `labor_rate_source` (`quoted`|`observed`|`inferred`), `typical_markup_pct`, `reliability` (`high`|`medium`|`low`|`unknown`), `last_job_date`, `avg_cost_variance_pct`
- **Tags:** `sub`, `{trade_specialty}`, `reliability-{high|medium|low}` (derived: `high` if `avg_cost_variance_pct <= 5`, `medium` if `5 < avg_cost_variance_pct <= 15`, `low` if `avg_cost_variance_pct > 15`; absent when `total_jobs < 3` for statistical significance), `red-flag` (if `avg_cost_variance_pct > 15`)
- **Section order:** Profile, Labor & Markup, Reliability Notes, Jobs Worked, Patterns

#### Supplier (`wiki/suppliers/`)

Profile for a materials vendor.

- **Required frontmatter:** `supplier_id`, `first_quote`, `total_materials`, `active`
- **Optional frontmatter:** `company`, `region`, `delivery_radius_mi`, `materials_sourced` (list of material wikilinks), `avg_quote_deviation_pct`, `last_quote_date`
- **Tags:** `supplier`, `active`|`inactive`, `price-flag` (if `avg_quote_deviation_pct > 10`)
- **Section order:** Profile, Materials Sourced, Quote History, Deviation Notes, Jobs Referenced

### 2. Job frontmatter additions

Eight new optional fields on `wiki/jobs/*.md`. All fields are optional at the frontmatter level so existing pages (the smoketest job) do not break; `_wiki_lint.py` emits warnings when won/closed jobs lack the fields that should be set.

| Field | Type | Written at | Purpose |
|---|---|---|---|
| `candidate_subs` | `list[str]` | `create_job` | Sub wikilinks considered pre-bid |
| `assigned_sub` | `str \| null` | `cascade_outcome` (won/closed) | Sub who actually ran the job |
| `quoted_suppliers` | `list[str]` | `enrich_tournament` | Suppliers whose quotes fed the estimate |
| `actual_supplier` | `str \| null` | `cascade_outcome` (closed) | Supplier on the realized invoice |
| `sqft` | `int \| null` | `create_job` (parsed from scope) | Enables $/sqft rollups |
| `margin_pct` | `float \| null` | `cascade_outcome` (won/closed) | `(our_bid - estimate_total) / our_bid` × 100, rounded to 1 decimal. Computed only when both fields are present and non-zero. |
| `delta_vs_actual_pct` | `float \| null` | `cascade_outcome` (closed) | `(our_bid - actual_cost) / actual_cost` × 100, rounded to 1 decimal. Requires both `our_bid` and `actual_cost`. |
| `market_segment` | `str` | `create_job` | `residential` \| `light_commercial` \| `commercial` |

### 3. Color conventions

#### Five-color semantic palette

| Color | Hex (accent / bg) | Meaning |
|---|---|---|
| GREEN | `#67c23a` / `#e1f5d8` | success, low risk, won, tight band, high reliability |
| YELLOW | `#f0c040` / `#fffbe6` | info, neutral, prospect, tournament running |
| AMBER | `#e6a23c` / `#ffebcc` | caution, price deviation 5–9%, medium risk, medium reliability |
| RED | `#d9363e` / `#fde3e3` | danger, underbid, price deviation ≥10%, lost, low reliability |
| SLATE | `#6272a4` / `#e8e8f5` | closed, inactive, archived, strategy/abstract |

Every callout type and every tag maps to exactly one of these five colors.

#### Callout taxonomy (added to `SCHEMA.md`)

| Callout | Color | When to use |
|---|---|---|
| `[!success]` | green | job won, margin ≥ target, band width < 15%, low-variance sub |
| `[!tip]` (existing) | green | risk assessment — high confidence, tight band |
| `[!info]` | yellow | prospect notes, neutral tournament commentary |
| `[!caution]` (existing) | amber | price deviation 5–9%, scope ambiguity, medium reliability |
| `[!warning]` (existing) | amber | price deviation ≥ 10% |
| `[!danger]` (existing) | red | underbid risk, low reliability, lost-with-gap |
| `[!abstract]` (existing) | slate | philosophy / strategy sections |
| `[!failure]` | slate | closed / archived / out-of-scope |

Three callouts are new: `success`, `info`, `failure`. The remaining five are already in `SCHEMA.md` and are now documented with their color mapping.

#### Tag families

Four orthogonal families; typical job page shows 3–4 tags total (one per family).

- **Status** (`won` / `lost` / `prospect` / `tournament-complete` / `closed`) → green / red / yellow / yellow / slate
- **Risk** (`red-flag` / `price-flag` / `reliability-high` / `reliability-medium` / `reliability-low`) → red / amber / green / amber / red
- **Type** (`job` / `client` / `personality` / `material` / `sub` / `supplier`) → blue / blue / blue / blue / teal / teal
- **Trade** (`remodel` / `electrical` / `plumbing` / `hvac` / `tile` / etc.) → purple (uniform across trades)

#### CSS snippet

Ships at `wiki/.obsidian/snippets/takeoffai-tags.css` (~40 lines). Selectors follow the pattern `.tag[href="#tag-name"] { background: ...; color: ...; }`. Toggled on once in Obsidian Appearance → CSS snippets. Checked into git so the vault works out-of-box on any machine.

### 4. Code changes

The existing five-file split in `backend/agents/_wiki_*.py` is preserved. No new sub-modules. Projected line counts after the change: `_wiki_entities.py` 217 → ~380, `_wiki_jobs.py` 357 → ~440, both within working range.

#### Additions to `_wiki_entities.py`

```python
def create_sub_stub(sub_id: str, trade_specialty: str) -> Path: ...
def enrich_sub(sub_id: str) -> Path: ...            # LLM, gated on total_jobs >= WIKI_ENRICH_MIN_LINKS
def ensure_sub_stub(sub_id: str, trade_specialty: str) -> Path: ...  # idempotent

def create_supplier_stub(supplier_id: str, materials: list[str] | None = None) -> Path: ...
def enrich_supplier(supplier_id: str) -> Path: ...  # LLM, gated on total_materials >= WIKI_ENRICH_MIN_LINKS
def ensure_supplier_stub(supplier_id: str, materials: list[str] | None = None) -> Path: ...
```

All six are re-exported through `wiki_manager.py`. `enrich_*` functions go through `_synthesize()` in `_wiki_llm.py`, matching `enrich_client` and `enrich_personality`.

#### Additions to `_wiki_io.py`

- New constants: `SUBS_DIR = WIKI_ROOT / "subs"`, `SUPPLIERS_DIR = WIKI_ROOT / "suppliers"`.
- New slug helpers: `sub_slug(sub_id)`, `supplier_slug(supplier_id)`.

#### Changes to `_wiki_jobs.py`

| Function | New behavior |
|---|---|
| `create_job` | Parse `sqft` and `market_segment` from scope text (regex-first: `r"(\d+)\s*sqft"` / `r"(\d+)\s*sf"`; LLM fallback via existing synthesis path if regex fails). Accept optional `candidate_subs: list[str]`; for each, call `ensure_sub_stub()` and link into frontmatter. |
| `enrich_tournament` | Accept optional `quoted_suppliers: list[str]`; for each, call `ensure_supplier_stub()` and link into frontmatter. |
| `cascade_outcome` | When `status in {won, closed}`: accept `assigned_sub`, `actual_supplier` kwargs; set on frontmatter; call `ensure_sub_stub` / `ensure_supplier_stub`; compute `margin_pct` from `our_bid` and `estimate_total`; compute `delta_vs_actual_pct` from `our_bid` and `actual_cost` (both best-effort, skip silently if inputs missing). After client + personality cascade, recompute `avg_cost_variance_pct` on the linked sub from its full jobs-worked history, then trigger `enrich_sub` / `enrich_supplier` when `total_jobs` / `total_materials` meet `WIKI_ENRICH_MIN_LINKS`. |

**Cascade order on won/closed:** `job → client → personality → sub → supplier`. Each step wrapped in its own `try/except` (best-effort pattern already in use).

#### Changes to `_wiki_lint.py`

Four new warning categories:

1. Won/closed job missing `assigned_sub`.
2. Won/closed job missing `margin_pct`.
3. Sub with `total_jobs >= WIKI_ENRICH_MIN_LINKS` but never enriched.
4. Supplier with `avg_quote_deviation_pct > 10` but missing `price-flag` tag (stale tag).

#### HTTP surface (`backend/api/wiki_routes.py`)

- `POST /api/job/update` gains four optional body fields: `assigned_sub`, `actual_supplier`, `candidate_subs`, `quoted_suppliers`. Route passes through to `cascade_outcome` / `create_job` / `enrich_tournament` depending on the update phase.
- **No new endpoints.** Sub/supplier creation is a side effect of job lifecycle updates.

#### New environment variable

- `WIKI_ENRICH_MIN_LINKS` (default `3`). Minimum `total_jobs` or `total_materials` before a stub is enriched via LLM synthesis. Controls the cost/coverage tradeoff.

### 5. Dashboard additions (`wiki/DASHBOARD.md`)

Four new Dataview query blocks appended to the existing four:

```dataview
TABLE trade_specialty AS "Trade", total_jobs AS "Jobs",
      labor_rate_hourly AS "Rate", avg_cost_variance_pct AS "Variance %",
      reliability
FROM "subs"
SORT avg_cost_variance_pct ASC
```

```dataview
TABLE total_materials AS "Materials", avg_quote_deviation_pct AS "Deviation %",
      last_quote_date AS "Last Quote", active
FROM "suppliers"
WHERE avg_quote_deviation_pct > 5
SORT avg_quote_deviation_pct DESC
```

```dataview
TABLE client, trade, our_bid AS "Bid", actual_cost AS "Actual", margin_pct AS "Margin %"
FROM "jobs"
WHERE status = "won" AND margin_pct != null
SORT margin_pct DESC
LIMIT 20
```

```dataview
TABLE date, client, trade, our_bid AS "Bid", actual_cost AS "Actual",
      delta_vs_actual_pct AS "Δ vs Actual %"
FROM "jobs"
WHERE delta_vs_actual_pct != null
SORT date DESC
LIMIT 20
```

The last block — the running calibration view — is the highest-signal addition.

---

## Rollout

Three independently-mergeable phases.

**Phase 1 — Schema + CSS (no code risk).**
1. Update `wiki/SCHEMA.md` with new page type sections, callout taxonomy, color palette documentation.
2. Add `wiki/.obsidian/snippets/takeoffai-tags.css` to the repo.
3. Add four new Dataview blocks to `wiki/DASHBOARD.md`.
4. Toggle snippet on in Obsidian → immediate visual lift on the 5 personality pages + 1 client + 1 job.

**Phase 2 — Writers + stubs (new code, no cascade changes).**
5. Add `SUBS_DIR`, `SUPPLIERS_DIR` constants and slug helpers to `_wiki_io.py`.
6. Add `create_sub_stub`, `create_supplier_stub`, `ensure_*`, `enrich_sub`, `enrich_supplier` to `_wiki_entities.py`.
7. Re-export new writers through `wiki_manager.py`.
8. Unit tests for the new writers (patching `_io` sub-module only).

**Phase 3 — Cascade wiring + lint (touches existing flows).**
9. Extend `create_job`, `enrich_tournament`, `cascade_outcome` with new fields and cascade hooks.
10. Extend `POST /api/job/update` body schema.
11. Add four new lint checks to `_wiki_lint.py`.
12. HTTP-layer tests with full patching pattern (`_io` + `wm`).

## Testing

All new tests follow the project's documented wiki patching pattern (`CLAUDE.md` → Testing → Wiki patching pattern).

- **Unit tests (Phase 2):** patch `_io.SUBS_DIR`, `_io.SUPPLIERS_DIR`, `_llm._anthropic`. Cover: stub writes have correct frontmatter and no LLM call, `enrich_*` gating fires at threshold and not before.
- **Integration tests (Phase 3):** patch `_io` *and* `wm` sub-module constants. Cover: `cascade_outcome` sets all four new fields, fires sub/supplier cascades after personality cascade, lint catches the four new warning categories.
- **End-to-end route test:** `POST /api/job/update` with `assigned_sub="rivera-tile"` creates `subs/rivera-tile.md` stub and links it from the job page.
- **Regression guard:** existing `test_wiki_manager.py` and `test_wiki_routes.py` suites must pass unmodified.

All LLM calls mocked. No real Anthropic API traffic during tests (per the existing project constraint).

## Success criteria

1. After Phase 1, opening `wiki/` in Obsidian with the snippet toggled on immediately shows colored tag pills on existing pages. Zero backend changes required to see first result.
2. After Phase 3, running a tournament → judging with `actual_winning_bid` → passing `assigned_sub="rivera-tile"` produces a new `wiki/subs/rivera-tile.md` stub automatically, with bidirectional links.
3. `_wiki_lint` flags the existing smoketest job (which has `status: closed` and no `assigned_sub` or `margin_pct`) as a migration reminder — validating that the lint rules fire on real data.
4. Existing test suites pass unmodified.
5. Dashboard "Delta vs Actual" view populates automatically as closed jobs accumulate.

## Risks and open questions

- **Regex-based `sqft` parsing is heuristic.** The scope text is free-form. A kitchen scope like "120 sqft" parses cleanly; a scope like "mid-sized kitchen, approximately 10x12" will miss. LLM fallback is documented but costs a token call. Acceptable for v1; deferred for a better parser in a follow-up.
- **`WIKI_ENRICH_MIN_LINKS=3` is a guess.** Too low burns tokens, too high leaves useful subs unenriched. Env-configurable so it's easy to tune without a redeploy.
- **Caller responsibility for `assigned_sub`.** This spec assumes the frontend (future) or API caller passes sub IDs explicitly. Without a frontend picker, Phase 3 is callable but not ergonomic. A follow-up spec should add the form surface.
- **Graph view color bleed.** Obsidian's graph view applies tag colors to the link label only, not the node. If we want colored nodes (not labels), we need a community plugin. For this spec, label-coloring is sufficient.

## References

- `backend/agents/wiki_manager.py` and the five `_wiki_*.py` sub-modules.
- `wiki/SCHEMA.md` — injected into every `_synthesize()` call.
- `TakeoffAI/CLAUDE.md` — Wiki system, Capture pipeline, Wiki patching pattern.
- Existing related specs: `2026-04-06-llm-wiki-job-tracking-design.md`.
