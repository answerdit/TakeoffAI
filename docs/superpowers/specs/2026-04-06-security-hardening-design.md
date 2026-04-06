# Security Hardening — Design Spec

**Date:** 2026-04-06
**Scope:** All 11 findings from security audit

---

## Summary

Fix all security findings from the 2026-04-06 audit: authentication gaps, error leakage, missing rate limiting, file upload hardening, CORS tightening, HTTPS prep, and minor info-leak / code-smell cleanups.

---

## Fixes

### Fix 1: Auth on all routers (CRITICAL)

**File:** `backend/api/main.py`

Add `dependencies=[Depends(verify_api_key)]` to `upload_router` and `verification_router` when including them in the app. Import `Depends` and `verify_api_key` from routes.

The `/api/health` endpoint is defined directly on `app`, not on any router, so it remains unauthenticated (intentional — health checks must work without auth).

### Fix 2: Fail-closed API key (CRITICAL)

**File:** `backend/api/routes.py`

Change `verify_api_key` so that if `settings.api_key` is empty/unset, the function **rejects** the request with 403 ("API key not configured — set API_KEY in .env"). Previously it silently allowed all requests when no key was configured.

### Fix 3: File size limit (CRITICAL)

**File:** `backend/api/upload.py`

Add a `MAX_UPLOAD_BYTES = 10 * 1024 * 1024` constant (10MB). In each upload endpoint that receives an `UploadFile`, read the file content and check `len(content) > MAX_UPLOAD_BYTES` before parsing. Return 413 if exceeded.

### Fix 4 / Fix 6: Generic error messages (HIGH)

**Files:** `backend/api/routes.py`, `backend/api/upload.py`, `backend/api/verification.py`

Replace every `detail=str(exc)` in 500-level HTTPException handlers with `detail="Internal server error"`. Add `import logging` at the top of each file and `logging.exception("...")` before each raise so the real error is logged server-side.

For 4xx errors (validation, not-found), keep the existing messages — those are intentional user-facing feedback.

### Fix 5: Rate limiting (HIGH)

**Files:** `backend/api/main.py`, `pyproject.toml`

Add `slowapi` dependency. Configure:
- Global default: 60/minute per IP
- `/api/tournament/run`: 10/minute (each call makes 30 Claude API calls)
- `/api/estimate`: 30/minute
- `/api/bid/strategy`: 30/minute

Use `slowapi.Limiter` with `get_remote_address` as key function. Add the `SlowAPIMiddleware` to the app. Add a custom 429 handler.

### Fix 7: Client ID sanitization in uploads (MEDIUM)

**File:** `backend/api/upload.py`

Add the same validation used in `routes.py:210`: `re.match(r'^[a-zA-Z0-9_\-]+$', client_id)`. Return 400 if invalid. Apply to every upload endpoint that accepts `client_id`.

### Fix 8: Tighter CORS (MEDIUM)

**File:** `backend/api/main.py`

Remove the implicit wildcard allowance. Change the CORS setup:
- `ALLOWED_ORIGINS` env var is **required** in non-development mode
- In development mode, default to `http://localhost:3000,http://localhost:5173`
- Never allow `*` with `allow_credentials=True`
- Restrict `allow_headers` to `["Content-Type", "X-API-Key"]` (drop broad `Authorization`)

### Fix 8b: File type content validation (MEDIUM)

**File:** `backend/api/upload.py`

After extension check, read the first few bytes and validate magic bytes:
- CSV: first bytes should be printable ASCII (not binary)
- XLSX: must start with PK zip magic bytes (`\x50\x4b\x03\x04`)

Return 400 with "Invalid file content" if magic bytes don't match.

### Fix 9: Health endpoint info-leak (LOW)

**File:** `backend/api/main.py`

Change the degraded health response from `{"status": "degraded", "reason": "ANTHROPIC_API_KEY not configured"}` to just `{"status": "degraded"}`. Remove the `reason` field that tells attackers what's misconfigured.

### Fix 10: PRAGMA f-string cleanup (LOW)

**File:** `backend/api/main.py`

Change `f"PRAGMA user_version = {version}"` to use a parameterized approach. SQLite PRAGMA doesn't support `?` parameters, so validate that `version` is an int and use `str(int(version))` explicitly to prevent any future injection path.

### Fix 11: HTTPS nginx stub (MEDIUM)

**File:** `nginx.conf`

Add a commented-out SSL server block listening on 443 with Let's Encrypt cert paths (`/etc/letsencrypt/live/...`). Add a redirect from port 80 to 443 (also commented). Include inline instructions for enabling.

---

## Out of Scope

- Session management / JWT (no user sessions exist — API-key-only auth is appropriate for this product stage)
- CSRF tokens (API is JSON-only, no cookie-based auth, SameSite not applicable)
- CSP headers (frontend is a static SPA, no server-rendered HTML)
- Dependency audit (`pip audit` / `uv audit` — separate task)

---

## Testing

Each fix gets at least one test:
- Fix 1: test that upload/verification endpoints return 403 without API key
- Fix 2: test that empty API key rejects requests
- Fix 3: test that oversized upload returns 413
- Fix 5: test that rate limiter returns 429 after burst
- Fix 7: test that malicious client_id in upload returns 400
- Fix 8b: test that binary file renamed to .csv returns 400
- Fixes 4/6/9/10/11: verified by existing tests + manual inspection
