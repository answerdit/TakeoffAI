# Security Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix all 11 security findings from the 2026-04-06 audit — authentication gaps, error leakage, missing rate limiting, file upload hardening, CORS tightening, HTTPS prep, and minor info-leak / code-smell cleanups.

**Architecture:** Defense-in-depth layering: fail-closed auth on every router, generic error messages with server-side logging, rate limiting via slowapi middleware, file upload validation (size + magic bytes + client_id sanitization), tighter CORS, and HTTPS-ready nginx config.

**Tech Stack:** FastAPI, slowapi, aiosqlite, pytest, httpx (test client), nginx

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `backend/api/routes.py` | Modify | Fix 2 (fail-closed auth), Fix 4 (generic errors) |
| `backend/api/main.py` | Modify | Fix 1 (auth on routers), Fix 5 (rate limiting), Fix 8 (CORS), Fix 9 (health info-leak), Fix 10 (PRAGMA cleanup) |
| `backend/api/upload.py` | Modify | Fix 3 (file size), Fix 6 (generic errors), Fix 7 (client_id sanitize), Fix 8b (magic bytes) |
| `backend/api/verification.py` | Modify | Fix 6 (generic errors) |
| `nginx.conf` | Modify | Fix 11 (HTTPS stub) |
| `pyproject.toml` | Modify | Fix 5 (add slowapi dep) |
| `tests/test_security.py` | Create | All security tests |

---

### Task 1: Fail-Closed Auth + Auth on All Routers (Fixes 1, 2)

**Files:**
- Modify: `backend/api/routes.py:32-34`
- Modify: `backend/api/main.py:156-158`
- Create: `tests/test_security.py`

- [ ] **Step 1: Write failing tests for fail-closed auth and router auth**

Create `tests/test_security.py`:

```python
"""Security hardening tests — auth, error handling, uploads, rate limiting."""

import pytest
from httpx import AsyncClient, ASGITransport

from backend.api.main import app


# ── Fix 1: Auth on all routers ──────────────────────────────────────────────

@pytest.mark.anyio
async def test_upload_endpoint_requires_api_key(monkeypatch):
    """Upload endpoints should return 403 without a valid API key."""
    monkeypatch.setenv("API_KEY", "test-secret-key")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post("/api/upload/bids/manual", json={"client_id": "x", "bids": []})
    assert resp.status_code == 403


@pytest.mark.anyio
async def test_verification_endpoint_requires_api_key(monkeypatch):
    """Verification endpoints should return 403 without a valid API key."""
    monkeypatch.setenv("API_KEY", "test-secret-key")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post("/api/verify/estimate", json={"line_items": []})
    assert resp.status_code == 403


# ── Fix 2: Fail-closed API key ─────────────────────────────────────────────

@pytest.mark.anyio
async def test_empty_api_key_rejects_requests(monkeypatch):
    """When API_KEY env var is empty, all authenticated endpoints must reject."""
    monkeypatch.setenv("API_KEY", "")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post("/api/estimate", json={
            "description": "Build a warehouse",
            "zip_code": "75001",
        })
    assert resp.status_code == 403


@pytest.mark.anyio
async def test_health_remains_unauthenticated(monkeypatch):
    """Health endpoint must work without any API key."""
    monkeypatch.setenv("API_KEY", "test-secret-key")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/api/health")
    assert resp.status_code == 200
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/bevo/Documents/answerD.it/TakeoffAI && python -m pytest tests/test_security.py::test_upload_endpoint_requires_api_key tests/test_security.py::test_empty_api_key_rejects_requests tests/test_security.py::test_health_remains_unauthenticated -v`

Expected: `test_upload_endpoint_requires_api_key` FAIL (currently no auth on upload_router), `test_empty_api_key_rejects_requests` FAIL (currently allows empty key), `test_health_remains_unauthenticated` PASS (health is already unauthenticated).

- [ ] **Step 3: Fix fail-closed auth in routes.py**

In `backend/api/routes.py`, replace lines 32-34:

```python
async def verify_api_key(key: str = Security(_api_key_header)):
    if settings.api_key and key != settings.api_key:
        raise HTTPException(status_code=403, detail="Invalid or missing API key")
```

