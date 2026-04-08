# TakeoffAI Full Project Audit — Design Spec

**Date:** 2026-04-08
**Scope:** Security & correctness, architecture & code quality, organization & polish
**Output:** PDF audit report + all repairs applied

---

## Part 1 — Security & Correctness (Critical)

Issues that would crash production or create vulnerabilities. Fix immediately.

### 1.1 `bid_to_win.py` is sync but awaited — **will crash**

- **File:** `backend/agents/bid_to_win.py:68`
- **Bug:** `run_bid_to_win()` is a **synchronous** function (uses sync `Anthropic()` client), but `routes.py:142` calls it with `await run_bid_to_win(...)`.
- **Impact:** `TypeError: object dict can't be used in 'await' expression` — the `/api/bid/strategy` endpoint is **completely broken** in production.
- **Fix:** Convert to `async def` + `AsyncAnthropic`, or wrap with `asyncio.to_thread()`.
- **Also:** Function has inline JSON parsing instead of using `call_with_json_retry()` from `utils.py` — no retry on malformed LLM output, no rate-limit handling.
- **Also:** Hardcodes `claude-sonnet-4-5` instead of using `settings.claude_model`.

### 1.2 `verify_api_key` rejects all requests when no `API_KEY` is set

- **File:** `backend/api/routes.py:39-44`
- **Bug:** When `API_KEY` is unset, `configured_key` resolves to `""` (falsy), so the guard raises `403: API key not configured`. The app is **unusable** without setting `API_KEY` in `.env`, even in development.
- **Impact:** Every authenticated endpoint returns 403 out of the box.
- **Fix:** In dev mode (`APP_ENV=development`), skip auth when no key is configured. Add: `if not configured_key and settings.app_env == "development": return`.

### 1.3 `asyncio.run()` inside `record_actual_outcome` — **will crash**

- **File:** `backend/agents/feedback_loop.py:280`
- **Bug:** `asyncio.run(_load_entries())` is called inside `record_actual_outcome()`, which is invoked via `asyncio.to_thread()` from `verification.py:205`. While `to_thread` runs in a separate thread (so `asyncio.run()` works there), this is fragile — if anyone calls `record_actual_outcome` directly from async context, it crashes with `RuntimeError: This event loop is already running`.
- **Fix:** Make `record_actual_outcome` async and use `await` for the DB query. Or document the sync-only contract clearly.

### 1.4 System prompt sent twice in `pre_bid_calc.py` — double token cost

- **File:** `backend/agents/pre_bid_calc.py:214-237`
- **Bug:** `run_prebid_calc_with_modifier()` passes `system=system` to `call_with_json_retry()` AND embeds the full system prompt again as the first content block in `messages[0].content[0].text`. The model sees the entire system prompt twice.
- **Impact:** ~2x token cost on every estimate call. With the material cost table embedded, this is hundreds of extra tokens per request.
- **Fix:** Remove the system prompt from the user message content. Keep only the `system=` parameter. The `cache_control` hint should move to the system parameter level.

### 1.5 CORS missing `DELETE` method

- **File:** `backend/api/main.py:172`
- **Bug:** `allow_methods=["POST", "GET", "PATCH", "OPTIONS"]` — but `routes.py:303` registers `@router.delete("/client/{client_id}/agent-history/{agent_name}")`.
- **Impact:** Browser DELETE requests will be blocked by CORS preflight.
- **Fix:** Add `"DELETE"` to `allow_methods`.

### 1.6 Frontend upload hits nonexistent route

- **File:** `frontend/dist/index.html:1617`
- **Bug:** Frontend sends upload to `/api/upload/bids` but the backend only registers `/api/upload/bids/csv`, `/api/upload/bids/excel`, `/api/upload/bids/manual`. The file upload will always 404.
- **Fix:** The frontend should detect file type and route to `/bids/csv` or `/bids/excel` accordingly. Or add a unified `/bids` endpoint that auto-detects.

### 1.7 Frontend preprocess-pdf uses relative URL instead of `API_BASE`

