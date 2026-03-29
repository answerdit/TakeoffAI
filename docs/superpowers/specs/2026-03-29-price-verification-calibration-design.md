# TakeoffAI — Price Verification & Self-Calibration System
**Date:** 2026-03-29
**Status:** Approved — ready for implementation planning
**Scope:** Web-sourced price verification, audit logging, human review queue, feedback loop closure, win probability calibration

---

## 1. Problem Statement

The current estimation pipeline has no ground truth for pricing. `material_costs.csv` contains 22 seed items; everything else is LLM-synthesized with no validation. There are no sanity checks on LLM-generated prices, no mechanism to detect drift from real-world market rates, and no feedback loop that closes after a job runs. Win probabilities and confidence levels are never validated against actual outcomes.

---

## 2. Goals

1. Log every LLM-generated unit price so a human can audit and override it
2. Automatically verify prices against web sources (supplier sites first, search fallback)
3. Auto-update `material_costs.csv` when 3+ sources agree; queue everything else for human review
4. After a job closes, feed actual costs back in and flag deviations >5%
5. Track win probability accuracy over time using Brier scoring
6. Red-flag any tournament agent whose estimates are consistently outside the winning range

---

## 3. Architecture

Three new components bolt onto the existing system. No existing agents are modified — only new functions are added and new trigger hooks are appended.

```
backend/
  agents/
    price_verifier.py        # NEW — web lookup + deviation logic
  api/
    verification.py          # NEW — on-demand + review queue endpoints
  scheduler.py               # NEW — nightly cron runner
  db.py                      # MODIFIED — 2 new tables added
  agents/feedback_loop.py    # MODIFIED — 2 new functions appended
  agents/judge.py            # MODIFIED — background task hook appended
  data/
    material_costs.csv       # MODIFIED — auto-updated by verifier (high confidence only)
    client_profiles/*.json   # MODIFIED — calibration block added
```

### Trigger Paths

| Trigger | When | What Gets Verified |
|---|---|---|
| Background | After `judge_tournament()` completes | All line items in the winning estimate |
| On-demand | `POST /api/verify/estimate` | Any estimate's line items, caller-triggered |
| Nightly batch | Cron at 2:00 AM local | All rows in `material_costs.csv` |

All three paths feed the same `price_audit` table.

---

## 4. Data Model

### 4.1 New Table: `price_audit`