with:

```python
async def verify_api_key(key: str = Security(_api_key_header)):
    if not settings.api_key:
        raise HTTPException(status_code=403, detail="API key not configured")
    if key != settings.api_key:
        raise HTTPException(status_code=403, detail="Invalid or missing API key")
```

- [ ] **Step 4: Add auth dependencies to upload and verification routers in main.py**

In `backend/api/main.py`, add `Depends` to the imports on line 6:

```python
from fastapi import Depends, FastAPI
```

Add the import of `verify_api_key` after line 10:

```python
from backend.api.routes import router, verify_api_key
```

Replace lines 156-158:

```python
app.include_router(router, prefix="/api")
app.include_router(upload_router, prefix="/api")
app.include_router(verification_router, prefix="/api")
```

with:

```python
app.include_router(router, prefix="/api")
app.include_router(upload_router, prefix="/api", dependencies=[Depends(verify_api_key)])
app.include_router(verification_router, prefix="/api", dependencies=[Depends(verify_api_key)])
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /Users/bevo/Documents/answerD.it/TakeoffAI && python -m pytest tests/test_security.py -v`

Expected: All 4 tests PASS.

- [ ] **Step 6: Run existing test suite to check for regressions**

Run: `cd /Users/bevo/Documents/answerD.it/TakeoffAI && python -m pytest tests/ -v --timeout=30`

Expected: Some existing tests that don't send an API key may now fail (e.g., `test_health_endpoints.py` tests that hit authenticated routes). Those tests need an `X-API-Key` header added — note any failures for fixing in this step.

If existing tests fail because they now need auth, add `monkeypatch.setenv("API_KEY", "")` or set API_KEY and pass the header. The `test_health_endpoints.py::test_post_verify_run_returns_500_on_error` test checks `detail` contains `"network timeout"` — this will break after Fix 4 (generic errors). That test gets updated in Task 2.

- [ ] **Step 7: Commit**

```bash
git add backend/api/routes.py backend/api/main.py tests/test_security.py
git commit -m "sec: fail-closed API key + auth on upload/verification routers (Fixes 1, 2)"
```

---

### Task 2: Generic Error Messages (Fixes 4, 6)

**Files:**
- Modify: `backend/api/routes.py`
- Modify: `backend/api/upload.py`
- Modify: `backend/api/verification.py`
- Modify: `tests/test_security.py`

- [ ] **Step 1: Write failing test for generic error messages**

Append to `tests/test_security.py`:

```python
# ── Fixes 4/6: Generic error messages ──────────────────────────────────────

@pytest.mark.anyio
async def test_500_error_does_not_leak_details(monkeypatch):
    """500 responses must say 'Internal server error', not the raw exception."""
    monkeypatch.setenv("API_KEY", "test-key")
    from unittest.mock import AsyncMock, patch
    with patch("backend.agents.pre_bid_calc.run_prebid_calc",
               new=AsyncMock(side_effect=RuntimeError("secret DB connection string leaked"))):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post(
                "/api/estimate",
                json={"description": "Build a 10000 sqft warehouse", "zip_code": "75001"},
                headers={"X-API-Key": "test-key"},
            )
    assert resp.status_code == 500
    assert "secret" not in resp.json()["detail"]
    assert resp.json()["detail"] == "Internal server error"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/bevo/Documents/answerD.it/TakeoffAI && python -m pytest tests/test_security.py::test_500_error_does_not_leak_details -v`

Expected: FAIL — currently `detail=str(exc)` leaks the raw exception.

- [ ] **Step 3: Fix routes.py — replace all detail=str(exc) in 500 handlers**

In `backend/api/routes.py`, add `import logging` at the top (after line 8, before the `from` imports):

```python
import logging
```

Then replace every `raise HTTPException(status_code=500, detail=str(exc)) from exc` pattern with:

```python
logging.exception("...")
raise HTTPException(status_code=500, detail="Internal server error") from exc
```

Specifically, the replacements in `routes.py`:

Line 82 (estimate endpoint):
```python
    except Exception as exc:
        logging.exception("estimate failed")
        raise HTTPException(status_code=500, detail="Internal server error") from exc
```

