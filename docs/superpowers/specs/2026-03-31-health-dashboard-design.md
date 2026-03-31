# TakeoffAI — Health Dashboard Design Spec
**Date:** 2026-03-31
**Status:** Approved — ready for implementation planning
**Scope:** Single-page vanilla HTML/JS health dashboard at `localhost:3000/health` — tabbed layout, scorecard vitals, expand-in-place fix actions, manual health check trigger

---

## 1. Problem Statement

TakeoffAI has a fully functional price verification and self-calibration backend (price_audit, review_queue, agent ELO, Brier scoring) but no way to inspect system health without hitting raw API endpoints. The estimator needs a "doctor's dashboard" — a single page they can open before a client meeting, after a job closes, or any time they want a quick read on whether the AI is calibrated and the data is clean.

---

## 2. Goals

1. Surface all health signals in one place: agent red-flags, price deviations, win probability calibration, nightly batch status
2. Allow inline resolution of flagged items (price queue, agent red-flags) without leaving the page
3. Provide a manual "Run Check Now" button that triggers on-demand verification
4. Zero new dependencies — vanilla HTML/CSS/JS dropped into the existing `frontend/dist/` folder
5. Works immediately in the Docker container via the existing nginx setup

---

## 3. Architecture

### New / Modified Files

| File | Change | Responsibility |
|---|---|---|
| `frontend/dist/health.html` | CREATE | Entire dashboard — HTML + inline CSS + vanilla JS |
| `nginx.conf` | MODIFY | Add `/health` route serving `health.html` |
| `backend/api/verification.py` | MODIFY | Add `POST /api/verify/run`; add `custom_price` field to `QueueResolveRequest`; call `_update_seed_csv()` when queue item approved |
| `backend/api/routes.py` | MODIFY | Add `POST /api/client/{client_id}/exclude-agent` and `DELETE /api/client/{client_id}/agent-history/{agent_name}` |
| `backend/agents/tournament.py` | MODIFY | Skip agents in `client_profile.excluded_agents[]` when running tournament |

### How It Fits

`health.html` is a self-contained static file served by nginx at `localhost:3000/health`. It makes `fetch()` calls to the FastAPI backend at `/api/*`. No build step, no framework, no CDN. Same pattern as the existing `index.html` and `upload.html`.

The dashboard reads a `?client=` query parameter to scope accuracy data to a specific client (e.g. `localhost:3000/health?client=acme_construction`). Falls back to `"default"` if absent. A small input in the top bar lets the user switch clients without a page reload.

---

## 4. Tab Structure

### 4.1 Top Bar (persistent, all tabs)

- Left: `⚡ TakeoffAI Health` label
- Right: client switcher input + last-run timestamp + `▶ Run Check Now` button
- "Run Check Now" calls `POST /api/verify/run`, shows spinner during request, refreshes all tab data on completion

### 4.2 OVERVIEW Tab

**Scorecards (4, top row):**

| Scorecard | Data Source | Color Logic |
|---|---|---|
| AGENTS HEALTHY | `accuracy.{agent}.red_flagged` count | Green if 0 flagged, yellow if any |
| IN REVIEW QUEUE | `GET /api/verify/queue?status=pending` count | Green if 0, yellow if 1–2, red if 3+ |
| BRIER SCORE | `accuracy.brier_score` | Green if < 0.25, yellow if 0.25–0.50, red if > 0.50 |
| PRICES VERIFIED | `GET /api/verify/audit` total count | Green always (informational) |

**Alert Strip (below scorecards):**

One alert row per active issue. Issues in priority order:
1. Red-flagged agents — one row per flagged agent (from `accuracy.red_flagged_agents[]`)
2. Pending queue items — one row if queue count > 0
3. Nightly batch status — always shown (green if last run OK, yellow if > 25h since last run)

Each alert row has a `→ fix` button. Clicking it expands the row inline (no modal, no tab switch) with resolution controls. Clicking `→ fix` again collapses it.