- **File:** `frontend/dist/index.html:1813`
- **Bug:** `fetch('/api/estimate/preprocess-pdf', ...)` uses a relative path while every other endpoint uses `${API_BASE}/api/...`. When opened as a local `file://`, this will fail silently (no host).
- **Fix:** Use `${API_BASE}/api/estimate/preprocess-pdf`.

### 1.8 `datetime.utcnow()` deprecated — 3 occurrences

- **File:** `backend/agents/feedback_loop.py:28, 72, 155`
- **Bug:** `datetime.utcnow()` is deprecated since Python 3.12, slated for removal. Returns naive datetime.
- **Fix:** Replace with `datetime.now(timezone.utc)` (already used correctly elsewhere in the codebase).

---

## Part 2 — Architecture & Code Quality (Structural)

Inconsistencies, tech debt, and missing patterns. Fix for maintainability.

### 2.1 DB_PATH defined 6 different ways across the codebase

- **Files:** `routes.py`, `main.py`, `tournament.py` use `settings.db_path`; `judge.py`, `feedback_loop.py`, `price_verifier.py`, `verification.py` use `str(Path(...))` to hardcode the path.
- **Impact:** If `db_path` is ever configured differently (e.g., test override, Docker volume), half the app uses the override and half ignores it.
- **Fix:** All files should use `from backend.config import settings` and reference `settings.db_path`. Remove all `str(Path(__file__)...takeoffai.db)` definitions.

### 2.2 Duplicate `_validate_client_id` function

- **Files:** `backend/api/upload.py:24` and `backend/api/verification.py:26` — identical regex logic.
- **Fix:** Move to a shared location (e.g., `routes.py` alongside `verify_api_key`, or a new `backend/api/validators.py`). Import from there.

### 2.3 `bid_to_win.py` doesn't use shared `call_with_json_retry`

- **File:** `backend/agents/bid_to_win.py:93-106`
- **Issue:** Has its own inline JSON-fence-stripping and `json.loads()` with no retry, no rate-limit backoff, no error handling. Every other agent uses `utils.py`.
- **Fix:** Refactor to use `call_with_json_retry()` after converting to async.

### 2.4 Material costs loaded once at import — never refreshed