Line 97 (bid_strategy endpoint):
```python
    except Exception as exc:
        logging.exception("bid strategy failed")
        raise HTTPException(status_code=500, detail="Internal server error") from exc
```

Line 158-159 (tournament_run endpoint):
```python
    except Exception as exc:
        logging.exception("tournament run failed")
        raise HTTPException(status_code=500, detail="Internal server error") from exc
```

Line 175-176 (tournament_judge endpoint — keep 404 ValueError handler, fix 500):
```python
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logging.exception("tournament judge failed")
        raise HTTPException(status_code=500, detail="Internal server error") from exc
```

Line 203 (tournament_get endpoint):
```python
    except Exception as exc:
        logging.exception("tournament get failed")
        raise HTTPException(status_code=500, detail="Internal server error") from exc
```

Line 221 (client_profile endpoint):
```python
    except Exception as exc:
        logging.exception("client profile failed")
        raise HTTPException(status_code=500, detail="Internal server error") from exc
```

Line 235 (exclude_agent endpoint):
```python
    except Exception as exc:
        logging.exception("exclude agent failed")
        raise HTTPException(status_code=500, detail="Internal server error") from exc
```

Line 244 (reset_agent_history endpoint):
```python
    except Exception as exc:
        logging.exception("reset agent history failed")
        raise HTTPException(status_code=500, detail="Internal server error") from exc
```

Lines 267-269 (evolve_harness endpoint):
```python
    except ValueError as exc:
        logging.exception("evolve harness failed")
        raise HTTPException(status_code=500, detail="Internal server error") from exc
    except Exception as exc:
        logging.exception("evolve harness failed")
        raise HTTPException(status_code=500, detail="Internal server error") from exc
```

- [ ] **Step 4: Fix upload.py — replace detail=str(exc) in 500 handlers**

In `backend/api/upload.py`, add `import logging` at the top (after line 1, the docstring):

```python
import logging
```

Replace 500-level handlers. Keep 4xx messages as-is (they are intentional user-facing validation feedback).

Line 166 (upload_csv):
```python
    except Exception as exc:
        logging.exception("CSV import failed")
        raise HTTPException(status_code=500, detail="Internal server error") from exc
```

Line 193 (upload_excel):
```python
    except Exception as exc:
        logging.exception("Excel import failed")
        raise HTTPException(status_code=500, detail="Internal server error") from exc
```

Line 237 (upload_manual):
```python
    except Exception as exc:
        logging.exception("manual import failed")
        raise HTTPException(status_code=500, detail="Internal server error") from exc
```

- [ ] **Step 5: Fix verification.py — replace detail=str(exc) in 500 handlers**

In `backend/api/verification.py`, add `import logging` at the top:

```python
import logging
```

Replace all 500-level handlers. Keep 404 ValueError handlers as-is.

Line 58 (verify_estimate):
```python
    except Exception as exc:
        logging.exception("verify estimate failed")
        raise HTTPException(status_code=500, detail="Internal server error") from exc
```

Line 102 (list_audit):
```python
    except Exception as exc:
        logging.exception("list audit failed")
        raise HTTPException(status_code=500, detail="Internal server error") from exc
```

Line 125 (list_queue):
```python
    except Exception as exc:
        logging.exception("list queue failed")
        raise HTTPException(status_code=500, detail="Internal server error") from exc
```

Line 184 (resolve_queue_item):
```python
    except Exception as exc:
        logging.exception("resolve queue item failed")
        raise HTTPException(status_code=500, detail="Internal server error") from exc
```

Line 208 (submit_outcome — keep ValueError 404):
```python
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logging.exception("submit outcome failed")
        raise HTTPException(status_code=500, detail="Internal server error") from exc
```

Line 221 (get_accuracy — keep ValueError 404):
```python
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logging.exception("get accuracy failed")
        raise HTTPException(status_code=500, detail="Internal server error") from exc
```

Line 231 (run_verification):
```python
    except Exception as exc:
        logging.exception("run verification failed")
        raise HTTPException(status_code=500, detail="Internal server error") from exc
```

- [ ] **Step 6: Fix existing test that checks raw error message**

