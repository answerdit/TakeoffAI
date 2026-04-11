# Tournament: A Practical Guide

A working manual for `POST /api/tournament/run`. Read this before you change tournament behavior, write a test against it, or wire a new frontend against the response.

Audience: engineers in this repo, coding agents refactoring tournament internals, QA validating output.

The document is structured as notebook cells so it can be pasted into Jupyter or fed into NotebookLM as a source. Each cell is labeled.

---

<!-- CELL: markdown -->

## 1. Purpose

By the end of this guide you should be able to:

1. Call `POST /api/tournament/run` and know what every field in the response means.
2. Tell the difference between a raw grid entry and a consensus entry, and know which one to trust.
3. Read the three accuracy fields and understand when they are populated and when they are not.
4. Understand how `rerank_active` changes the meaning of `consensus_entries` ordering.
5. Avoid the refactor traps that would silently break the hybrid accuracy rollout.

If you are tempted to "simplify" the rerank tier logic or copy accuracy annotations onto raw entries, read section 12 first. There is a reason those choices were made.

---

<!-- CELL: markdown -->

## 2. What Tournament is

Tournament runs a grid of LLM estimates against the same job description and then collapses the grid down to one opinionated number per personality.

The grid dimensions:

- **5 personalities**: `conservative`, `balanced`, `aggressive`, `historical_match`, `market_beater`. Each is a system-prompt modifier.
- **3 temperatures**: `0.3`, `0.7`, `1.0`.
- **n_samples** (1–5, default 2): how many repeats per cell.

Default shape: 5 × 3 × 2 = **30 parallel Anthropic calls**.

After the grid finishes, the tournament picks the entry closest to the median bid *within each personality*. That gives you 5 consensus entries — one per agent. The full 30 raw entries are preserved in the response for audit.

Optionally, the consensus entries can be re-ordered using the client's historical accuracy data. That overlay is the "hybrid rollout" and it is gated behind a feature flag plus a minimum-jobs threshold. Section 7 covers it in detail.

---

<!-- CELL: markdown -->

## 3. Basic request

Minimum required fields are `description` and `zip_code`. `client_id` is how the tournament pulls the client profile for excluded agents, winning examples, and accuracy history. Without it, you get a generic run with no personalization.

<!-- CELL: code (bash) -->

```bash
curl -X POST http://localhost:8000/api/tournament/run \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_KEY" \
  -d '{
    "description": "Build a 10,000 sqft metal warehouse in Houston, TX. Concrete slab, 24ft clear height, 2 overhead doors, basic electrical.",
    "zip_code": "77001",
    "client_id": "acme_construction",
    "n_samples": 2
  }'
```

<!-- CELL: markdown -->

`n_samples` is optional. Bumping it from 2 to 5 roughly doubles cost. Dropping to 1 halves cost and is fine for smoke tests.

---

<!-- CELL: markdown -->

## 4. Basic response anatomy

The response is one flat JSON object. These are the fields you will actually read:

| Field | Type | What it is |
|---|---|---|
| `tournament_id` | int | Primary key in `bid_tournaments`. Use it to look up trace files. |
| `entries` | array | The full raw grid. Up to 30 objects by default. One per LLM call. |
| `consensus_entries` | array | Exactly 5 objects. The authoritative per-agent view. |
| `accuracy_annotations` | object | Map of `agent_name -> {avg_deviation_pct, closed_job_count, is_accuracy_flagged}`. |
| `accuracy_recommended_agent` | string\|null | Lowest-deviation non-flagged agent, or `null`. |
| `rerank_active` | bool | `true` iff the consensus order was modified by accuracy re-ranking. |

There are other fields on the response — do not depend on them unless they are listed here.

---

<!-- CELL: markdown -->

## 5. Raw entries vs consensus entries

This distinction trips people up. Get it right or you will end up double-counting estimates in the UI.

**Raw entries (`entries`)** are the full grid. If you ran the default 5×3×2 shape, you get 30 objects, one per LLM call. Each raw entry carries:

- `agent_name`, `total_bid`, `margin_pct`, `confidence`
- `temperature` and `sample_index` (so you can tell which cell it came from)
- `estimate` (the full structured estimate dict)

Raw entries are the audit trail. The frontend does not render them as cards. QA and the harness evolver both read them to spot outliers and temperature patterns.

**Consensus entries (`consensus_entries`)** are exactly 5. One per personality. Each consensus entry is the raw entry closest to the median bid within that personality — the grid "votes on itself." Consensus is what the UI renders as the 5-card row. Consensus is what the judge scores. Consensus is what gets annotated with accuracy data.

