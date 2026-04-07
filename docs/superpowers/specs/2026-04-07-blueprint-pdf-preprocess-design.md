# Blueprint PDF Preprocessing — Design Spec

**Date:** 2026-04-07
**Status:** Approved

---

## Overview

Allow contractors to drop a blueprint PDF into the Pre-Bid Estimate tab and hit **Preprocess PDF** to have Claude read the plans and auto-fill a draft project description. The contractor reviews and edits the draft, then runs the estimate as normal. The existing estimate flow is completely unchanged — PDF preprocessing is an optional first step.

---

## Goals

1. Eliminate manual description writing for contractors who have plans in hand.
2. Generate consistent, estimate-ready draft descriptions from construction PDFs.
3. If a job is open in Job Tracking, persist the extracted draft to the wiki Scope section.
4. Stay within the existing architecture — no new dependencies, no new infrastructure.

---

## Scope

**In scope:**
- 1–5 page blueprint PDFs (floor plans, site plans, spec sheets, simple bid sets)
- Drop zone + Preprocess button in the estimate tab UI
- New backend endpoint and agent function
- Wiki Scope enrichment when a job slug is present

**Out of scope:**
- Full construction document sets (50+ pages)
- Storing the PDF file itself — only the extracted draft text is persisted (and only when tied to a job)
- OCR or page rendering — Claude's native document API handles this
- Automatic estimate trigger — contractor always reviews/edits before running

---

## Architecture

### New Endpoint

**`POST /api/estimate/preprocess-pdf`** — multipart form

| Field | Type | Required | Notes |
|---|---|---|---|
| `pdf` | file | Yes | PDF only; max 32MB |
| `zip_code` | string | Yes | Passed to extraction prompt for regional context |
| `trade_type` | string | Yes | Focuses extraction on relevant scope elements |
| `job_slug` | string | No | If present, wiki Scope section is updated |

**Response:**
```json
{ "draft": "18,000 sqft 3-story commercial office building..." }
```

**Errors:**
- `400` — wrong file type (not PDF) or file exceeds 32MB
- `422` — missing required fields
- `500` — Claude API failure (wrapped, non-fatal message returned)

Auth: `X-API-Key`. Rate limit: 10/minute.

---

### New Agent Function

**`preprocess_blueprint(pdf_bytes: bytes, zip_code: str, trade_type: str) -> str`**
in `backend/agents/pre_bid_calc.py`

Sends the PDF to Claude using the document content block format:

```python
response = client.messages.create(
    model=settings.claude_model,
    max_tokens=1024,
    system=BLUEPRINT_EXTRACTION_SYSTEM,
    messages=[{
        "role": "user",
        "content": [
            {
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": "application/pdf",
                    "data": base64.standard_b64encode(pdf_bytes).decode("utf-8"),
                },
            },
            {
                "type": "text",
                "text": BLUEPRINT_EXTRACTION_PROMPT.format(
                    zip_code=zip_code,
                    trade_type=trade_type,
                ),
            },
        ],
    }],
)
return response.content[0].text.strip()
```

---

### Extraction Prompt Template

Two module-level constants defined in `pre_bid_calc.py`, same pattern as `SYSTEM_PROMPT`:

```python
BLUEPRINT_EXTRACTION_SYSTEM = """You are a construction takeoff assistant for TakeoffAI by answerd.it.
Your job is to read construction plans and extract a plain-English project description
that a cost estimator can use to generate a line-item bid estimate.

Write in the voice of an experienced contractor describing the job to their estimator.
Be specific and quantitative. Lead with the single most important number (total sqft, CY, LF, etc.).
Do not include contract terms, owner names, bid dates, or submission requirements.
Do not use adjectives without numbers behind them."""

BLUEPRINT_EXTRACTION_PROMPT = """Review these construction plans and extract a project description
for a {trade_type} estimate. The project is located in zip code {zip_code}.

Structure your response in this order:
1. Total size (sqft, CY, LF, or units — whichever is primary for this trade)
2. Building type and occupancy
3. Structural system and key materials called out in the plans
4. Scope of work — what is being built or installed
5. Room counts or system counts (fixtures, panels, openings, etc.)
6. Site conditions or access constraints visible in the plans
7. Explicit exclusions noted in the plans or specs

Write as a single paragraph or short bulleted list. Keep it under 200 words.
If a dimension or quantity is not clearly shown in the plans, omit it rather than guess."""
```

The structured output order ensures the draft description always leads with what PreBidCalc needs most (size) and flows into the detail the estimate engine uses for line-item parsing.

---

### Wiki Integration

New function in `backend/agents/wiki_manager.py`:

**`enrich_scope_from_blueprint(job_slug: str, draft_text: str) -> None`**

- Reads the existing job page
- Overwrites the `## Scope` section body with the blueprint-extracted draft
- Updates frontmatter `status` to `prospect` (no change if already advanced)
- Wrapped in `try/except Exception` — fire-and-forget, same pattern as all other wiki hooks
- Called from the endpoint via `asyncio.ensure_future()` if `job_slug` is present

---

## Frontend Changes

**Estimate tab — above the description textarea:**

```
┌─────────────────────────────────────────────────┐
│  Drop blueprint PDF here, or click to browse     │
│  Supports PDF up to 32MB · 1–5 pages works best  │
└─────────────────────────────────────────────────┘
[ Preprocess PDF ]   ← appears only when file is selected
```

- Drop zone: `<input type="file" accept=".pdf">` styled as dashed drop target
- File name shown after selection; drop zone border turns amber on hover/drag
- **Preprocess PDF** button: disabled + spinner while processing
- On success: description textarea fills with draft; amber notice appears below the field: *"Draft extracted from blueprint — review and edit before estimating"*
- On error: inline error on the drop zone (file too large, wrong type, API failure)
- `job_slug` sent silently if present in `sessionStorage` — contractor never sees this field

---

## Files Changed

| File | Change |
|---|---|
| `backend/agents/pre_bid_calc.py` | Add `BLUEPRINT_EXTRACTION_SYSTEM`, `BLUEPRINT_EXTRACTION_PROMPT`, `preprocess_blueprint()` |
| `backend/api/routes.py` | Add `POST /api/estimate/preprocess-pdf` multipart endpoint |
| `backend/agents/wiki_manager.py` | Add `enrich_scope_from_blueprint()` |
| `frontend/dist/index.html` | PDF drop zone + preprocess button + draft notice in estimate tab |
| `tests/test_routes.py` | Tests for new endpoint |

No new dependencies. Claude's document API is available via the existing `anthropic` package (`>=0.40.0` already declared).

---

## Error Handling

| Scenario | Behavior |
|---|---|
| File is not a PDF | `400` — "Only PDF files are accepted" |
| File exceeds 32MB | `400` — "File too large. Maximum size is 32MB." |
| Claude returns empty response | Return `{ "draft": "" }` — frontend shows empty field, contractor writes manually |
| Claude API error | `500` with message — frontend shows inline error on drop zone |
| Wiki write fails | Logged, silently ignored — draft still returned to frontend |

---

## Out of Scope

- Storing the PDF file itself
- Multi-file upload (one PDF per preprocess call)
- Automatic estimate trigger after preprocessing
- Page count enforcement beyond the 32MB limit (Claude handles gracefully)
- Changes to the tournament or bid strategy tabs