**Red-flagged agent fix options (expand-in-place):**
- `Exclude from tournaments` — calls `POST /api/client/{client_id}/exclude-agent` (new endpoint, see §6)
- `Keep & watch` — dismisses alert locally for this session
- `Reset history` — calls `DELETE /api/client/{client_id}/agent-history/{agent_name}` (new endpoint)

**Queue alert fix (expand-in-place):**
- Shows count + "→ go to Queue tab to resolve" link that switches to the Queue tab

**Nightly batch row:**
- Shows last run time, items checked, items flagged, items auto-updated
- Last run time derived from `GET /api/verify/audit?triggered_by=nightly&limit=1` — uses `created_at` of the most recent nightly audit record
- Yellow warning if most recent nightly record is > 25 hours old (missed a run)
- No fix action needed — informational only

### 4.3 AGENTS Tab

One row per agent (conservative, balanced, aggressive, historical_match, market_beater):

| Column | Source |
|---|---|
| Agent name | Static |
| Avg deviation % (last 5 jobs) | `accuracy.{agent}.avg_deviation_pct` |
| Deviation history sparkline | `accuracy.{agent}.deviation_history` (5 values, rendered as inline bar chart) |
| ELO score | `profile.agent_elo.{agent}` |
| Health status | `accuracy.{agent}.red_flagged` → `● healthy` or `⚠ flagged` |

Footer row: Brier score + calibration verdict + total win probability predictions count.

No fix actions on this tab — flagged agents are fixed from the Overview alert strip.

### 4.4 PRICES Tab

Table of all price audit records from `GET /api/verify/audit?limit=100`:

| Column | Source |
|---|---|
| Item | `price_audit.line_item` |
| Unit | `price_audit.unit` |
| AI price | `price_audit.ai_unit_cost` |
| Web price | `price_audit.verified_mid` |
| Deviation | `price_audit.deviation_pct` — colored red if flagged |
| Sources | `price_audit.source_count` |
| Triggered by | `price_audit.triggered_by` badge |
| Auto-updated | `price_audit.auto_updated` — green checkmark if 1 |
| Date | `price_audit.created_at` |

Filterable by: flagged only (toggle), triggered_by (dropdown), date range (two inputs).

No fix actions on this tab — flagged prices are resolved in the Queue tab.

### 4.5 QUEUE Tab

One card per pending queue item from `GET /api/verify/queue?status=pending`:

Each card shows:
- Item name + unit
- AI price vs web price + deviation % (colored)
- Source breakdown: "HD $0.94 · Lowe's $0.99 · Web $0.98"
- Three inline actions:
  - `✓ Accept $X.XX` — calls `PATCH /api/verify/queue/{id}` with `{status: "approved"}`; backend calls `_update_seed_csv(item, verified_low, verified_high)` on approval
  - `✗ Keep $X.XX` — calls `PATCH /api/verify/queue/{id}` with `{status: "rejected"}`; CSV unchanged
  - Custom price input + `→ set` — calls `PATCH /api/verify/queue/{id}` with `{status: "approved", custom_price: X}`; backend calls `_update_seed_csv(item, custom_price * 0.95, custom_price * 1.05)` (±5% band)

`QueueResolveRequest` Pydantic model gains an optional `custom_price: Optional[float] = None` field.

After resolution, the card collapses with a `✓ Done` flash. Queue badge count in the tab header decrements live.

If queue is empty: shows `✓ Nothing to review — all prices verified`.

---

## 5. New Backend Endpoint: `POST /api/verify/run`

**Purpose:** Trigger on-demand verification of all `material_costs.csv` rows (same as nightly batch). Returns a summary.

**Request:** `POST /api/verify/run` — no body required.

**Response:**
```json
{
  "status": "complete",
  "items_checked": 22,
  "flagged": 3,
  "auto_updated": 1,
  "duration_seconds": 14.2,
  "triggered_at": "2026-03-31T10:30:00Z"
}
```

**Implementation:** Calls `_run_nightly_verification()` from `backend/scheduler.py` as an awaited coroutine (not a background task — the response waits for completion so the dashboard can refresh with fresh data).