In `tests/test_health_endpoints.py`, line 55 currently asserts:
```python
assert "network timeout" in resp.json().get("detail", "")
```

Replace with:
```python
assert resp.json()["detail"] == "Internal server error"
```

- [ ] **Step 7: Run tests**

Run: `cd /Users/bevo/Documents/answerD.it/TakeoffAI && python -m pytest tests/test_security.py tests/test_health_endpoints.py -v`

Expected: All PASS.

- [ ] **Step 8: Commit**

```bash
git add backend/api/routes.py backend/api/upload.py backend/api/verification.py tests/test_security.py tests/test_health_endpoints.py
git commit -m "sec: generic error messages — stop leaking internals in 500 responses (Fixes 4, 6)"
```

---

### Task 3: File Upload Hardening — Size Limit, Content Validation, Client ID Sanitization (Fixes 3, 7, 8b)

**Files:**
- Modify: `backend/api/upload.py`
- Modify: `tests/test_security.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_security.py`:

```python
# ── Fix 3: File size limit ─────────────────────────────────────────────────

@pytest.mark.anyio
async def test_oversized_upload_returns_413(monkeypatch):
    """Uploads over 10MB must be rejected with 413."""
    monkeypatch.setenv("API_KEY", "test-key")
    import io
    # Create a file just over 10MB
    big_content = b"a" * (10 * 1024 * 1024 + 1)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post(
            "/api/upload/bids/csv",
            files={"file": ("big.csv", io.BytesIO(big_content), "text/csv")},
            data={"client_id": "test"},
            headers={"X-API-Key": "test-key"},
        )
    assert resp.status_code == 413


# ── Fix 7: Client ID sanitization in uploads ──────────────────────────────

@pytest.mark.anyio
async def test_malicious_client_id_in_upload_returns_400(monkeypatch):
    """client_id with path traversal chars must be rejected."""
    monkeypatch.setenv("API_KEY", "test-key")
    import io
    csv_content = b"project_name,zip_code,bid_date,your_bid_amount\nTest,75001,2024-01-01,100000\n"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post(
            "/api/upload/bids/csv",
            files={"file": ("bids.csv", io.BytesIO(csv_content), "text/csv")},
            data={"client_id": "../../../etc/passwd"},
            headers={"X-API-Key": "test-key"},
        )
    assert resp.status_code == 400


# ── Fix 8b: File type content validation ───────────────────────────────────

@pytest.mark.anyio
async def test_binary_file_renamed_to_csv_returns_400(monkeypatch):
    """A binary file with .csv extension must be rejected."""
    monkeypatch.setenv("API_KEY", "test-key")
    import io
    binary_content = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100  # PNG header
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post(
            "/api/upload/bids/csv",
            files={"file": ("fake.csv", io.BytesIO(binary_content), "text/csv")},
            data={"client_id": "test"},
            headers={"X-API-Key": "test-key"},
        )
    assert resp.status_code == 400


@pytest.mark.anyio
async def test_csv_renamed_to_xlsx_returns_400(monkeypatch):
    """A CSV file with .xlsx extension must be rejected (no PK magic bytes)."""
    monkeypatch.setenv("API_KEY", "test-key")
    import io
    csv_content = b"project_name,zip_code,bid_date,your_bid_amount\nTest,75001,2024-01-01,100000\n"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post(
            "/api/upload/bids/excel",
            files={"file": ("fake.xlsx", io.BytesIO(csv_content), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            data={"client_id": "test"},
            headers={"X-API-Key": "test-key"},
        )
    assert resp.status_code == 400
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/bevo/Documents/answerD.it/TakeoffAI && python -m pytest tests/test_security.py::test_oversized_upload_returns_413 tests/test_security.py::test_malicious_client_id_in_upload_returns_400 tests/test_security.py::test_binary_file_renamed_to_csv_returns_400 tests/test_security.py::test_csv_renamed_to_xlsx_returns_400 -v`

Expected: All 4 FAIL.

- [ ] **Step 3: Add constants and helpers at top of upload.py**

In `backend/api/upload.py`, add `import re` to the imports (after `import io`):

```python
import re
```

