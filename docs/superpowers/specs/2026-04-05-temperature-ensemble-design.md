# Temperature & Self-Consistency Ensemble — Design Spec

**Date:** 2026-04-05  
**Feature:** Stochastic multi-agent consensus via temperature ladder + self-consistency collapse  
**Ideas:** #11 (temperature sampling ladder) and #12 (self-consistency median collapse)

---

## Summary

Expand the existing 5-agent tournament into a 5 × 3 × N grid:
- **5 personalities** (conservative, balanced, aggressive, historical_match, market_beater)
- **3 temperature tiers** (0.3 / 0.7 / 1.2)
- **N samples per cell** (default 2, configurable via `n_samples`)

Default: **30 parallel API calls** per tournament (5 × 3 × 2).

All raw entries are stored. A median-collapse step produces 5 "consensus entries" (one per personality). The judge works on consensus entries — identical interface to today.

---

## Goals

- Capture LLM sampling variance within each personality (self-consistency)
- Capture determinism vs. creativity tradeoffs across temperature tiers
- Return richer uncertainty signal (spread across 30 entries) while keeping judge logic unchanged
- Be configurable so operators can trade cost for quality

---

## Architecture

### Affected Files

| File | Change |
|------|--------|
| `backend/agents/utils.py` | Add `temperature: float = 0.7` to `call_with_json_retry` |
| `backend/agents/pre_bid_calc.py` | Add `temperature: float = 0.7` to `run_prebid_calc_with_modifier` |
| `backend/agents/tournament.py` | Grid expansion, median collapse, updated `AgentResult`/`TournamentResult` |
| `backend/api/routes.py` | Expose `n_samples: int = 2` on `POST /api/tournament/run` |

**No changes to:** `judge.py`, `feedback_loop.py`, `bid_to_win.py`, `upload.py`

---

## Data Structures

### `AgentResult` (additions)

```python
temperature: float = 0.7    # which temperature tier produced this result
sample_index: int = 0        # which repeat within the personality+temp cell
```

### `TournamentResult` (additions)

```python
consensus_entries: list[AgentResult]  # 5 entries, one per personality (median-collapsed)
# entries: list[AgentResult]          # unchanged — all raw results (15–45)
```

---

## Call Stack

```
run_tournament(n_samples=2)
  └── for personality in 5:
        for temperature in [0.3, 0.7, 1.2]:
          for sample_index in range(n_samples):
            → _run_single_agent(..., temperature, sample_index)
                → run_prebid_calc_with_modifier(..., temperature)
                    → call_with_json_retry(..., temperature)
                        → client.messages.create(temperature=temperature)
```

All tasks dispatched in one `asyncio.gather` call — fully parallel.

---

## Temperature Tiers

| Tier | Value | Character |
|------|-------|-----------|
| Low | 0.3 | Deterministic, consistent — favors the most likely estimate |
| Mid | 0.7 | Current default — balanced exploration |
| High | 1.0 | Max allowed by Anthropic API — highest variance, surfaces edge-case interpretations |

---

## Median Collapse (`_collapse_to_consensus`)

After `asyncio.gather` returns all raw results:

1. Group `AgentResult` objects by `agent_name` (personality)
2. Within each group, drop entries with `error` or `total_bid <= 0`
3. Sort remaining entries by `total_bid`
4. Pick the entry whose `total_bid` is closest to the group's median bid value
5. Return that entry as the consensus entry for this personality

Result: 5 `AgentResult` objects — one per personality — stored in `TournamentResult.consensus_entries`.

The judge receives `consensus_entries`, same 5-entry interface as today.

---

## Database

Two new columns added to `tournament_entries` at startup:

```sql
ALTER TABLE tournament_entries ADD COLUMN temperature REAL DEFAULT 0.7;
ALTER TABLE tournament_entries ADD COLUMN is_consensus INTEGER DEFAULT 0;
```

Migration strategy: wrap each `ALTER TABLE` in a try/except for `OperationalError` (SQLite raises this if the column already exists — it does not support `IF NOT EXISTS` on `ALTER TABLE`). Both raw entries and consensus entries are stored; consensus entries have `is_consensus=1`.

---

## API Changes

`POST /api/tournament/run` request body gains:

```json
{
  "n_samples": 2
}
```

- Type: `int`, optional, default `2`, min `1`, max `5`
- Total calls = `5 × 3 × n_samples` (15–75)

Response `TournamentResult` gains `consensus_entries` field alongside existing `entries`.

---

## Error Handling

- If a full personality group has zero valid entries after grid expansion, it is excluded from consensus (same logic as today's `valid_results` filter)
- Individual cell failures are recorded as `AgentResult(error=...)` and excluded from median collapse but kept in `entries` for diagnostics
- If `n_samples=1` and all 3 temps fail for a personality, that personality drops from consensus — judge sees fewer than 5 entries (already handled by judge)

---

## Cost & Latency

| n_samples | API calls | vs. today |
|-----------|-----------|-----------|
| 1 | 15 | 3× |
| 2 (default) | 30 | 6× |
| 3 | 45 | 9× |

Latency impact is minimal — all calls remain parallel. Cost scales linearly with `n_samples`.

---

## Out of Scope

- UI changes to display confidence bands (idea #35 — separate spec)
- Per-trade sub-tournaments (idea #39 — separate spec)
- Cross-provider models (idea #13 — separate spec)
- Changes to judge, feedback loop, or ELO logic
