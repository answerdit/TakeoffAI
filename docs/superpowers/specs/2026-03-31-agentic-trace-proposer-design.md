# Agentic Trace Proposer — Design Spec

**Date:** 2026-03-31
**Status:** Approved

---

## Goal

Upgrade the harness evolver's proposer from a single-shot summarized-context call to a full agentic loop that navigates raw per-tournament trace files using tool calls. Inspired by the Meta-Harness paper's core insight: giving the proposer filesystem-level diagnostic context (what each agent actually bid on each job, line by line) produces targeted, evidence-grounded rewrites rather than generic ones.

---

## Problem With Current Approach

`_call_claude_sync` assembles a fixed context string (win rates, last 5 examples as JSON) and sends it in one shot. The proposer diagnoses from aggregates — it can see that `conservative` wins 12% of the time but cannot read what `conservative` actually estimated on job #47 vs. what `aggressive` estimated, or why one won. This is equivalent to OPRO, the baseline the Meta-Harness paper beats. The paper showed that access to raw execution traces is the primary driver of improvement.

---

## Scope

- Adds trace file persistence alongside existing SQLite writes (no schema changes)
- Replaces `_call_claude_sync` with `_run_agentic_proposer` in `harness_evolver.py`
- Everything else unchanged: lock, dominance check, regex rewrite, git commit, `/api/tournament/evolve` endpoint
- No new files — modifications only

---

## Components

| File | Change | Detail |
|---|---|---|
| `backend/agents/tournament.py` | MODIFY | Write trace files in `_save_entries` after DB insert |
| `backend/agents/harness_evolver.py` | MODIFY | Add `HARNESS_EVOLVER_MAX_TOOL_CALLS` env var, replace `_call_claude_sync` with `_run_agentic_proposer`, add tool definitions + handler |

---

## Trace File Format

**Location:** `backend/data/traces/{tournament_id}/{agent_name}.json`

**Written by:** `_save_entries` in `tournament.py`, immediately after the DB insert loop.

**Content:**
```json
{
  "tournament_id": 47,
  "agent_name": "conservative",
  "client_id": "acme_roofing",
  "project_description": "Replace flat roof on 8,000 sqft warehouse...",
  "zip_code": "76801",
  "won": false,
  "score": null,
  "timestamp": "2026-03-31T14:22:01Z",
  "estimate": {
    "project_summary": "...",
    "location": "...",
    "line_items": [
      {
        "description": "TPO membrane, 60mil",
        "quantity": 8000,
        "unit": "sqft",
        "unit_material_cost": 1.85,
        "unit_labor_cost": 0.95,
        "total_material": 14800.00,
        "total_labor": 7600.00,
        "subtotal": 22400.00
      }
    ],
    "subtotal": 0.0,
    "overhead_pct": 20,
    "overhead_amount": 0.0,
    "margin_pct": 12,
    "margin_amount": 0.0,
    "total_bid": 0.0,
    "confidence": "high",
    "notes": "..."
  }
}
```

`won` and `score` are `false`/`null` at write time (entries are saved before judging). The judge updates the DB row but does not rewrite the trace file — the proposer uses win history from the client profile JSON for the aggregate view and reads trace files for the bid anatomy.

The trace directory is created automatically (`Path.mkdir(parents=True, exist_ok=True)`). Write failures are logged but do not raise — trace persistence is best-effort and must not break tournament execution.

---

## Agentic Proposer

### Configuration

```python
HARNESS_EVOLVER_MAX_TOOL_CALLS = int(os.getenv("HARNESS_EVOLVER_MAX_TOOL_CALLS", "30"))
```

Sits alongside the existing `HARNESS_EVOLVER_MODEL` env var. Operators can tune via environment without touching code.

### Tools

Two tools, both read-only:

**`list_traces`**
```json
{
  "name": "list_traces",
  "description": "List available trace files for a client. Returns file paths with metadata (agent_name, tournament_id, total_bid, timestamp). Use to find which tournaments to investigate.",
  "input_schema": {
    "type": "object",
    "properties": {
      "client_id": {"type": "string"},
      "agent_name": {"type": "string", "description": "Filter by agent name (optional)"},
      "limit": {"type": "integer", "default": 50}
    },
    "required": ["client_id"]
  }
}
```

Returns a list of dicts: `[{"path": "backend/data/traces/47/conservative.json", "agent_name": "conservative", "tournament_id": 47, "total_bid": 142000.0, "timestamp": "..."}]`

Implementation: glob `backend/data/traces/*/{agent_name or *}.json`, filter by client_id from file content (read just the `client_id` field), sort by tournament_id descending, return up to `limit`.