After the `upload_router = APIRouter(...)` line (line 17), add:

```python
MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB


def _validate_client_id(client_id: str) -> None:
    """Reject client_id values that could enable path traversal."""
    if not re.match(r'^[a-zA-Z0-9_\-]+$', client_id):
        raise HTTPException(status_code=400, detail="Invalid client_id format")


def _validate_csv_content(content: bytes) -> None:
    """Reject binary files disguised as CSV — first 512 bytes must be printable ASCII."""
    sample = content[:512]
    try:
        sample.decode("ascii")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="Invalid file content")
    # Check no null bytes (binary signature)
    if b"\x00" in sample:
        raise HTTPException(status_code=400, detail="Invalid file content")


def _validate_xlsx_content(content: bytes) -> None:
    """Reject files that don't start with PK zip magic bytes."""
    if not content.startswith(b"\x50\x4b\x03\x04"):
        raise HTTPException(status_code=400, detail="Invalid file content")
```

- [ ] **Step 4: Add size check, client_id check, and content validation to upload_csv**

Replace the `upload_csv` function body. The full function becomes:

```python
@upload_router.post("/bids/csv")
async def upload_csv(
    file: UploadFile,
    client_id: str = Form(..., description="Client ID to update"),
):
    """Parse a CSV bid history file and import into client profile."""
    _validate_client_id(client_id)
    if not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="File must be a .csv")
    try:
        contents = await file.read()
    except Exception as exc:
        logging.exception("CSV upload read failed")
        raise HTTPException(status_code=500, detail="Internal server error") from exc

    if len(contents) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File too large (10 MB max)")

    _validate_csv_content(contents)

    try:
        df = pd.read_csv(io.BytesIO(contents), dtype=str)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Could not parse CSV: {exc}") from exc

    if df.empty:
        raise HTTPException(status_code=422, detail="CSV file is empty")

    bids, errors = _process_dataframe(df)
    if not bids:
        raise HTTPException(status_code=422, detail={"message": "No valid rows found", "errors": errors})

    try:
        return _import_summary(client_id, bids, errors)
    except Exception as exc:
        logging.exception("CSV import failed")
        raise HTTPException(status_code=500, detail="Internal server error") from exc
```

- [ ] **Step 5: Add size check, client_id check, and content validation to upload_excel**

Replace the `upload_excel` function body:

```python
@upload_router.post("/bids/excel")
async def upload_excel(
    file: UploadFile,
    client_id: str = Form(..., description="Client ID to update"),
):
    """Parse an Excel (.xlsx) bid history file and import into client profile."""
    _validate_client_id(client_id)
    if not file.filename.lower().endswith((".xlsx", ".xls")):
        raise HTTPException(status_code=400, detail="File must be .xlsx or .xls")
    try:
        contents = await file.read()
    except Exception as exc:
        logging.exception("Excel upload read failed")
        raise HTTPException(status_code=500, detail="Internal server error") from exc

    if len(contents) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File too large (10 MB max)")

    _validate_xlsx_content(contents)

    try:
        df = pd.read_excel(io.BytesIO(contents), dtype=str, engine="openpyxl")
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Could not parse Excel file: {exc}") from exc

    if df.empty:
        raise HTTPException(status_code=422, detail="Excel file is empty")

    bids, errors = _process_dataframe(df)
    if not bids:
        raise HTTPException(status_code=422, detail={"message": "No valid rows found", "errors": errors})

    try:
        return _import_summary(client_id, bids, errors)
    except Exception as exc:
        logging.exception("Excel import failed")
        raise HTTPException(status_code=500, detail="Internal server error") from exc
```

- [ ] **Step 6: Add client_id check to upload_manual**

At the top of the `upload_manual` function, add the validation call:

```python
@upload_router.post("/bids/manual")
async def upload_manual(req: ManualUploadRequest):
    """Import bid records submitted as a JSON array from the manual entry table."""
    _validate_client_id(req.client_id)
    if not req.bids:
        raise HTTPException(status_code=422, detail="No bid records provided")
    # ... rest unchanged
```

- [ ] **Step 7: Add client_id check to import_bids**

At the top of the `import_bids` function, add:

