# Confidence Bands Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface cost uncertainty as a dollar range in two places — a "Est. Range" row on the Pre-Bid Estimate totals block (driven by new LLM fields), and a new Tournament tab showing all agent bids with a min/max band across consensus entries.

**Architecture:** One backend prompt change passes through the existing dict response unchanged. All UI work is in `frontend/dist/index.html` (no build step — the file is the deployed artifact). The frontend currently doesn't pass `X-API-Key` on any fetch call; fix that first so all tabs work correctly.

**Tech Stack:** Python/FastAPI (backend), plain HTML/CSS/JS (frontend), pytest + httpx (tests), `uv run pytest` to run tests.

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `backend/agents/pre_bid_calc.py` | Modify | Add `estimate_low`, `estimate_high` to LLM JSON schema |
| `tests/test_routes.py` | Modify | Add passthrough test for new estimate fields |
| `frontend/dist/index.html` | Modify | All UI: API key helper, estimate range row, Tournament tab |

---

### Task 1: Add estimate_low / estimate_high to PreBidCalc

**Files:**
- Modify: `backend/agents/pre_bid_calc.py:63-87` (SYSTEM_PROMPT JSON schema)
- Modify: `tests/test_routes.py` (add passthrough test)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_routes.py`:

```python
from unittest.mock import AsyncMock, patch