If you ever need to ask "what did `balanced` think the job should cost?" — the answer is in `consensus_entries`, not `entries`.

---

<!-- CELL: markdown -->

## 6. Accuracy fields explained

Three fields live on consensus entries when the client has calibration history:

- `avg_deviation_pct` — mean absolute percent deviation of this agent's bids from actual closed amounts, expressed as a percent. A value of `0.55` means this agent's historical bids were on average 0.55% off.
- `closed_job_count` — how many closed jobs contributed to the deviation figure. More is better. A deviation of `0.5%` over 2 jobs is not the same kind of signal as `0.5%` over 25.
- `is_accuracy_flagged` — boolean. `true` means this agent crossed the client's red-flag threshold. Treat flagged agents as suspect regardless of their bid.

They are populated **only when the client profile has data for that agent**. A brand-new client with no calibration gets no annotations on anything. A mature client may still have a fresh `historical_match` personality with no data — that is the "no-data" case, and it is handled specifically.

Two rules that never change:

1. **No-data is not zero.** If `avg_deviation_pct` is missing or `null`, do not coerce it to `0.0` for sorting. An agent with no history is neither perfect nor terrible. It is unknown.
2. **No-data is not flagged.** Do not treat a missing deviation as if the agent had crossed the threshold. Unknown and bad are separate states.

Accuracy annotations never appear on raw entries. Ever. See section 12 for why.

---

<!-- CELL: markdown -->

## 7. Hybrid reranking explained

Hybrid reranking is opt-in and gated. Two things must both be true for it to kick in:

```
settings.tournament_accuracy_rerank_enabled == True
AND
accuracy_annotations[recommended_agent].closed_job_count >= settings.tournament_accuracy_rerank_min_jobs
```

Defaults: the flag is `false`, the min-jobs threshold is `5`.

When both conditions hold, the consensus entries are re-sorted into three tiers. Within each tier, order is stable.

1. **Tier A — non-flagged agents with accuracy data**, ascending by `avg_deviation_pct`. Lowest deviation first.
2. **Tier B — no-data agents**, in their original consensus order. Neither rewarded nor punished.
3. **Tier C — flagged agents**, also in their original consensus order. Always last.

The response sets `rerank_active: true`. Nothing else changes — the raw grid, the consensus set, and the annotations are identical to the default-order response. Only the *order* of `consensus_entries` moves.

When either gate fails, you get the default order and `rerank_active: false`. The frontend must not pretend rerank happened when it did not.

---

<!-- CELL: markdown -->

## 8. Example: rerank inactive (new client)

A brand-new client has no calibration history. Annotations are empty. The flag can be on, and it does not matter — the minimum-jobs gate fails.

<!-- CELL: code (json) -->

```json
{
  "tournament_id": 4821,
  "entries": [
    {
      "agent_name": "balanced",
      "total_bid": 152430.0,
      "margin_pct": 12.0,
      "confidence": "high",
      "temperature": 0.3,
      "sample_index": 0,
      "estimate": { "line_items": [] }
    },
    {
      "agent_name": "aggressive",
      "total_bid": 86200.0,
      "margin_pct": 8.5,
      "confidence": "low",
      "temperature": 0.3,
      "sample_index": 0,
      "estimate": { "line_items": [] }
    }
  ],
  "consensus_entries": [
    { "agent_name": "conservative",     "total_bid": 168400.0, "margin_pct": 14.0, "confidence": "high"   },
    { "agent_name": "balanced",         "total_bid": 151200.0, "margin_pct": 12.0, "confidence": "high"   },
    { "agent_name": "aggressive",       "total_bid": 85600.0,  "margin_pct": 8.5,  "confidence": "low"    },
    { "agent_name": "historical_match", "total_bid": 149900.0, "margin_pct": 12.0, "confidence": "medium" },
    { "agent_name": "market_beater",    "total_bid": 144750.0, "margin_pct": 11.0, "confidence": "medium" }
  ],
  "accuracy_annotations": {},
  "accuracy_recommended_agent": null,
  "rerank_active": false
}
```

<!-- CELL: markdown -->

Notice what is missing: no `avg_deviation_pct`, no `closed_job_count`, no `is_accuracy_flagged` on any consensus entry. The frontend should render the 5 cards in default order with no accuracy meta lines and no "Sorted by accuracy" badge.

This is the zero-regression case. If your frontend change broke this, you broke existing customers.

---

<!-- CELL: markdown -->

## 9. Example: rerank active (mature client)

Same job, but the client has real history. `balanced` is the recommended agent with 6 closed jobs and 0.55% deviation. The flag is on. The gate passes. Consensus gets re-sorted.

