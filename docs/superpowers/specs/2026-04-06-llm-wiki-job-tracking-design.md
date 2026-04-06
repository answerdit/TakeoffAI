# LLM Wiki + Job Tracking — Design Spec

**Date:** 2026-04-06
**Status:** Approved

---

## Overview

Add a persistent, LLM-maintained markdown knowledge base (`wiki/`) to TakeoffAI. The wiki serves as a narrative layer on top of the existing structured data (JSON client profiles, SQLite, CSV seed costs). It tracks jobs through a full bid pipeline lifecycle, maintains cross-referenced client and personality pages, and compounds institutional knowledge with every tournament and outcome.

The wiki is viewed locally via Obsidian (pointed at the `wiki/` folder). A web-based viewer is deferred to the hosted version roadmap.

---

## Goals

1. **Job tracking** — Track every project from prospect through closed with a 6-status pipeline, each status transition enriching the wiki page with LLM-synthesized narrative.
2. **Compounding knowledge** — Tournament results, win/loss outcomes, price deviations, and personality performance accumulate across pages. The more you use TakeoffAI, the richer the wiki becomes.
3. **Better HarnessEvolver inputs** — Personality pages with narrative win/loss histories give the evolver richer context than raw trace JSON.
4. **Human-readable history** — Obsidian provides graph view, backlinks, search, and Dataview queries across all jobs, clients, and materials.

---

## Architecture

Single new agent file: `backend/agents/wiki_manager.py`. All wiki operations go through this file. No other code writes to the `wiki/` directory.

### wiki_manager.py Public Functions

| Function | Trigger | Pages Touched |
|---|---|---|
| `create_job()` | `POST /api/job/create` | Creates job page (prospect) |
| `enrich_estimate()` | `POST /api/estimate` (after response) | Updates job page (estimated) |
| `enrich_tournament()` | `POST /api/tournament/run` (after response) | Updates job page (tournament-complete) |
| `record_bid_decision()` | `POST /api/job/update` with status=bid-submitted | Updates job page (bid-submitted) |
| `cascade_outcome()` | `POST /api/job/update` with status=won/lost/closed | Updates job + client + personality + material pages |
| `update_material_page()` | `price_verifier.verify_line_items()` (after audit) | Creates/updates material page |
| `lint()` | `GET /api/wiki/lint` or scheduler | Reads all pages, returns report |

### Internal: `_synthesize()`

All page content flows through a single internal function that makes one Claude API call:

```python
WIKI_MODEL = os.getenv("WIKI_MODEL", "claude-haiku-4-5")

async def _synthesize(
    system: str,
    context: str,
    instruction: str,
) -> str:
    """Single LLM call to generate wiki page content.
    Returns markdown string (body only, no frontmatter)."""
```

- `system`: role prompt + SCHEMA.md conventions (loaded once, cached)
- `context`: existing page content + related pages + new data
- `instruction`: what to write/update ("Write the Tournament section for this job")
- Model controlled by `WIKI_MODEL` environment variable, defaults to `claude-haiku-4-5`
- Frontmatter is always written by Python, never by the LLM

---

## Wiki Directory Structure

```
wiki/
├── SCHEMA.md                              # conventions doc
├── DASHBOARD.md                           # Dataview-powered overview
├── jobs/
│   └── 2026-04-06-acme-parking-garage.md
├── clients/
│   └── acme-construction.md
├── materials/
│   └── concrete-q1-2026.md
└── personalities/
    ├── conservative.md
    ├── balanced.md
    ├── aggressive.md
    ├── historical-match.md
    └── market-beater.md
```

- `wiki/` lives at project root, git-tracked
- Obsidian opens this folder as a vault
- All cross-references use `[[folder/page-slug]]` wikilink syntax
- YAML frontmatter on every page for Dataview queries

---

## Job Page Structure

### Frontmatter

```yaml
---
status: prospect | estimated | tournament-complete | bid-submitted | won | lost | closed
client: acme-construction
date: 2026-04-06
trade: concrete
zip: 78701
our_bid: null
estimate_total: null
estimate_low: null
estimate_high: null
tournament_id: null
winner_personality: null
band_low: null
band_high: null
actual_cost: null
outcome_date: null
---
```

All monetary values stored as raw numbers (no `$`). Dates in ISO 8601.

### Body Sections (LLM-synthesized, progressively appended)

```markdown
# Acme Parking Garage — Downtown Austin

## Scope
[Written at prospect. LLM summary of the project description.]

## Estimate
[Written at estimated. Narrative of PreBidCalc result — key line items,
cost concentration, confidence, range interpretation.]

## Tournament
[Written at tournament-complete. Analysis of all agent bids — who bid what,
agreement/divergence, band interpretation. Links to personality pages.]

## Bid Decision
[Written at bid-submitted. Which number, why, risk assessment.]

## Outcome
[Written at won/lost/closed. Result, deviation analysis, lessons learned.]

## Price Flags
[Appended by PriceVerifier if any line items flagged. Links to material pages.]

## Links
- Client: [[clients/acme-construction]]
- Materials: [[materials/concrete-q1-2026]]
```