```python
@upload_router.post("/import")
async def import_bids(req: ImportRequest):
    """Persist parsed bid records into the client profile for tournament learning."""
    _validate_client_id(req.client_id)
    from backend.agents.feedback_loop import update_client_profile_from_upload
    # ... rest unchanged
```

- [ ] **Step 8: Run tests**

Run: `cd /Users/bevo/Documents/answerD.it/TakeoffAI && python -m pytest tests/test_security.py -v`

Expected: All tests PASS (including new ones).

- [ ] **Step 9: Commit**

```bash
git add backend/api/upload.py tests/test_security.py
git commit -m "sec: file upload hardening — size limit, magic bytes, client_id sanitization (Fixes 3, 7, 8b)"
```

---

### Task 4: Rate Limiting with slowapi (Fix 5)

**Files:**
- Modify: `pyproject.toml`
- Modify: `backend/api/main.py`
- Modify: `tests/test_security.py`

- [ ] **Step 1: Add slowapi dependency**

In `pyproject.toml`, add `slowapi` to the dependencies list. After the `"httpx>=0.28.0",` line, add:

```
    "slowapi>=0.1.9",
```

- [ ] **Step 2: Install the dependency**

Run: `cd /Users/bevo/Documents/answerD.it/TakeoffAI && uv sync`

- [ ] **Step 3: Write failing test for rate limiting**

Append to `tests/test_security.py`:

```python
# ── Fix 5: Rate limiting ──────────────────────────────────────────────────

@pytest.mark.anyio
async def test_rate_limiter_returns_429_after_burst(monkeypatch):
    """Hitting an endpoint more than its rate limit should return 429."""
    monkeypatch.setenv("API_KEY", "test-key")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        statuses = []
        # Tournament run is limited to 10/minute — send 12 requests
        for _ in range(12):
            resp = await c.post(
                "/api/tournament/run",
                json={
                    "description": "Build a 10000 sqft warehouse in Houston TX",
                    "zip_code": "77001",
                },
                headers={"X-API-Key": "test-key"},
            )
            statuses.append(resp.status_code)
    assert 429 in statuses, f"Expected at least one 429, got: {set(statuses)}"
```

- [ ] **Step 4: Run test to verify it fails**

Run: `cd /Users/bevo/Documents/answerD.it/TakeoffAI && python -m pytest tests/test_security.py::test_rate_limiter_returns_429_after_burst -v`

Expected: FAIL — no rate limiter yet.

- [ ] **Step 5: Add rate limiting to main.py**

In `backend/api/main.py`, add imports after the existing imports (after line 7):

```python
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
```

After `app = FastAPI(...)` block (after line 141), add the limiter setup:

```python
limiter = Limiter(key_func=get_remote_address, default_limits=["60/minute"])
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
```

Then add endpoint-specific rate limits. After the `app.include_router(...)` lines, add:

```python
# ── Endpoint-specific rate limits ─────────────────────────────────────────
# slowapi decorates the endpoint functions directly

from backend.api.routes import estimate, bid_strategy, tournament_run

estimate = limiter.limit("30/minute")(estimate)
bid_strategy = limiter.limit("30/minute")(bid_strategy)
tournament_run = limiter.limit("10/minute")(tournament_run)
```

**Important:** slowapi's `Limiter` with `default_limits` applies globally via middleware. The endpoint-specific limits above override the default for those endpoints. The `/api/health` endpoint uses the global 60/minute default.

- [ ] **Step 6: Run test to verify it passes**

Run: `cd /Users/bevo/Documents/answerD.it/TakeoffAI && python -m pytest tests/test_security.py::test_rate_limiter_returns_429_after_burst -v`

Expected: PASS.

- [ ] **Step 7: Run full test suite**

Run: `cd /Users/bevo/Documents/answerD.it/TakeoffAI && python -m pytest tests/ -v --timeout=30`

Expected: All PASS.

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml uv.lock backend/api/main.py tests/test_security.py
git commit -m "sec: add rate limiting via slowapi — 10/min tournament, 30/min estimate/bid (Fix 5)"
```

---

### Task 5: CORS Tightening (Fix 8)

**Files:**
- Modify: `backend/api/main.py`

- [ ] **Step 1: Tighten CORS configuration**

In `backend/api/main.py`, replace lines 143-154 (the CORS block):

```python
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000,http://localhost:5173").split(",")