<!-- CELL: code (json) -->

```json
{
  "tournament_id": 4822,
  "entries": [
    { "agent_name": "balanced",   "total_bid": 151248.0, "margin_pct": 12.0, "confidence": "high",   "temperature": 0.3, "sample_index": 0, "estimate": {} },
    { "agent_name": "aggressive", "total_bid": 85626.0,  "margin_pct": 8.5,  "confidence": "low",    "temperature": 0.3, "sample_index": 0, "estimate": {} }
  ],
  "consensus_entries": [
    {
      "agent_name": "balanced",
      "total_bid": 151248.0,
      "margin_pct": 12.0,
      "confidence": "high",
      "avg_deviation_pct": 0.55,
      "closed_job_count": 6,
      "is_accuracy_flagged": false
    },
    {
      "agent_name": "conservative",
      "total_bid": 162800.0,
      "margin_pct": 14.0,
      "confidence": "high",
      "avg_deviation_pct": 3.02,
      "closed_job_count": 5,
      "is_accuracy_flagged": false
    },
    {
      "agent_name": "market_beater",
      "total_bid": 144100.0,
      "margin_pct": 11.0,
      "confidence": "medium",
      "avg_deviation_pct": 4.00,
      "closed_job_count": 5,
      "is_accuracy_flagged": false
    },
    {
      "agent_name": "historical_match",
      "total_bid": 149900.0,
      "margin_pct": 12.0,
      "confidence": "medium",
      "avg_deviation_pct": null,
      "closed_job_count": 0,
      "is_accuracy_flagged": false
    },
    {
      "agent_name": "aggressive",
      "total_bid": 85626.0,
      "margin_pct": 8.5,
      "confidence": "low",
      "avg_deviation_pct": 10.10,
      "closed_job_count": 5,
      "is_accuracy_flagged": true
    }
  ],
  "accuracy_annotations": {
    "balanced":         { "avg_deviation_pct": 0.55,  "closed_job_count": 6, "is_accuracy_flagged": false },
    "conservative":     { "avg_deviation_pct": 3.02,  "closed_job_count": 5, "is_accuracy_flagged": false },
    "market_beater":    { "avg_deviation_pct": 4.00,  "closed_job_count": 5, "is_accuracy_flagged": false },
    "historical_match": { "avg_deviation_pct": null,  "closed_job_count": 0, "is_accuracy_flagged": false },
    "aggressive":       { "avg_deviation_pct": 10.10, "closed_job_count": 5, "is_accuracy_flagged": true  }
  },
  "accuracy_recommended_agent": "balanced",
  "rerank_active": true
}
```

<!-- CELL: markdown -->

Read the consensus order top to bottom:

1. `balanced` — 0.55% dev, 6 jobs, not flagged → Tier A, lowest deviation.
2. `conservative` — 3.02% → Tier A.
3. `market_beater` — 4.00% → Tier A.
4. `historical_match` — no history → Tier B.
5. `aggressive` — flagged → Tier C.

That is the three-tier rule playing out on a real shape. Tier A is sorted by deviation. Tier B keeps its original spot. Tier C is always last.

---

<!-- CELL: markdown -->

## 10. Example: cheapest flagged agent demoted

This is the scenario the regression test `test_rerank_flagged_cheapest_bid_still_sorts_last` pins.

`aggressive` produces the cheapest bid in the grid — $85,626 against the next lowest of $144,100. A naive "sort by cost" would put it first. The rerank pipeline puts it last because it is flagged for historical inaccuracy.

<!-- CELL: code (json) -->

```json
{
  "consensus_entries": [
    { "agent_name": "balanced",         "total_bid": 151248.0, "avg_deviation_pct": 0.55,  "closed_job_count": 6, "is_accuracy_flagged": false },
    { "agent_name": "conservative",     "total_bid": 162800.0, "avg_deviation_pct": 3.02,  "closed_job_count": 5, "is_accuracy_flagged": false },
    { "agent_name": "market_beater",    "total_bid": 144100.0, "avg_deviation_pct": 4.00,  "closed_job_count": 5, "is_accuracy_flagged": false },
    { "agent_name": "historical_match", "total_bid": 149900.0, "avg_deviation_pct": null,  "closed_job_count": 0, "is_accuracy_flagged": false },
    { "agent_name": "aggressive",       "total_bid": 85626.0,  "avg_deviation_pct": 10.10, "closed_job_count": 5, "is_accuracy_flagged": true  }
  ],
  "rerank_active": true
}
```

<!-- CELL: markdown -->