### Status Transitions

| Transition | Trigger | Wiki Action |
|---|---|---|
| (new) → `prospect` | `POST /api/job/create` | Create job page with Scope section |
| `prospect` → `estimated` | `POST /api/estimate` | Append Estimate section |
| `estimated` → `tournament-complete` | `POST /api/tournament/run` | Append Tournament section |
| `tournament-complete` → `bid-submitted` | `POST /api/job/update` | Append Bid Decision section |
| `bid-submitted` → `won`/`lost` | `POST /api/job/update` | Append Outcome, trigger cascade |
| `won`/`lost` → `closed` | `POST /api/job/update` (with actual_cost) | Append cost analysis, trigger full cascade |

---

## Cascade Logic

When `cascade_outcome()` fires (on won/lost/closed):

**Step 1 — Update job page.** Set frontmatter status. LLM writes the Outcome section. If `closed` with actual_cost: LLM writes cost deviation analysis.

**Step 2 — Update client page.** Read current client wiki page + updated job page + client JSON profile. LLM rewrites client page narrative: updated win/loss record, pattern analysis, ELO interpretation. Append job link to Recent Jobs.

**Step 3 — Update personality pages.** For each personality that participated in the tournament: read current personality page + job tournament data + outcome. LLM appends a performance note. If personality page doesn't exist, create it from `PERSONALITY_PROMPTS` seed text.

**Step 4 — Update material pages (conditional).** Only if PriceVerifier flagged deviations on this job's line items. Read flagged material pages, append outcome context.

**LLM call count per cascade:** 2-5 calls (1 job + 1 client + 1-3 personalities + 0-1 materials). All using `WIKI_MODEL`. Roughly $0.005-0.01 total at Haiku pricing.

**Failure handling:** If any single page write fails, log it and continue to the next page. The cascade is best-effort. `lint()` catches inconsistencies later.

**Execution:** All wiki calls are fire-and-forget async — they run after the HTTP response is sent. The core API is never blocked or slowed by wiki operations.

---

## Client Page Structure

Created on first job for a client. Updated on every cascade.

```yaml
---
client_id: acme-construction
company: Acme Construction LLC
region: Central Texas
first_job: 2026-04-06
total_jobs: 5
wins: 3
losses: 2
---
```

Body contains LLM-synthesized sections:
- **Profile** — company info, region, trade specialties
- **Win/Loss Summary** — narrative record with pattern analysis
- **ELO Standings** — interpretation of agent ELO scores for this client
- **Recent Jobs** — links to job pages, most recent first
- **Patterns** — LLM-written analysis ("Acme wins most often with Balanced on commercial remodels, but switches to Aggressive for metal buildings")

---

## Personality Page Structure

Seeded once from `PERSONALITY_PROMPTS` in tournament.py. Updated on every cascade_outcome.

```yaml
---
personality: market-beater
current_prompt_hash: abc123
total_tournaments: 42
wins: 18
win_rate: 0.4286
last_evolution: 2026-04-01
---
```

Body contains:
- **Philosophy** — current prompt text and strategy description
- **Performance** — win rate trends, strengths by trade type, regional patterns
- **Recent Results** — short notes per job outcome, linked to job pages
- **Evolution History** — linked to HarnessEvolver git commits

---

## Material Page Structure

Created when PriceVerifier flags a deviation. Updated on subsequent flags or job outcomes.

```yaml
---
material: concrete
category: structural
last_verified: 2026-04-06
seed_low: 4.50
seed_high: 6.75
verified_mid: 5.80
deviation_pct: 8.2
---
```

Body contains:
- **Current Pricing** — verified price range, source count, last check date
- **Deviation History** — trend of AI vs verified prices over time
- **Job Impact** — links to jobs where this material was flagged, with outcome context

---

## New API Endpoints

### `POST /api/job/create`

```json
{
  "client_id": "acme-construction",
  "project_name": "Parking Garage — Downtown Austin",
  "description": "3-level precast parking structure, 420 spaces...",
  "zip_code": "78701",
  "trade_type": "concrete"
}
```

Returns: `{ "job_slug": "2026-04-06-acme-parking-garage", "status": "prospect" }`

Auth: `X-API-Key`. Rate limit: `10/minute`.

### `POST /api/job/update`

```json
{
  "job_slug": "2026-04-06-acme-parking-garage",
  "status": "bid-submitted",
  "our_bid": 159880,
  "notes": "Went with Balanced consensus"
}
```

For `won`/`lost`: requires no additional fields (notes optional).
For `closed`: requires `actual_cost` field.

Returns: updated frontmatter as JSON.

Auth: `X-API-Key`. Rate limit: `10/minute`.

### `GET /api/job/{slug}`

Returns the job's current frontmatter as JSON. The full markdown is for Obsidian.

Auth: `X-API-Key`.

### `GET /api/jobs`

Query parameter: `?status=active` (optional filter). `active` = everything except `closed` and `lost`.

Returns: list of frontmatter dicts from all job pages.

Auth: `X-API-Key`.