if "*" in ALLOWED_ORIGINS and os.getenv("APP_ENV", "development") != "development":
    raise RuntimeError("Wildcard ALLOWED_ORIGINS cannot be used with allow_credentials=True in non-dev environments")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["POST", "GET", "PATCH", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)
```

with:

```python
_app_env = os.getenv("APP_ENV", "development")
_raw_origins = os.getenv("ALLOWED_ORIGINS", "")

if _app_env != "development" and not _raw_origins:
    raise RuntimeError("ALLOWED_ORIGINS env var is required in non-development mode")

ALLOWED_ORIGINS = (
    _raw_origins.split(",") if _raw_origins
    else ["http://localhost:3000", "http://localhost:5173"]
)

if "*" in ALLOWED_ORIGINS:
    raise RuntimeError("Wildcard ALLOWED_ORIGINS is not permitted (incompatible with credentials)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["POST", "GET", "PATCH", "OPTIONS"],
    allow_headers=["Content-Type", "X-API-Key"],
)
```

Key changes:
- `ALLOWED_ORIGINS` required in non-dev mode (not just validated)
- Wildcard `*` always rejected (not just in non-dev)
- `allow_headers` changed from `Authorization` to `X-API-Key` (we use API key auth, not Bearer)
- Dev mode defaults to localhost:3000 and localhost:5173

- [ ] **Step 2: Run existing tests**

Run: `cd /Users/bevo/Documents/answerD.it/TakeoffAI && python -m pytest tests/ -v --timeout=30`

Expected: All PASS (tests run in default development mode).

- [ ] **Step 3: Commit**

```bash
git add backend/api/main.py
git commit -m "sec: tighter CORS — require ALLOWED_ORIGINS in prod, restrict headers to X-API-Key (Fix 8)"
```

---

### Task 6: Health Info-Leak + PRAGMA Cleanup (Fixes 9, 10)

**Files:**
- Modify: `backend/api/main.py`
- Modify: `tests/test_security.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_security.py`:

```python
# ── Fix 9: Health endpoint info-leak ───────────────────────────────────────