This is not a bug. It is the whole economic point of the hybrid rollout. "Cheap but historically inaccurate" is precisely the pattern that loses money on closed jobs. A bid that is 10% low sounds like a deal until you realize it is 10% low because the agent chronically under-scopes labor.

If a future refactor re-sorts consensus by `total_bid` after the rerank, this test fails. When it fails, fix the refactor. Do not change the test.

---

<!-- CELL: markdown -->

## 11. Example: no-data agent stays in the middle

This is what `test_rerank_no_data_tier_sits_between_data_and_flagged` enforces.

The `historical_match` personality has no closed jobs yet — a common state for newer clients even when the rest of the agents are calibrated. It must land strictly after every non-flagged agent with data, and strictly before any flagged agent.

<!-- CELL: code (json) -->

```json
{
  "consensus_entries": [
    { "agent_name": "balanced",         "avg_deviation_pct": 0.55,  "closed_job_count": 6, "is_accuracy_flagged": false },
    { "agent_name": "conservative",     "avg_deviation_pct": 3.02,  "closed_job_count": 5, "is_accuracy_flagged": false },
    { "agent_name": "market_beater",    "avg_deviation_pct": 4.00,  "closed_job_count": 5, "is_accuracy_flagged": false },
    { "agent_name": "historical_match", "avg_deviation_pct": null,  "closed_job_count": 0, "is_accuracy_flagged": false },
    { "agent_name": "aggressive",       "avg_deviation_pct": 10.10, "closed_job_count": 5, "is_accuracy_flagged": true  }
  ],
  "rerank_active": true
}
```

<!-- CELL: markdown -->

Why neutral and not "assume the worst"? Because the opposite choices are both bad in practice:

- Treating no-data as `0.0` would rocket a brand-new personality to the top purely because it is unknown. That is how you end up recommending an untested agent on a real bid.
- Treating no-data as flagged would punish every fresh personality by default, including one that might turn out to be the best agent for that client. The harness evolver exists precisely to test new prompts in production traffic — a "punish unknown" rule guts that loop.

Neutral is the only honest answer. Section 12 makes this a hard invariant.

---

<!-- CELL: markdown -->

## 12. Behavioral invariants

These three rules must survive any refactor of `_maybe_rerank_by_accuracy`, the tournament route serializer, or `get_accuracy_annotations`. Each has a pinned regression test.

### Invariant 1 — Flagged cheapest can still be demoted

An agent in `red_flagged_agents` sorts to the bottom of the consensus order even when it produced the lowest raw bid. This is not a soft preference. It is the load-bearing economic rule of the hybrid rollout.

Test: `tests/test_tournament.py::test_rerank_flagged_cheapest_bid_still_sorts_last`

### Invariant 2 — No-data is neutral

Agents without a deviation history sort after every non-flagged agent that has data, and before any flagged agent. `avg_deviation_pct is None` is not the same as `0.0`, and "no data" is not the same as "flagged." The three tiers are non-negotiable.

Test: `tests/test_tournament.py::test_rerank_no_data_tier_sits_between_data_and_flagged`

### Invariant 3 — Only consensus entries carry annotations

Raw entries do not carry `avg_deviation_pct`, `closed_job_count`, or `is_accuracy_flagged`. Only the 5 consensus entries are annotated. Copying annotations onto raw entries would 6× the response payload in the default shape and break the "consensus is the authoritative annotated view" contract.

Test: `tests/test_routes.py::test_tournament_run_preserves_accuracy_annotations_in_response`

If any of these three tests fail, the refactor is wrong. Not the test.

---

<!-- CELL: markdown -->

## 13. Common mistakes

A list of specific bad assumptions, each paired with what actually happens.

**"Null deviation means zero."**
No. `None` means the client has no history for that agent. Coercing it to `0.0` in the tier comparator puts unknown agents ahead of your best-calibrated one.

**"No-data and flagged go together."**
No. They are separate tiers. Bucketing them together is how you accidentally punish a brand-new personality that nobody has measured yet.

**"If the cheapest bid isn't first, something is broken."**
Only if `rerank_active` is false. When rerank is active, cheapest flagged gets demoted on purpose. Read the flag before concluding the ordering is wrong.

**"I can resort consensus by `total_bid` at the end for stability."**
You cannot. A bid-based final sort destroys the tier structure. If you need a tiebreaker inside Tier A, use deviation first, then original consensus order. Never bid.

**"`rerank_active` just tells the UI which label to show."**
It also tells you how to interpret the order of `consensus_entries`. If you cache or log that list, log `rerank_active` next to it. The same 5 entries in a different order mean different things.