- **File:** `backend/agents/pre_bid_calc.py:31`
- **Issue:** `_MATERIAL_COSTS = _load_material_costs()` runs once at import time. When PriceVerifier updates `material_costs.csv`, the cost table in the system prompt is stale until server restart.
- **Fix:** Reload the CSV on each call (it's tiny — ~50 rows), or add a cache with a short TTL.

### 2.5 `price_verifier.py` uses synchronous `Anthropic()` client

- **File:** `backend/agents/price_verifier.py:22`
- **Issue:** Uses sync `anthropic_client = Anthropic()` in `_fetch_supplier_price()` and `_web_search_price()` — but these are `async def` functions. The sync `.messages.create()` calls block the event loop.
- **Fix:** Switch to `AsyncAnthropic()` and `await client.messages.create(...)`.

### 2.6 `wiki_manager.py` is 730 lines — largest file by 2x

- **File:** `backend/agents/wiki_manager.py`
- **Issue:** Contains job CRUD, client pages, personality pages, material pages, lint, synthesis, frontmatter parsing — too many concerns.
- **Recommendation:** Split into `wiki_core.py` (frontmatter, synthesis, write helpers), `wiki_jobs.py` (job CRUD + enrichment), `wiki_lint.py` (lint logic). Keep the public API surface unchanged via `__init__.py` re-exports.

### 2.7 `feedback_loop.py` has two `DB_PATH` assignments

- **File:** `backend/agents/feedback_loop.py:11` (PROFILES_DIR) and `:228` (DB_PATH)
- **Issue:** The second `DB_PATH` is defined 220 lines below the imports, easy to miss. The calibration section was clearly appended later.
- **Fix:** Move `DB_PATH` to the top alongside other module-level constants, use `settings.db_path`.

### 2.8 No input size limit on `BidStrategyRequest.estimate`

- **File:** `backend/api/routes.py:72`
- **Issue:** `estimate: dict = Field(...)` — accepts any size dict. A malicious or buggy client could send a massive JSON payload.
- **Fix:** Add a reasonable max size validator or limit the depth/keys accepted.

### 2.9 `harness_evolver.py` commits to git without user confirmation

- **File:** `backend/agents/harness_evolver.py:82-94`
- **Issue:** `_git_commit()` automatically stages and commits changes to `tournament.py`. In a customer-deployed scenario, this modifies source code autonomously.
- **Recommendation:** Document this clearly. Consider a dry-run mode that proposes changes without committing.

---

## Part 3 — Organization & Polish (Cleanup)

Stale docs, inconsistencies, and cleanup for professionalism.

### 3.1 `ARCHITECTURE.md` is stale

- **File:** `docs/ARCHITECTURE.md:27`
- **Issues:**
  - Says "React Frontend" — it's a static HTML file, no React
  - Says "localhost:3000" — frontend is opened as a local file or served separately
  - Missing: wiki system, upload system, blueprint PDF, scheduler
  - Missing: `wiki_routes.py`, `upload.py` routers
- **Fix:** Rewrite to reflect current architecture.

### 3.2 Orphan/junk files in project root

- `Untitled.md` — empty/placeholder, should be removed
- `TakeoffAI — User Guide for Construction Companies.gdoc` — Google Drive link file, gitignored by pattern but still visible
- `Client Estimates/Unconfirmed 441333.crdownload` — partial download artifact
- `.obsidian/` — Obsidian vault config, should be gitignored

### 3.3 Frontend is a 1,836-line monolith

- **File:** `frontend/dist/index.html`
- **Issue:** CSS (~700 lines), HTML (~400 lines), and JS (~700 lines) all in one file. Hard to maintain.
- **Recommendation:** For now, add section comments and organize logically. Full extraction to separate files is a larger task (not blocking).

### 3.4 `__pycache__` directories tracked in git status

- Multiple `__pycache__/` directories show up. While `.gitignore` covers `__pycache__/`, they may have been committed previously.
- **Fix:** Verify they're not tracked. If tracked, `git rm -r --cached`.

### 3.5 Missing test for `bid_to_win.py` as standalone

- **File:** `tests/` directory
- **Issue:** No `test_bid_to_win.py`. The bid strategy endpoint is tested through `test_routes.py` but the agent itself has no unit tests.
- **Fix:** Add `test_bid_to_win.py` with mocked Anthropic client.

### 3.6 `slowapi` deprecation warning

- **Warning:** `asyncio.iscoroutinefunction` is deprecated in Python 3.14, used by slowapi.
- **Impact:** 6 warnings on every test run. Will break in Python 3.16.
- **Recommendation:** Pin slowapi or check for updated version. Low priority — upstream fix.

### 3.7 `.env.template` completeness

- **File:** `.env.template`
- **Check:** Ensure all env vars from `config.py` and `CLAUDE.md` table are represented.

### 3.8 PDF audit report generation

- Use `reportlab` (already a dependency) to generate a professional PDF summarizing all findings, fixes applied, and remaining recommendations.
- Output to `docs/TakeoffAI-Audit-Report.pdf`.

---

## Implementation Order

1. **Part 1 fixes** — all 8 items (security/correctness)
2. **Part 2 fixes** — items 2.1–2.5, 2.7–2.8 (code quality, skip 2.6 wiki split and 2.9 harness dry-run for now)
3. **Part 3 fixes** — items 3.1–3.2, 3.4–3.5, 3.7 (cleanup)
4. **Run full test suite** — verify 141 tests still pass + new tests
5. **Generate PDF report** — summarize findings, fixes, and recommendations

---

## Out of Scope

- Full frontend extraction to separate CSS/JS files (flag for future)
- Wiki manager split (flag for future — works fine at current scale)
- Harness evolver dry-run mode (flag for future)
- API versioning (premature — no external consumers yet)
- Production deployment config (Docker/Railway — separate task)