```sql
CREATE TABLE price_audit (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    triggered_by    TEXT NOT NULL,
    -- 'background' | 'on_demand' | 'nightly'
    tournament_id   INTEGER,
    -- NULL for nightly batch runs
    line_item       TEXT NOT NULL,
    unit            TEXT NOT NULL,
    ai_unit_cost    REAL NOT NULL,
    -- the unit cost used in the estimate
    verified_low    REAL,
    verified_high   REAL,
    verified_mid    REAL,
    -- midpoint of verified range
    deviation_pct   REAL,
    -- ((ai_unit_cost - verified_mid) / verified_mid) * 100
    sources         TEXT,
    -- JSON: [{"url": "...", "price": 0.00, "retrieved_at": "ISO8601"}]
    source_count    INTEGER DEFAULT 0,
    flagged         INTEGER DEFAULT 0,
    -- 1 if abs(deviation_pct) > 5
    auto_updated    INTEGER DEFAULT 0,
    -- 1 if seed CSV was updated automatically
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

### 4.2 New Table: `review_queue`

```sql
CREATE TABLE review_queue (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    audit_id        INTEGER NOT NULL REFERENCES price_audit(id),
    line_item       TEXT NOT NULL,
    unit            TEXT NOT NULL,
    ai_unit_cost    REAL NOT NULL,
    verified_mid    REAL NOT NULL,
    deviation_pct   REAL NOT NULL,
    sources         TEXT,
    -- JSON array (same structure as price_audit.sources)
    status          TEXT NOT NULL DEFAULT 'pending',
    -- 'pending' | 'approved' | 'rejected'
    reviewer_notes  TEXT,
    resolved_at     DATETIME,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

### 4.3 Client Profile Extension

New `calibration` key added to `client_profiles/{client_id}.json`:

```json
"calibration": {
  "win_prob_predictions": [0.72, 0.65, 0.80],
  "win_prob_actuals":     [1,    0,    1   ],
  "brier_score":          0.14,
  "confidence_accuracy": {
    "low":    0.60,
    "medium": 0.71,
    "high":   0.83
  },
  "agent_deviation_history": {
    "conservative":    [-2.1, 4.3, -1.8],
    "balanced":        [0.5,  1.2,  0.8],
    "aggressive":      [8.1,  7.4,  6.9],
    "historical_match":[1.0,  0.3, -0.5],
    "market_beater":   [3.1,  2.8,  4.0]
  },
  "red_flagged_agents": ["aggressive"]
  -- agents with avg deviation > 5% over last 5 jobs
}
```

**Brier score interpretation:** < 0.25 = well-calibrated, 0.25–0.50 = moderate, > 0.50 = poor.

---

## 5. Price Verifier Agent (`price_verifier.py`)

### 5.1 Signature

```python
async def verify_line_items(
    line_items: list[dict],
    triggered_by: str,           # 'background' | 'on_demand' | 'nightly'
    tournament_id: Optional[int] = None
) -> list[dict]                  # list of audit records written to price_audit
```

### 5.2 Verification Logic (per line item)

**Phase 1 — Supplier lookup**
Query Home Depot, Lowe's, and one regional supplier catalog using `WebFetch`. URL patterns target product search pages for the item name + unit. Claude parses the returned HTML to extract a unit price. Target: 2 prices minimum from this phase.

**Phase 2 — Web search fallback**
If Phase 1 returns fewer than 2 results, run a `WebSearch` for:
`"{line_item} price per {unit} 2026"`
Parse top 3 results. Claude extracts numeric prices and source URLs.

**Phase 3 — Confidence decision**

| Condition | Action |
|---|---|
| 3+ sources agree (within 10% of each other) | Auto-update `material_costs.csv`; set `auto_updated=1` in audit |
| 1–2 sources OR sources disagree (>10% spread) | Write to `review_queue`; set `flagged=1` in audit |
| 0 sources found | Log failure in audit; skip; do not flag |

**Deviation threshold:** `abs(deviation_pct) > 5` triggers a flag.

### 5.3 Nightly Batch

`scheduler.py` runs at 2:00 AM local time. It reads all rows from `material_costs.csv`, builds synthetic line items (one per row), and calls `verify_line_items()` with `triggered_by='nightly'`. Results feed the same `price_audit` table. The scheduler logs a summary of items updated, flagged, and failed.

---

## 6. Feedback Loop Extensions (`feedback_loop.py`)

Two new functions appended. Existing functions are not modified.

### 6.1 `record_actual_outcome()`

```python
def record_actual_outcome(
    client_id: str,
    tournament_id: int,
    actual_cost: float,
    won: bool
) -> dict
```

- Loads the winning estimate's line items from `tournament_entries`
- Computes per-line-item deviation by prorating `actual_cost` across line items proportionally by each item's share of the AI estimate total: `prorated = actual_cost * (line_item_subtotal / estimate_total)`, then `deviation_pct = (ai_subtotal - prorated) / prorated * 100`
- Appends deviation to `calibration.agent_deviation_history` in client profile
- Red-flags any agent whose last 5 deviations average > 5%
- Updates `calibration.win_prob_predictions` and `calibration.win_prob_actuals`
- Recomputes Brier score
- Returns updated calibration block

### 6.2 `get_agent_accuracy_report()`

```python
def get_agent_accuracy_report(client_id: str) -> dict
```

Returns:
- Per-agent average deviation (last 5 jobs)
- Red-flag status per agent
- Brier score + confidence accuracy breakdown
- Recommendation: which agent personality is currently most accurate for this client

---

## 7. Judge Hook (`judge.py`)

One addition at the end of `judge_tournament()`, after `update_client_profile()` completes:

```python
# Fire-and-forget background verification of winning estimate's line items
asyncio.create_task(
    verify_line_items(
        line_items=winner_entry.estimate.get("line_items", []),
        triggered_by="background",
        tournament_id=tournament_id
    )
)
```

This is non-blocking. The judge response returns immediately; verification runs in the background.

---

## 8. API Endpoints (`verification.py`)

New router mounted at `/api/verify`.

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/verify/estimate` | On-demand verify any estimate's line items |
| `GET` | `/api/verify/audit` | List audit records (filter: `flagged`, `triggered_by`, `date_from`, `date_to`, `line_item`) |
| `GET` | `/api/verify/queue` | List pending review queue items |
| `PATCH` | `/api/verify/queue/{id}` | Approve or reject a flagged deviation (`status`, `reviewer_notes`) |
| `POST` | `/api/verify/outcome` | Submit actual job cost after closeout (`client_id`, `tournament_id`, `actual_cost`, `won`) |
| `GET` | `/api/client/{client_id}/accuracy` | Agent accuracy + calibration report |

### Key Pydantic Models

```python
class VerifyEstimateRequest(BaseModel):
    line_items: list[dict]       # from any estimate response
    tournament_id: Optional[int] = None

class OutcomeRequest(BaseModel):
    client_id: str
    tournament_id: int
    actual_cost: float           # total actual project cost
    won: bool

class QueueResolveRequest(BaseModel):
    status: Literal["approved", "rejected"]
    reviewer_notes: Optional[str] = None
```

---

## 9. Error Handling

| Scenario | Behavior |
|---|---|
| Supplier site unreachable | Log warning, continue to web search fallback |
| Web search returns no results | Log audit record with `source_count=0`, skip flagging |
| Claude fails to extract price from page | Treat as no result for that source |
| `material_costs.csv` write fails | Log error, do not mark `auto_updated`, add to review queue instead |
| `record_actual_outcome` called for unknown tournament | Return 404 |
| Background task exception | Caught and logged; does not affect judge response |

All verifier failures are non-fatal. The estimation pipeline never blocks on verification.

---

## 10. Testing Criteria

- [ ] `verify_line_items()` returns correct audit records for a known line item with mocked web sources
- [ ] Deviation > 5% correctly sets `flagged=1` and creates a `review_queue` entry
- [ ] 3 agreeing sources triggers `auto_updated=1` and updates `material_costs.csv`
- [ ] Background trigger fires after `judge_tournament()` and does not delay the response
- [ ] Nightly batch verifies all CSV rows and logs a run summary
- [ ] `record_actual_outcome()` correctly computes Brier score and red-flags agents at >5% avg deviation
- [ ] `PATCH /api/verify/queue/{id}` correctly updates status and `resolved_at`
- [ ] `GET /api/client/{client_id}/accuracy` returns calibration data with correct Brier score

---

## 11. Out of Scope

- Real-time price verification during estimate generation (adds latency, excluded by design)
- Integration with paid pricing APIs (RSMeans, etc.) — web scraping only for this phase
- UI changes — all new functionality is API-first; frontend surfacing is a separate task
- Historical backfill of past estimates through the verifier