@pytest.mark.anyio
async def test_estimate_passes_through_confidence_band(monkeypatch):
    """estimate endpoint should pass estimate_low and estimate_high from the agent."""
    monkeypatch.setenv("API_KEY", "test-key")

    mock_result = {
        "project_summary": "Small office build",
        "location": "75001",
        "line_items": [],
        "subtotal": 100000.0,
        "overhead_pct": 20,
        "overhead_amount": 20000.0,
        "margin_pct": 12,
        "margin_amount": 14400.0,
        "total_bid": 134400.0,
        "estimate_low": 118000.0,
        "estimate_high": 151000.0,
        "confidence": "medium",
        "notes": "Test note",
    }

    with patch("backend.api.routes.run_prebid_calc", new=AsyncMock(return_value=mock_result)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post(
                "/api/estimate",
                json={"description": "Build a small office", "zip_code": "75001"},
                headers={"X-API-Key": "test-key"},
            )

    assert resp.status_code == 200
    data = resp.json()
    assert "estimate_low" in data
    assert "estimate_high" in data
    assert data["estimate_low"] < data["total_bid"] < data["estimate_high"]
```

Also add the `AsyncMock` import if not already present — check the top of `tests/test_routes.py` for existing imports and add `from unittest.mock import AsyncMock, patch` if missing.

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_routes.py::test_estimate_passes_through_confidence_band -v
```

Expected: FAIL — `estimate_low` key missing from response (LLM doesn't return it yet).

- [ ] **Step 3: Update SYSTEM_PROMPT in pre_bid_calc.py**

In `backend/agents/pre_bid_calc.py`, locate the JSON schema block inside `SYSTEM_PROMPT` (lines ~63–87). Add `estimate_low` and `estimate_high` after `total_bid`:

```python
SYSTEM_PROMPT = f"""You are PreBidCalc, an expert construction cost estimator for TakeoffAI by answerd.it.
...
Always return valid JSON in this exact format — no markdown fences, no extra text:
{{
  "project_summary": "...",
  "location": "...",
  "line_items": [
    {{
      "description": "...",
      "quantity": 0,
      "unit": "sqft|LF|EA|LS|CY|SQ|GAL",
      "unit_material_cost": 0.00,
      "unit_labor_cost": 0.00,
      "total_material": 0.00,
      "total_labor": 0.00,
      "subtotal": 0.00
    }}
  ],
  "subtotal": 0.00,
  "overhead_pct": 0,
  "overhead_amount": 0.00,
  "margin_pct": 0,
  "margin_amount": 0.00,
  "total_bid": 0.00,
  "estimate_low": 0.00,
  "estimate_high": 0.00,
  "confidence": "low|medium|high",
  "notes": "..."
}}"""
```

Also add a sentence to the instructions section (after step 5, before the JSON schema):

```
6. Return estimate_low and estimate_high as the realistic low and high end of the total project cost, based on the low/high range in the seed data and scope uncertainty. estimate_low should be 5–15% below total_bid; estimate_high should be 5–15% above total_bid.
```

The full instructions block starts at line ~48 with `Your job is to:`. Add this as item 6 in that numbered list.

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_routes.py::test_estimate_passes_through_confidence_band -v
```

Expected: PASS — mock response passes through unchanged.

- [ ] **Step 5: Run full test suite to confirm no regressions**

```bash
uv run pytest tests/ -v --ignore=tests/test_harness_evolver.py --ignore=tests/test_agentic_trace_proposer.py -x
```

Expected: all previously passing tests still pass. (The two ignored tests require live LLM calls.)

- [ ] **Step 6: Commit**

```bash
git add backend/agents/pre_bid_calc.py tests/test_routes.py
git commit -m "feat: add estimate_low/estimate_high to PreBidCalc LLM schema (#35)"
```

---

### Task 2: Frontend — API key header helper

**Files:**
- Modify: `frontend/dist/index.html` (header nav + JS utilities section)

The frontend currently sends no `X-API-Key` header on any fetch call, but the backend requires it. This task adds a shared API key helper used by all subsequent fetch calls.

- [ ] **Step 1: Add API key input to the header nav**

In `frontend/dist/index.html`, find the `<nav>` element inside `<header>` (around line 614):

```html
<nav style="display:flex;align-items:center;gap:1.5rem;">
  <a href="upload.html" class="nav-link">Import Bid History</a>
  <span class="header-badge">Beta</span>
</nav>
```

Replace with:

```html
<nav style="display:flex;align-items:center;gap:1rem;">
  <input
    id="hdr-api-key"
    type="password"
    placeholder="API Key"
    style="background:var(--bg-input);border:1px solid var(--border);border-radius:5px;
           padding:4px 10px;font-size:0.78rem;color:var(--text);width:130px;
           outline:none;font-family:monospace"
  />
  <span class="header-badge">Beta</span>
</nav>
```

- [ ] **Step 2: Add getHeaders() helper in the JS utilities section**

In `frontend/dist/index.html`, find the `/* ── Utilities ──` comment block (around line 921). Add after the existing utility functions (`fmt$`, `setLoading`, `showError`, `clearError`, `toast`):

```javascript
/* ── API key ───────────────────────────────────────────────────────── */

const hdrApiKey = document.getElementById('hdr-api-key');
hdrApiKey.value = sessionStorage.getItem('takeoffai_key') || '';
hdrApiKey.addEventListener('input', () =>
  sessionStorage.setItem('takeoffai_key', hdrApiKey.value)
);

function getHeaders() {
  return {
    'Content-Type': 'application/json',
    'X-API-Key': hdrApiKey.value,
  };
}
```

- [ ] **Step 3: Update existing fetch calls to use getHeaders()**

There are three fetch calls in the file. Find each one by searching for `'Content-Type': 'application/json'` and replace `headers: { 'Content-Type': 'application/json' }` with `headers: getHeaders()`.

The three locations are:
1. `POST /api/estimate` (inside `estBtn.addEventListener`)
2. `POST /api/bid/strategy` (inside `bidBtn.addEventListener`)
3. `POST /api/upload/...` calls inside the upload tab handlers

For the upload tab, the fetch uses `FormData` (not JSON), so its Content-Type must NOT be set manually. Find the upload fetch calls and add only the `X-API-Key` header:

```javascript
// Upload fetch — FormData, no Content-Type override
headers: { 'X-API-Key': hdrApiKey.value }
```

To find the exact upload fetch locations, search for `fetch(` in the file and inspect each one. Only override `Content-Type: application/json` on the JSON fetches; leave FormData fetches with just `X-API-Key`.

- [ ] **Step 4: Manual verification**

Start the backend: `uv run uvicorn backend.api.main:app --reload`

Open `frontend/dist/index.html` in a browser (or via the running nginx/docker). Enter the API key in the header input. Run a test estimate. Confirm requests succeed (200) in the browser DevTools Network tab — verify `X-API-Key` header is present on the request.

- [ ] **Step 5: Commit**

```bash
git add frontend/dist/index.html
git commit -m "fix: pass X-API-Key header on all frontend fetch calls (#35)"
```

---

### Task 3: Frontend — Confidence band row on estimate totals

**Files:**
- Modify: `frontend/dist/index.html` (CSS block + `renderEstimate()` function)

- [ ] **Step 1: Add CSS for the range row**

In `frontend/dist/index.html`, find the `.total-row.grand` CSS rule (around line 436):

```css
.total-row.grand {
```

Add a new rule directly after the closing `}` of `.total-row.grand span:last-child`:

```css
.total-row.range {
  font-size: 0.78rem;
  color: var(--text-muted);
  border-top: 1px dashed #2a2a2a;
  padding-top: 5px;
  margin-top: 3px;
}
```

- [ ] **Step 2: Update renderEstimate() to show the range row**

In `frontend/dist/index.html`, find `renderEstimate` function, specifically the `totalsEl.innerHTML` assignment (around line 1052). The current grand total row ends with:

```javascript
      <div class="total-row grand">
        <span>TOTAL BID</span>
        <span>${fmt$(data.total_bid)}</span>
      </div>
```

Append a conditional range row after the grand total. Replace the full `totalsEl.innerHTML` assignment with:

```javascript
    const rangeRow = (data.estimate_low && data.estimate_high)
      ? `<div class="total-row range">
           <span>Est. Range</span>
           <span>${fmt$(data.estimate_low)} – ${fmt$(data.estimate_high)}</span>
         </div>`
      : '';

    totalsEl.innerHTML = `
      <div class="total-row subtotal">
        <span>Subtotal</span>
        <span>${fmt$(data.subtotal)}</span>
      </div>
      <div class="total-row">
        <span>Overhead (${data.overhead_pct ?? ''}%)</span>
        <span>${fmt$(data.overhead_amount)}</span>
      </div>
      <div class="total-row">
        <span>Margin (${data.margin_pct ?? ''}%)</span>
        <span>${fmt$(data.margin_amount)}</span>
      </div>
      <div class="total-row grand">
        <span>TOTAL BID</span>
        <span>${fmt$(data.total_bid)}</span>
      </div>
      ${rangeRow}
    `;
```

- [ ] **Step 3: Manual verification**

With the backend running and `estimate_low`/`estimate_high` in the LLM response (or by temporarily hard-coding them in a mock), run an estimate. Confirm the "Est. Range" row appears below TOTAL BID in muted text. If `estimate_low`/`estimate_high` are absent, confirm the row doesn't appear.

- [ ] **Step 4: Commit**

```bash
git add frontend/dist/index.html
git commit -m "feat: show confidence band range row on estimate totals (#35)"
```

---

### Task 4: Frontend — Tournament tab

**Files:**
- Modify: `frontend/dist/index.html` (CSS block, tab nav, new panel HTML, new JS section)

- [ ] **Step 1: Add Tournament CSS**

In `frontend/dist/index.html`, find the `@media (max-width: 700px)` block near the end of the `<style>` section (around line 597). Add new CSS rules directly **before** the `@media` block:

```css
/* ── Tournament tab ─────────────────────────────────────────────── */

.band-summary {
  display: flex;
  justify-content: space-between;
  align-items: center;
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 10px 16px;
  margin-bottom: 12px;
  font-size: 0.88rem;
}

.band-summary .band-label { color: var(--text-muted); }
.band-summary .band-value { color: var(--text); font-weight: 600; }

.tournament-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
  gap: 10px;
  margin-top: 12px;
}

.agent-card {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 12px 14px;
}

.agent-card.lowest-bid {
  border-color: var(--amber-dim);
}

.agent-name {
  font-size: 0.75rem;
  color: var(--text-muted);
  text-transform: capitalize;
  margin-bottom: 4px;
}

.agent-bid {
  font-size: 1.05rem;
  font-weight: 700;
  color: var(--text);
  margin-bottom: 6px;
}

.agent-card.lowest-bid .agent-bid { color: var(--amber); }
```

Also add responsive override inside the existing `@media (max-width: 700px)` block:

```css
.tournament-grid { grid-template-columns: 1fr 1fr; }
```

- [ ] **Step 2: Add Tournament tab button**

In `frontend/dist/index.html`, find the tabs nav (around line 624):

```html
  <div class="tabs">
    <button class="tab-btn active" data-tab="estimate">Pre-Bid Estimate</button>
    <button class="tab-btn"        data-tab="strategy">Bid Strategy</button>
    <button class="tab-btn"        data-tab="upload">Import Bid History</button>
  </div>
```

Add the Tournament button:

```html
  <div class="tabs">
    <button class="tab-btn active" data-tab="estimate">Pre-Bid Estimate</button>
    <button class="tab-btn"        data-tab="strategy">Bid Strategy</button>
    <button class="tab-btn"        data-tab="upload">Import Bid History</button>
    <button class="tab-btn"        data-tab="tournament">Tournament</button>
  </div>
```

- [ ] **Step 3: Add Tournament tab panel HTML**

In `frontend/dist/index.html`, find the closing `</main>` tag (around line 915). Insert the Tournament tab panel immediately before it:

```html
  <!-- ══════════════════════════════════════════════════════
       TAB 4 — TOURNAMENT
  ══════════════════════════════════════════════════════ -->
  <div id="tab-tournament" class="tab-panel">

    <div class="card">
      <div class="card-title">Run a Bid Tournament</div>
      <p style="color:var(--text-muted);font-size:0.83rem;margin-bottom:1rem;line-height:1.6">
        Runs 5 bidding personalities on the same job in parallel. Compare strategies and see the confidence band across all agent bids.
      </p>
      <div class="form-grid" style="gap:1.1rem">

        <div class="field full">
          <label for="trn-desc">Project Description</label>
          <textarea id="trn-desc" rows="4" placeholder="Describe the project — scope, size, location, materials, special requirements…"></textarea>
        </div>

        <div class="field">
          <label for="trn-zip">Zip Code</label>
          <input id="trn-zip" type="text" placeholder="e.g. 76801" maxlength="10" />
        </div>

        <div class="field">
          <label for="trn-trade">Trade Type</label>
          <select id="trn-trade">
            <option value="general">General</option>
            <option value="electrical">Electrical</option>
            <option value="plumbing">Plumbing</option>
            <option value="hvac">HVAC</option>
            <option value="roofing">Roofing</option>
            <option value="concrete">Concrete</option>
            <option value="framing">Framing</option>
          </select>
        </div>

        <div class="field">
          <label for="trn-client">Client ID</label>
          <input id="trn-client" type="text" value="default" placeholder="e.g. abc_construction" />
        </div>

        <div class="field">
          <label for="trn-overhead">Overhead: <strong id="trn-overhead-val">20</strong>%</label>
          <input type="range" id="trn-overhead" min="0" max="60" step="1" value="20" />
        </div>

        <div class="field">
          <label for="trn-margin">Margin: <strong id="trn-margin-val">12</strong>%</label>
          <input type="range" id="trn-margin" min="0" max="50" step="1" value="12" />
        </div>

        <div class="field">
          <label for="trn-agents">Agents (max 5)</label>
          <input id="trn-agents" type="number" value="5" min="1" max="5" />
        </div>

        <div class="field">
          <label for="trn-samples">Samples per agent (max 5)</label>
          <input id="trn-samples" type="number" value="3" min="1" max="5" />
        </div>

      </div>
    </div>

    <div class="action-row">
      <button class="btn btn-primary" id="trn-btn">Run Tournament</button>
    </div>

    <div id="trn-error" class="error-box" style="display:none"></div>

    <div id="trn-results" class="results" style="display:none">
      <div class="results-header">
        <span class="results-title" id="trn-title">Tournament Results</span>
      </div>
      <div class="band-summary" id="trn-band" style="display:none">
        <span class="band-label">Tournament Range</span>
        <span class="band-value" id="trn-band-value">—</span>
      </div>
      <div class="tournament-grid" id="trn-grid"></div>
    </div>

  </div>
```

- [ ] **Step 4: Add Tournament JS**

In `frontend/dist/index.html`, find the `/* ── HTML escape ──` comment (around line 1226). Add the Tournament JS section directly before it:

```javascript
  /* ── TAB 4: Tournament ─────────────────────────────────────────── */

  const trnOverheadSlider = document.getElementById('trn-overhead');
  const trnMarginSlider   = document.getElementById('trn-margin');
  const trnOverheadVal    = document.getElementById('trn-overhead-val');
  const trnMarginVal      = document.getElementById('trn-margin-val');

  trnOverheadSlider.addEventListener('input', () => trnOverheadVal.textContent = trnOverheadSlider.value);
  trnMarginSlider.addEventListener('input',   () => trnMarginVal.textContent   = trnMarginSlider.value);

  const trnBtn     = document.getElementById('trn-btn');
  const trnError   = document.getElementById('trn-error');
  const trnResults = document.getElementById('trn-results');

  trnBtn.addEventListener('click', async () => {
    const description  = document.getElementById('trn-desc').value.trim();
    const zip_code     = document.getElementById('trn-zip').value.trim();
    const trade_type   = document.getElementById('trn-trade').value;
    const client_id    = document.getElementById('trn-client').value.trim() || 'default';
    const overhead_pct = parseFloat(trnOverheadSlider.value);
    const margin_pct   = parseFloat(trnMarginSlider.value);
    const n_agents     = parseInt(document.getElementById('trn-agents').value, 10);
    const n_samples    = parseInt(document.getElementById('trn-samples').value, 10);

    if (!description) { showError(trnError, 'Project description is required.'); return; }
    if (!zip_code)     { showError(trnError, 'Zip code is required.'); return; }

    clearError(trnError);
    trnResults.style.display = 'none';
    setLoading(trnBtn, true, 'Run Tournament');

    try {
      const res = await fetch(`${API_BASE}/api/tournament/run`, {
        method: 'POST',
        headers: getHeaders(),
        body: JSON.stringify({ description, zip_code, trade_type, client_id,
                               overhead_pct, margin_pct, n_agents, n_samples }),
      });

      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${res.status}`);
      }

      const data = await res.json();
      renderTournament(data);

    } catch (e) {
      showError(trnError, 'Error: ' + e.message);
    } finally {
      setLoading(trnBtn, false, 'Run Tournament');
    }
  });

  function renderTournament(data) {
    const entries = data.consensus_entries || [];

    // Band
    const bids = entries.map(e => e.total_bid).filter(b => b != null && !isNaN(b));
    const bandEl    = document.getElementById('trn-band');
    const bandValEl = document.getElementById('trn-band-value');

    if (bids.length >= 2) {
      const bandLow  = Math.min(...bids);
      const bandHigh = Math.max(...bids);
      bandValEl.textContent = `${fmt$(bandLow)} – ${fmt$(bandHigh)}`;
      bandEl.style.display = 'flex';
    } else {
      bandEl.style.display = 'none';
    }

    // Update title with tournament ID
    document.getElementById('trn-title').textContent =
      `Tournament #${data.tournament_id} Results`;

    // Agent cards
    const lowestBid = bids.length ? Math.min(...bids) : null;
    const grid = document.getElementById('trn-grid');
    grid.innerHTML = '';

    entries.forEach(e => {
      const isLowest = lowestBid != null && e.total_bid === lowestBid;
      const conf = (e.confidence || 'medium').toLowerCase();
      const card = document.createElement('div');
      card.className = 'agent-card' + (isLowest ? ' lowest-bid' : '');
      card.innerHTML = `
        <div class="agent-name">${esc(e.agent_name || '—')}${isLowest ? ' ★' : ''}</div>
        <div class="agent-bid">${fmt$(e.total_bid)}</div>
        <span class="confidence-badge confidence-${conf}">
          ${conf.charAt(0).toUpperCase() + conf.slice(1)}
        </span>
      `;
      grid.appendChild(card);
    });

    trnResults.style.display = 'block';
  }
```

- [ ] **Step 5: Manual verification**

With backend running and a valid API key in the header input:
1. Click the "Tournament" tab — confirm it appears and the form renders correctly
2. Fill in a project description and zip code, leave other fields at defaults
3. Click "Run Tournament" — confirm spinner appears
4. Confirm results render: band summary row shows "$X – $Y", agent cards appear with confidence badges, lowest-bid card has amber border and ★
5. Confirm no console errors in browser DevTools

- [ ] **Step 6: Commit**

```bash
git add frontend/dist/index.html
git commit -m "feat: add Tournament tab with confidence band (#35)"
```

---

### Task 5: Final regression check

- [ ] **Step 1: Run full test suite**

```bash
uv run pytest tests/ -v --ignore=tests/test_harness_evolver.py --ignore=tests/test_agentic_trace_proposer.py
```

Expected: all tests pass.

- [ ] **Step 2: Smoke test all tabs**

With backend running (`uv run uvicorn backend.api.main:app --reload`) and a valid API key entered in the header:

1. **Tab 1 — Pre-Bid Estimate:** Submit a project → verify `Est. Range` row appears below TOTAL BID
2. **Tab 2 — Bid Strategy:** Submit an RFP with estimate JSON → verify scenarios render
3. **Tab 3 — Import Bid History:** Upload a CSV → verify parse results show
4. **Tab 4 — Tournament:** Run a tournament → verify band + agent cards render

- [ ] **Step 3: Commit any fixups, then add .superpowers to .gitignore**

```bash
echo '.superpowers/' >> .gitignore
git add .gitignore
git commit -m "chore: ignore .superpowers brainstorm dir"
```
