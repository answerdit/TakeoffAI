# Harness Evolution — Design Spec

**Date:** 2026-03-31
**Status:** Approved

---

## Goal

Automatically improve the `PERSONALITY_PROMPTS` in `tournament.py` by observing which agent personalities win bids and evolving the underperforming ones using Claude as a proposer. Inspired by the Meta-Harness paper (yoonholee.com/meta-harness/): give the proposer full diagnostic context so it can do root-cause analysis, not just blind rewrites.

---

## Scope

- Evolves **underperforming** agent personalities only (surgical, not full rewrite)
- Prompts apply **globally** (same for all clients)
- Triggered **manually** via API or **automatically** when one agent dominates
- Version history via **git commits** — rollback and fork are native git operations
- One evolution at a time (async lock prevents concurrent calls)

---

## Components

| File | Change | Role |
|---|---|---|
| `backend/agents/harness_evolver.py` | CREATE | Core logic: context assembly, Claude API call, regex rewrite, git commit |
| `backend/api/routes.py` | MODIFY | Add `POST /api/tournament/evolve` endpoint |
| `backend/agents/feedback_loop.py` | MODIFY | Add `check_dominance()` called after each judge; auto-triggers evolver |

---

## Data Flow

```
judge_tournament()
  → check_dominance(client_id)         # feedback_loop.py
    → if dominant: evolve_harness()    # harness_evolver.py
        → read client profile (diagnostic context)
        → identify underperforming agents
        → call Claude API with context + current prompts
        → parse JSON response
        → regex-replace only underperforming agents in tournament.py
        → git commit with structured message

POST /api/tournament/evolve
  → evolve_harness(client_id)          # same path, manual trigger
```

---

## Diagnostic Context (sent to Claude)

Claude receives a structured prompt containing:

```
Current PERSONALITY_PROMPTS for underperforming agents (full text)

Win rates by agent:
  conservative:     12%
  balanced:          8%
  aggressive:       62%   ← dominant
  historical_match: 10%
  market_beater:     8%

Deviation history (last N jobs per agent):
  conservative:     [4.2, 6.1, 5.8, ...]
  balanced:         [2.1, 3.4, ...]
  ...

Last 5 winning examples:
  [{agent: aggressive, total_bid: 142000, margin_pct: 11.2, ...}, ...]

Avg winning margin: 11.8%
Dominant agent: aggressive (62% win rate)
Agents to improve: conservative, balanced, historical_match, market_beater

Task: Rewrite only the listed agents' personality prompts to make them more
competitive. Return a JSON dict mapping agent name → new prompt string.
Do not change the aggressive prompt.
```

**Claude model:** `claude-sonnet-4-6` (configurable via `HARNESS_EVOLVER_MODEL` env var)

**Claude's response format:**
```json
{
  "conservative": "## BIDDING PERSONALITY: CONSERVATIVE\n...",
  "balanced": "## BIDDING PERSONALITY: BALANCED\n..."
}
```

Only agents listed in `agents_to_improve` are included. The regex rewriter replaces only those agents' triple-quoted strings in `tournament.py`.

---

## Versioning

Each evolution commits `tournament.py` with:
```
harness: gen-{N} — evolved {agent_list} (dominant: {agent} at {pct}%)
```

Examples:
```
harness: gen-1 — evolved conservative,balanced (dominant: aggressive at 62%)
harness: gen-2 — evolved market_beater (dominant: historical_match at 65%)
```

Generation number = `git log --oneline --grep="harness: gen" | wc -l`

**Rollback:**
```bash
git checkout <commit-hash> -- backend/agents/tournament.py
```

**Fork from gen-2:**
```bash
git checkout -b harness/gen-2-fork <commit-hash>
```

---

## Auto-Trigger Logic

`check_dominance(client_id)` runs after every `judge_tournament()` call.

Fires `evolve_harness()` when **both** conditions are met:
- `total_tournaments >= MIN_TOURNAMENTS_BEFORE_EVOLVE` (default: **10**)
- Any single agent `win_rate > DOMINANCE_THRESHOLD` (default: **0.60**)

Both constants live in `harness_evolver.py` for easy tuning.

**Concurrency:** An `asyncio.Lock` (`_evolution_lock`) prevents concurrent evolutions. If evolution is already in progress when auto-trigger fires, the trigger is silently skipped (not queued).

---

## API Endpoint

```
POST /api/tournament/evolve
Body: { "client_id": "default" }

Response 200:
{
  "status": "evolved",
  "generation": 3,
  "evolved_agents": ["conservative", "balanced"],
  "dominant_agent": "aggressive",
  "dominant_win_rate": 0.62,
  "commit": "abc1234"
}

Response 200 (no evolution needed):
{
  "status": "skipped",
  "reason": "no dominance detected",
  "win_rates": { ... }
}

Response 423 (lock busy):
{
  "detail": "Evolution already in progress"
}
```

---

## Error Handling

| Failure | Behavior |
|---|---|
| Claude API error | Raise HTTPException 500, do not modify tournament.py |
| Claude returns invalid JSON | Raise HTTPException 500, log raw response |
| Claude returns unknown agent names | Skip unknown keys, only apply known ones |
| git commit fails | Log error; `tournament.py` is already rewritten on disk — return response with `"commit": null` |
| Fewer than MIN_TOURNAMENTS data | Return `{"status": "skipped", "reason": "insufficient data"}` |

---

## Testing

- `test_harness_evolver.py` (new file)
- `test_evolve_endpoint_returns_skipped_on_no_dominance` — mock profile with balanced win rates
- `test_evolve_endpoint_evolves_underperforming_agents` — mock Claude response, verify tournament.py updated
- `test_evolve_endpoint_returns_423_when_lock_held` — acquire lock, call endpoint, expect 423
- `test_check_dominance_triggers_evolution` — mock judge flow end-to-end
- `test_check_dominance_skips_below_threshold` — 9 tournaments, no trigger