@pytest.mark.anyio
async def test_health_degraded_does_not_leak_reason(monkeypatch):
    """Degraded health response must not include a 'reason' field."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/api/health")
    data = resp.json()
    assert data["status"] == "degraded"
    assert "reason" not in data
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/bevo/Documents/answerD.it/TakeoffAI && python -m pytest tests/test_security.py::test_health_degraded_does_not_leak_reason -v`

Expected: FAIL — currently includes `"reason": "ANTHROPIC_API_KEY not configured"`.

- [ ] **Step 3: Fix health endpoint**

In `backend/api/main.py`, replace lines 161-166:

```python
@app.get("/api/health")
async def health():
    from backend.config import settings
    if not settings.anthropic_api_key or settings.anthropic_api_key == "sk-ant-your-key-here":
        return {"status": "degraded", "reason": "ANTHROPIC_API_KEY not configured", "product": "TakeoffAI", "company": "answerd.it"}
    return {"status": "ok", "product": "TakeoffAI", "company": "answerd.it"}
```

with:

```python
@app.get("/api/health")
async def health():
    from backend.config import settings
    if not settings.anthropic_api_key or settings.anthropic_api_key == "sk-ant-your-key-here":
        return {"status": "degraded", "product": "TakeoffAI", "company": "answerd.it"}
    return {"status": "ok", "product": "TakeoffAI", "company": "answerd.it"}
```

- [ ] **Step 4: Fix PRAGMA f-string**

In `backend/api/main.py`, replace lines 110 and 115 in the `_run_migrations` function.

Replace every occurrence of:
```python
await db.execute(f"PRAGMA user_version = {version}")
```

with:
```python
await db.execute(f"PRAGMA user_version = {int(version)}")
```

This explicitly casts to `int` to prevent any future injection path. SQLite PRAGMA doesn't support `?` parameters, so this is the standard safe approach — `version` comes from `enumerate()` so it's already an int, but the explicit cast documents the safety guarantee.

- [ ] **Step 5: Run tests**

Run: `cd /Users/bevo/Documents/answerD.it/TakeoffAI && python -m pytest tests/test_security.py -v`

Expected: All PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/api/main.py tests/test_security.py
git commit -m "sec: remove health reason field, sanitize PRAGMA version (Fixes 9, 10)"
```

---

### Task 7: HTTPS Nginx Stub (Fix 11)

**Files:**
- Modify: `nginx.conf`

- [ ] **Step 1: Add commented SSL server block**

Replace the entire `nginx.conf` with:

```nginx
server {
    listen 80;
    root /usr/share/nginx/html;
    index index.html;

    # ── Uncomment the next 2 lines + the ssl server block below to enable HTTPS ──
    # return 301 https://$host$request_uri;

    # Proxy all /api/ requests to the backend container — eliminates CORS
    location /api/ {
        proxy_pass         http://backend:8000/api/;
        proxy_http_version 1.1;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Real-IP         $remote_addr;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_read_timeout 120s;
    }

    # Health dashboard
    location = /health {
        try_files /health.html =404;
    }

    # Serve static files; fall back to index.html for SPA routing
    location / {
        try_files $uri $uri/ /index.html;
    }
}

# ── HTTPS server block (Let's Encrypt) ─────────────────────────────────────
# To enable:
#   1. Install certbot:  apt install certbot python3-certbot-nginx
#   2. Obtain cert:      certbot certonly --webroot -w /usr/share/nginx/html -d yourdomain.com
#   3. Uncomment the server block below
#   4. Uncomment the "return 301" line in the port-80 block above
#   5. Reload nginx:     nginx -s reload
#
# server {
#     listen 443 ssl http2;
#     server_name yourdomain.com;
#
#     ssl_certificate     /etc/letsencrypt/live/yourdomain.com/fullchain.pem;
#     ssl_certificate_key /etc/letsencrypt/live/yourdomain.com/privkey.pem;
#
#     ssl_protocols TLSv1.2 TLSv1.3;
#     ssl_ciphers HIGH:!aNULL:!MD5;
#     ssl_prefer_server_ciphers on;
#
#     root /usr/share/nginx/html;
#     index index.html;
#
#     location /api/ {
#         proxy_pass         http://backend:8000/api/;
#         proxy_http_version 1.1;
#         proxy_set_header   Host              $host;
#         proxy_set_header   X-Real-IP         $remote_addr;
#         proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
#         proxy_read_timeout 120s;
#     }
#
#     location = /health {
#         try_files /health.html =404;
#     }
#
#     location / {
#         try_files $uri $uri/ /index.html;
#     }
# }
```

- [ ] **Step 2: Visual inspection**

Verify the commented block has correct Let's Encrypt paths, TLS 1.2+, and duplicates all location blocks from the HTTP server.

- [ ] **Step 3: Commit**

```bash
git add nginx.conf
git commit -m "sec: add commented HTTPS/SSL nginx stub with Let's Encrypt instructions (Fix 11)"
```

---

## Verification

After all tasks, run the full test suite:

```bash
cd /Users/bevo/Documents/answerD.it/TakeoffAI && python -m pytest tests/ -v --timeout=30
```

All tests should pass, including:
- **Fix 1:** `test_upload_endpoint_requires_api_key`, `test_verification_endpoint_requires_api_key`
- **Fix 2:** `test_empty_api_key_rejects_requests`
- **Fix 3:** `test_oversized_upload_returns_413`
- **Fix 4/6:** `test_500_error_does_not_leak_details`
- **Fix 5:** `test_rate_limiter_returns_429_after_burst`
- **Fix 7:** `test_malicious_client_id_in_upload_returns_400`
- **Fix 8b:** `test_binary_file_renamed_to_csv_returns_400`, `test_csv_renamed_to_xlsx_returns_400`
- **Fix 9:** `test_health_degraded_does_not_leak_reason`
- **Fix 10:** Covered by existing migration tests
- **Fix 11:** Manual inspection (nginx config is not testable via pytest)
