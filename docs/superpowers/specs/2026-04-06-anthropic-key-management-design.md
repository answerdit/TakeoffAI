# Anthropic Key Management — Design Spec

**Date:** 2026-04-06
**Status:** Approved

---

## Overview

Allow contractors to provide their own Anthropic API key through the TakeoffAI UI. Their usage bills directly to their Anthropic account. The header shows a live status indicator (red / yellow / normal) and a direct link to the Anthropic console to obtain a key.

This is distinct from the existing TakeoffAI auth key (`X-API-Key` header field) which authenticates requests to TakeoffAI's own backend. The new field is for the Anthropic API key used to call Claude.

---

## Goals

1. Contractors paste their Anthropic API key once — it's validated and saved to backend config.
2. All subsequent Claude API calls use the contractor's key automatically.
3. The header field gives instant visual feedback: red (missing), yellow (invalid), normal (working).
4. A "Get key →" button opens the Anthropic console keys page directly.

---

## Architecture

### New backend endpoint

**`POST /api/settings/anthropic-key`**

```json
{ "key": "sk-ant-api03-..." }
```

- Validates the key format (`sk-ant-` prefix, minimum length check).
- Makes a minimal live Anthropic call (`client.models.list()`) to confirm the key is active. No tokens are charged.
- If valid: writes `ANTHROPIC_API_KEY=<key>` to `.env`, hot-reloads `settings.anthropic_api_key` in the running process.
- Returns:

```json
{ "status": "ok" }           // key valid and saved
{ "status": "invalid" }      // key failed validation (bad format or API rejected)
{ "status": "error", "detail": "..." }  // unexpected failure
```

Auth: `X-API-Key`. No rate limit beyond the global 60/min default.

### Hot-reload mechanism

`settings` is a pydantic-settings singleton. After writing to `.env`, the endpoint calls:

```python
import importlib, backend.config as _cfg
_cfg.settings.anthropic_api_key = new_key
```

All agents read `settings.anthropic_api_key` at call time (not at module import), so updating the singleton is sufficient. No server restart needed.

### Agent call pattern

Every agent currently instantiates `Anthropic()` or `AsyncAnthropic()` at module level (reads `ANTHROPIC_API_KEY` env var at import time). These will be changed to instantiate clients **per-call** using `settings.anthropic_api_key` directly, so the hot-reloaded value is always used.

---

## Frontend Changes

### Header — new Anthropic key field

Added to the `<nav>` in the header, to the right of the existing TakeoffAI API Key field:

```
[ API Key ▒▒▒▒▒▒ ]   [ Anthropic Key ▒▒▒▒▒▒▒ ]  [Get key →]   BETA
```

- `id="hdr-anthropic-key"` — `type="password"`, monospace, same style as existing field
- Color states applied via `border-color` and `box-shadow`:
  - **Red** (`#ff4d4d`) — field is empty
  - **Yellow** (`#f0c040`) — key entered but validation returned `invalid`
  - **Default border** — key validated successfully
- **"Get key →"** — plain `<a>` tag, `target="_blank"`, href `https://console.anthropic.com/settings/keys`

### Validation trigger

On `blur` (field loses focus) or `Enter` key: frontend POSTs to `POST /api/settings/anthropic-key`. Updates border color based on response `status`. Shows no modal or blocking UI — just the color change.

On page load: frontend reads the current key status via `GET /api/settings/anthropic-key-status` and sets the initial color.

### `GET /api/settings/anthropic-key-status`

Returns the status of the currently configured key without exposing it:

```json
{ "status": "ok" | "invalid" | "missing" }
```

Used only on page load to set initial field color. The key value itself is never returned to the frontend.

---

## Files Changed

| File | Change |
|---|---|
| `backend/api/routes.py` | Add `POST /api/settings/anthropic-key` and `GET /api/settings/anthropic-key-status` endpoints |
| `backend/agents/bid_to_win.py` | Move `Anthropic()` client from module-level to per-call |
| `backend/agents/price_verifier.py` | Move `Anthropic()` client from module-level to per-call |
| `backend/agents/harness_evolver.py` | Move `anthropic.Anthropic()` client from local to per-call |
| `frontend/dist/index.html` | Add Anthropic key field + status colors + "Get key →" link to header |
| `tests/test_routes.py` | Add tests for both new endpoints |

**Not changed:** `pre_bid_calc.py`, `utils.py`, `tournament.py`, `wiki_manager.py` — these already instantiate clients per-call or pass them as arguments.

---

## Security

- The Anthropic key is written to `.env` on disk (already the existing pattern for `ANTHROPIC_API_KEY`).
- The key is never returned to the frontend — status endpoint returns only `ok / invalid / missing`.
- Format validation (`sk-ant-` prefix) happens before any live API call.
- The endpoint requires `X-API-Key` auth (same as all other endpoints).

---

## Out of Scope

- Multiple contractor key profiles
- Key rotation / expiry detection
- Encrypting the key at rest (`.env` file permissions is the security model)
- UI changes beyond the header field