### `GET /api/wiki/lint`

Runs wiki health check. Returns structured report:

```json
{
  "orphan_pages": ["materials/lumber-q4-2025.md"],
  "broken_links": [{"page": "jobs/...", "link": "clients/deleted-client"}],
  "stale_jobs": [{"slug": "...", "status": "estimated", "days_stale": 45}],
  "frontmatter_errors": [],
  "contradictions": []
}
```

Auth: `X-API-Key`.

---

## SCHEMA.md

Lives at `wiki/SCHEMA.md`. Injected into `_synthesize()` system prompt. Defines:

- **Page types:** job, client, material, personality — required frontmatter fields and section ordering per type
- **Frontmatter rules:** status enum values, date format (ISO 8601), monetary values as raw numbers
- **Link conventions:** always include folder prefix (`[[clients/slug]]` not `[[slug]]`)
- **Naming:** job slugs are `YYYY-MM-DD-{client}-{short-description}`, kebab-case
- **Section ordering:** Scope always first, Links always last, chronological sections in between

---

## DASHBOARD.md

Obsidian Dataview-powered overview page:

```markdown
# TakeoffAI Dashboard

## Active Jobs
\```dataview
TABLE status, client, trade, our_bid
FROM "jobs"
WHERE status != "closed" AND status != "lost"
SORT date DESC
\```

## Recent Outcomes
\```dataview
TABLE status, client, our_bid, actual_cost
FROM "jobs"
WHERE status = "won" OR status = "lost" OR status = "closed"
SORT outcome_date DESC
LIMIT 10
\```

## Price Flags
\```dataview
TABLE material, deviation_pct, last_verified
FROM "materials"
WHERE deviation_pct > 5
SORT deviation_pct DESC
\```

## Personality Standings
\```dataview
TABLE wins, win_rate, last_evolution
FROM "personalities"
SORT win_rate DESC
\```
```

---

## Lint Checks

`wiki_manager.lint()` performs these checks:

1. **Orphan check** — pages with no inbound `[[wikilinks]]` from any other page
2. **Broken link check** — `[[wikilinks]]` pointing to pages that don't exist
3. **Stale job check** — jobs stuck in `estimated` or `tournament-complete` for >30 days
4. **Frontmatter validation** — required fields present, status is valid enum, dates parse
5. **Contradiction scan** — LLM call: feed client page + last 5 job pages, ask if claims contradict

Lint reports only — no auto-fixing. Returns structured dict.

---

## Integration Points

| Existing Code | Change |
|---|---|
| `routes.py` | Add job create/update/get/list and wiki lint endpoints |
| `routes.py: estimate endpoint` | Add optional `job_slug` field to request body. When present, fire-and-forget `wiki_manager.enrich_estimate()` after response. When absent, no wiki action (backwards compatible). |
| `routes.py: tournament/run endpoint` | Add optional `job_slug` field to request body. When present, fire-and-forget `wiki_manager.enrich_tournament()` after response. When absent, no wiki action (backwards compatible). |
| `feedback_loop.update_client_profile()` | No change — wiki_manager reads JSON profiles but doesn't modify them |
| `price_verifier.verify_line_items()` | After audit records written, fire-and-forget `wiki_manager.update_material_page()` for any flagged items |

The existing JSON profiles, SQLite tables, and CSV seed data are unchanged. The wiki augments — it does not replace.

**Backwards compatibility:** The `job_slug` field on estimate and tournament endpoints is optional. Existing API consumers that don't send it get identical behavior to today — no wiki pages are created or updated. The wiki enrichment only fires when a job has been explicitly created via `POST /api/job/create` first and the slug is passed through.

---

## Configuration

| Variable | Default | Description |
|---|---|---|
| `WIKI_MODEL` | `claude-haiku-4-5` | Model used for all wiki LLM synthesis calls |

Added to `.env.template` and documented.

---

## Files Changed / Created

| File | Change |
|---|---|
| `backend/agents/wiki_manager.py` | **Create** — all wiki operations |
| `backend/api/routes.py` | **Modify** — add job and wiki lint endpoints, add fire-and-forget hooks to estimate and tournament endpoints |
| `wiki/SCHEMA.md` | **Create** — conventions doc |
| `wiki/DASHBOARD.md` | **Create** — Dataview dashboard |
| `wiki/personalities/*.md` | **Create** — 5 seeded personality pages from PERSONALITY_PROMPTS |
| `wiki/.obsidian/` | **Create** — minimal Obsidian vault config (optional) |
| `.env.template` | **Modify** — add WIKI_MODEL |
| `.gitignore` | **Verify** — ensure `wiki/` is NOT ignored (it should be tracked) |
| `tests/test_wiki_manager.py` | **Create** — unit tests for wiki_manager |

---

## Out of Scope

- Frontend changes (no new tabs or UI for job tracking)
- Web-based wiki viewer (deferred to hosted version)
- Auto-fixing in lint (report only)
- Changes to existing JSON profiles, SQLite schema, or CSV structure
- Scheduler integration for lint (manual/API-triggered only for now)