**`read_file`**
```json
{
  "name": "read_file",
  "description": "Read a file from the data directory. Use to read trace files or the client profile. Path must be under backend/data/.",
  "input_schema": {
    "type": "object",
    "properties": {
      "path": {"type": "string", "description": "Relative or absolute path. Must be under backend/data/."}
    },
    "required": ["path"]
  }
}
```

**Path sandboxing:** Resolve the path to absolute. If it does not start with the canonical `backend/data/` absolute path, return `{"error": "Access denied: path must be under backend/data/"}` as the tool result — do not raise. This is returned to Claude as a tool_result, not an exception.

### Agentic Loop

`_run_agentic_proposer(client_id, underperforming, dominant_agent, dominant_rate)` replaces `_call_claude_sync`. Runs inside `asyncio.to_thread` (same as today) so it doesn't block the event loop.

**System prompt:**
```
You are a harness optimization agent for a construction bidding AI system.
The system runs 5 bidding personalities in parallel on each job. You must improve
the underperforming personalities by finding concrete evidence of why they lose.

Use list_traces to find relevant tournaments, read_file to examine bid breakdowns
in detail, and read the client profile for aggregate win rates and history.

When you have sufficient evidence, output ONLY a valid JSON object mapping
agent name to new prompt string. Include only the agents you were asked to improve.
```

**Initial user message:** Provides client_id, underperforming agents, dominant agent + win rate, and the path to the client profile JSON (`backend/data/client_profiles/{client_id}.json`).

**Loop:**
1. Call `client.messages.create(model=..., tools=[...], messages=[...])`
2. If `stop_reason == "tool_use"`: execute each tool call, append `tool_result` blocks to messages, increment tool call counter, repeat
3. If tool call counter exceeds `HARNESS_EVOLVER_MAX_TOOL_CALLS`: append a final user message — `"You have enough context. Output your proposed prompts now as a JSON object."` — allow one more `messages.create` call (without tools, `tool_choice="none"`), then exit loop regardless
4. If `stop_reason == "end_turn"`: extract text from final assistant message, parse JSON (with existing markdown-wrapped fallback), return proposed dict

The parsed JSON flows into the existing `valid_proposed` filtering, `_replace_prompt_in_source`, and git commit — unchanged.

---

## Data Flow

```
run_tournament()
  → _save_entries()
      → INSERT INTO tournament_entries   (existing)
      → write backend/data/traces/{id}/{agent}.json   (new)

evolve_harness()
  → [lock, skip checks — unchanged]
  → _run_agentic_proposer(client_id, underperforming, ...)   (replaces _call_claude_sync)
      → agentic loop:
          list_traces → read_file → read_file → ... → end_turn
      → parse JSON response
  → _replace_prompt_in_source()   (unchanged)
  → _git_commit()   (unchanged)
```

---

## Error Handling

| Failure | Behavior |
|---|---|
| Trace write fails | Log warning, continue — must not break tournament |
| Tool call returns path-sandboxing error | Claude receives `{"error": "..."}` as tool_result, continues loop |
| Loop exceeds `MAX_TOOL_CALLS` | Inject final user message, force one more response |
| Final response not valid JSON | Existing `ValueError` path — raises, routes.py returns 500 |
| `list_traces` finds no files | Returns empty list — Claude receives `[]`, can still read client profile |

---

## Testing

**`test_agentic_trace_proposer.py`** (new file alongside `test_harness_evolver.py`)

- `test_trace_files_written_on_tournament_run` — mock `_save_entries`, verify trace files created at correct paths with correct content
- `test_trace_write_failure_does_not_break_tournament` — mock file write to raise OSError, verify tournament completes normally
- `test_list_traces_returns_matching_files` — write sample trace files to tmp_path, call handler, verify correct paths returned
- `test_read_file_sandboxing_blocks_outside_data_dir` — call handler with `../../backend/agents/tournament.py`, verify `{"error": "..."}` returned
- `test_agentic_proposer_multi_turn_tool_loop` — mock `messages.create` to return: (1) tool_use for list_traces, (2) tool_use for read_file, (3) end_turn with JSON — verify all 3 responses processed and proposed prompts extracted
- `test_agentic_proposer_soft_cap_forces_proposal` — mock to return 31 consecutive tool_use responses, verify injection message sent and loop terminates
- `test_evolve_harness_end_to_end_with_agentic_proposer` — full path: trace files in tmp_path, mock messages.create for agentic loop, verify tournament.py rewritten and result dict correct