---

## 6. New Backend Endpoints: Agent Management

Two new endpoints to support the Overview fix actions:

### `POST /api/client/{client_id}/exclude-agent`
**Body:** `{"agent_name": "aggressive"}`
Adds agent to a new `excluded_agents` list in the client profile JSON. The `tournament.py` agent checks this list and skips excluded agents when running a tournament.

### `DELETE /api/client/{client_id}/agent-history/{agent_name}`
Clears `calibration.agent_deviation_history[agent_name]` in the client profile JSON and removes agent from `red_flagged_agents`. Returns updated calibration block.

---

## 7. nginx Route

Add to `nginx.conf` inside the `server {}` block, before the catch-all `/` location:

```nginx
location = /health {
    try_files /health.html =404;
}
```

---

## 8. Client Switcher

The top bar includes a small text input pre-filled with the `?client=` query param value (or `"default"`). On change (debounced 500ms), re-fetches all tab data for the new client ID and updates the URL query param without a page reload (`history.replaceState`).

---

## 9. Data Loading & Refresh

On page load, all four tabs fetch their data in parallel. Each section renders independently — a slow `/api/verify/accuracy/{client_id}` call doesn't block the Queue tab from loading.

After "Run Check Now" completes, all sections refresh automatically.

Each tab shows a subtle `↻ refreshing...` indicator during fetches. If any fetch fails, the affected section shows an inline error: `⚠ Could not load — is the API running?`

---

## 10. Error & Empty States

| Scenario | Behavior |
|---|---|
| API unreachable | Each section shows `⚠ Could not connect to API` independently |
| No accuracy data for client | Agents tab: `No data yet — run some tournaments first` |
| Empty queue | Queue tab: `✓ Nothing to review` |
| No audit records | Prices tab: `No price verification runs yet — click Run Check Now` |
| Brier: not enough data | Brier scorecard shows `–` instead of score with tooltip "Need 5+ outcomes" |
| Run Check in progress | Button disabled + spinner, sections show `↻ refreshing...` |
| Fix action success | Alert/card collapses with `✓ Done` flash (300ms), badge decrements |
| Fix action failure | Inline `⚠ Failed — try again` below the action buttons |

---

## 11. Styling

Matches the existing TakeoffAI frontend aesthetic:
- Dark background: `#0f172a`
- Card/panel: `#1e293b`
- Accent blue: `#38bdf8`
- Success green: `#4ade80`
- Warning yellow: `#facc15`
- Error red: `#f87171`
- Font: monospace throughout (matches existing UI)
- No external CSS frameworks

---

## 12. Testing Criteria

- [ ] Dashboard loads at `localhost:3000/health` with no JS errors
- [ ] `?client=acme` populates client switcher and fetches accuracy data for `acme`
- [ ] All 4 tabs switch correctly, active tab highlighted
- [ ] "Run Check Now" triggers `POST /api/verify/run`, shows spinner, refreshes data on completion
- [ ] Overview scorecards show correct counts from API
- [ ] Red-flag alert expands inline on `→ fix` click; collapses on re-click
- [ ] Queue alert `→ fix` switches to Queue tab
- [ ] Queue item Accept calls `PATCH /api/verify/queue/{id}`, card collapses with `✓ Done`
- [ ] Queue item Keep calls `PATCH` with `rejected` status
- [ ] Custom price submit sends correct payload
- [ ] Queue badge count decrements after each resolution
- [ ] Prices tab filter (flagged only toggle) correctly filters rows
- [ ] Client switcher re-fetches data and updates URL without page reload
- [ ] API-down state shows per-section error without full page crash
- [ ] Empty queue shows `✓ Nothing to review`
- [ ] nginx `/health` route serves `health.html`

---

## 13. Out of Scope

- Authentication / per-user access control
- Mobile responsive layout (desktop only)
- Historical trend charts / time-series graphs
- Email / notification alerts when agents get flagged
- Tournament history browser
- Exporting audit data to CSV from the UI (use `GET /api/verify/audit` directly)