**"Raw entries should carry annotations too, for symmetry."**
They should not. The grid defaults to 30 raw entries. Duplicating three annotation fields per raw entry bloats the payload and breaks the "consensus is the annotated view" contract. The frontend does not render raw entries as cards, so there is no one to show the annotation to anyway.

**"I can drop `is_accuracy_flagged` from the tier key as long as flagged agents have high deviation."**
You cannot. A flagged agent might have lower deviation than a non-flagged one at the margin. The flag is what demotes it, not the number. Drop the flag from the comparator and a future red-flag-plus-lucky-streak agent will outrank a clean one.

**"If no consensus entry has annotations, I can still show accuracy meta lines as empty."**
Don't. The frontend has a grid-level check: if no entry has any annotation data, render no meta lines at all. Showing "no history" on every card when the whole feature is inactive is visual noise and confuses QA.

---

<!-- CELL: markdown -->

## 14. How the frontend should read this

`frontend/dist/app.js` consumes the response in `renderTournament`. The rules below are what the current implementation does. If you change any of them, you are changing the contract.

### `rerank_active`

Toggle the "Sorted by accuracy" pill in the results header. When `true`, show the pill. When `false`, hide it. That's the whole interaction.

```js
const rerankBadge = document.getElementById('trn-rerank-badge');
rerankBadge.style.display = data.rerank_active ? 'inline-block' : 'none';
```

Do not style the rest of the grid differently based on this flag. The cards render the same either way — only their order differs, and the backend already did that work.

### `accuracy_recommended_agent`

The lowest-deviation non-flagged agent. Can be `null` for new clients. The current frontend does not emphasize this agent visually beyond its position in the reranked order, and that is fine. If you want to add a "recommended" ribbon later, this is the field that tells you which card gets it.

### `avg_deviation_pct`

Render as `±X.X%` rounded to one decimal. When `null`, render the literal string `no history`. Do not render `±0.0%` for null — that would lie about the data.

```js
const dev = entry.avg_deviation_pct;
const label = dev != null ? `\u00b1${Number(dev).toFixed(1)}%` : 'no history';
```

### `closed_job_count`

Render as `N job` or `N jobs` depending on singular/plural. Only render if `> 0`. A zero job count paired with a `no history` deviation should not produce a `0 jobs` line — that is redundant and ugly.

### `is_accuracy_flagged`

When `true`, add the `flagged` class to the card's root div and append a `FLAGGED` pill inside the accuracy meta line. The class is what triggers the red tint in CSS. The pill is what actually communicates the state to the user.

### Grid-level "is the annotation layer even live?" check

Before rendering any accuracy meta lines at all, check whether **any** entry has annotation data:

```js
const hasAnyAccuracyData = entries.some(e =>
  e.avg_deviation_pct != null
  || (e.closed_job_count || 0) > 0
  || e.is_accuracy_flagged === true
);
```

If `hasAnyAccuracyData` is `false`, do not render meta lines on any card. The result should look identical to the pre-hybrid UI. This is what keeps new clients on the same visual surface as before — zero regression for customers who don't have calibration data yet.

---

<!-- CELL: markdown -->

## 15. Quick validation checklist

Before you ship a change that touches tournament, run through this list.

**Backend**

- [ ] `uv run pytest tests/test_tournament.py tests/test_routes.py -v` is green.
- [ ] The three pinned tests still exist and still pass:
  - `test_rerank_flagged_cheapest_bid_still_sorts_last`
  - `test_rerank_no_data_tier_sits_between_data_and_flagged`
  - `test_tournament_run_preserves_accuracy_annotations_in_response`
- [ ] Raw entries in a live response carry no `avg_deviation_pct`, `closed_job_count`, or `is_accuracy_flagged` keys.
- [ ] `rerank_active` is `true` only when the gate actually fired. Flipping the feature flag off without rerunning should not flip the field.

**Frontend**

- [ ] A new client with no calibration sees the same grid they saw before the hybrid rollout. No pill, no meta lines, no flagged tint.
- [ ] A mature client with at least one flagged agent sees the FLAGGED pill on that card and the red tint.
- [ ] The "Sorted by accuracy" pill appears only when `rerank_active` is `true`.
- [ ] A consensus entry with `avg_deviation_pct: null` renders "no history", not "±0.0%".

**End-to-end**

- [ ] Fire one real tournament against a calibrated client profile. Confirm the recommended agent is first and the flagged agent is last.
- [ ] Confirm the cheapest raw bid belongs to a flagged agent, and that agent still lands in the bottom card slot.
- [ ] Read `rerank_active` from the response in the network tab and match it to the badge state in the DOM.

If all three sections pass, the hybrid rollout is still intact and the refactor is safe to merge.
