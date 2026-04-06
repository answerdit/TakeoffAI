# Confidence Bands — Design Spec

**Issue:** #35  
**Date:** 2026-04-06  
**Status:** Approved

---

## Overview

Surface cost uncertainty visually in two places: a dollar range row on the Pre-Bid Estimate totals block, and a new Tournament tab that shows all agent bids with a min/max band across consensus entries.

---

## Scope

**In scope:**
- Add `estimate_low` / `estimate_high` to the PreBidCalc LLM prompt schema
- Render "Est. Range" row in the estimate totals block (Tab 1)
- New Tournament tab (Tab 4) with run form, agent card grid, and band summary

**Out of scope:**
- Tournament judge / feedback loop UI
- Per-sample breakdown within a personality
- Historical tournament list / tournament history view

---

## Architecture

No new API endpoints. All changes are either a prompt schema addition or frontend-only.

### 1. Backend — PreBidCalc prompt (`backend/agents/pre_bid_calc.py`)

Add two fields to the LLM JSON schema in `SYSTEM_PROMPT`:

```json
"estimate_low": 0.00,
"estimate_high": 0.00,
```

Placement: after `total_bid`, before `confidence`. The LLM already interpolates within `low_cost`/`high_cost` seed ranges per line item, so it can return a meaningful project-level range. These fields pass through the existing `/api/estimate` dict response — no Pydantic model or route changes required.

### 2. Frontend — Estimate totals block (`frontend/dist/index.html`)

In `renderEstimate(data)`, after the existing TOTAL BID row, conditionally append:

```html
<div class="total-row range">
  <span>Est. Range</span>
  <span>$X – $Y</span>
</div>
```

Only render if both `data.estimate_low` and `data.estimate_high` are present and non-zero. Style: muted color (`--text-muted`), smaller font than the grand total row. Add a `.total-row.range` CSS rule.

### 3. Frontend — Tournament tab (`frontend/dist/index.html`)

#### Tab button
Add a 4th tab button after "Import Bid History":

```html
<button class="tab-btn" data-tab="tournament">Tournament</button>
```

#### Tab panel (`id="tab-tournament"`)

**Form fields** (reuse existing `.card` / `.form-grid` / `.field` patterns):
- Description (textarea, same as Tab 1)
- Zip Code (text input)
- Trade Type (select, same options as Tab 1)
- Overhead % (range slider, default 20)
- Margin % (range slider, default 12)
- Client ID (text input, default `default`)
- Agents (number input, default `5`, min `1`, max `5`)
- Samples per agent (number input, default `3`, min `1`, max `5`)

**"Run Tournament" button** — calls `POST /api/tournament/run` with `X-API-Key` header (same auth pattern as existing fetch calls).

**Results section** (hidden until response):
- Band summary card: "Tournament Range — $X – $Y" derived from `Math.min/max` over `consensus_entries[*].total_bid`
- Agent card grid: one card per `consensus_entries` entry
  - Agent name (title-cased)
  - Total bid (large, amber styling if this entry has the lowest `total_bid` among all consensus entries — visual indicator of the most competitive bid, not a judged winner)
  - Confidence badge (reuse `.confidence-badge .confidence-{high|medium|low}` CSS classes already defined)

**Band computation (frontend):**
```js
const bids = result.consensus_entries.map(e => e.total_bid).filter(Boolean);
const bandLow  = Math.min(...bids);
const bandHigh = Math.max(...bids);
```

**API key handling:** The tournament endpoint requires `X-API-Key`. The existing tabs don't show an API key input — investigate how the existing fetch calls pass the key (hardcoded, env, or prompt). Match the same pattern. If no pattern exists yet, add a session-level key input (shared across tabs) in the header area.

---

## Data Flow

```
Tab 1 — Estimate
  User fills form → POST /api/estimate
  Response: { total_bid, estimate_low, estimate_high, confidence, line_items, ... }
  renderEstimate() → totals block + Est. Range row

Tab 4 — Tournament
  User fills form → POST /api/tournament/run
  Response: { tournament_id, entries[], consensus_entries[] }
  renderTournament() → band summary + agent card grid
```

---

## CSS Changes

All in `frontend/dist/index.html` `<style>` block:

```css
/* Range row in estimate totals */
.total-row.range {
  font-size: 0.78rem;
  color: var(--text-muted);
  border-top: 1px solid var(--border);
  padding-top: 5px;
  margin-top: 2px;
}

/* Tournament agent card grid */
.tournament-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
  gap: 10px;
  margin-top: 12px;
}

.agent-card {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 12px 14px;
}

.agent-card.winner {
  border-color: var(--amber-dim);
}

/* Band summary row */
.band-summary {
  display: flex;
  justify-content: space-between;
  align-items: center;
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 10px 16px;
  margin-bottom: 12px;
}
```

---

## Files Changed

| File | Change |
|------|--------|
| `backend/agents/pre_bid_calc.py` | Add `estimate_low`, `estimate_high` to `SYSTEM_PROMPT` JSON schema |
| `frontend/dist/index.html` | Add `.total-row.range` CSS + range row in `renderEstimate()`; add Tournament tab button, panel, `renderTournament()` function, CSS classes |

---

## Open Question (investigate during implementation)

The existing fetch calls to `/api/estimate` and `/api/bid/strategy` — how is `X-API-Key` currently passed? The frontend HTML doesn't show an explicit key input or a stored key. Resolve this before wiring up the tournament fetch, and apply consistently.
