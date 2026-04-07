# Blueprint PDF Preprocessing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow contractors to drop a blueprint PDF into the Pre-Bid Estimate tab, click Preprocess PDF, and have Claude auto-fill the description field with an estimate-ready draft.

**Architecture:** New multipart endpoint `POST /api/estimate/preprocess-pdf` calls a new `preprocess_blueprint()` function in `pre_bid_calc.py` that sends the PDF to Claude as a native document. If a `job_slug` is present, a fire-and-forget call updates the wiki job Scope section. The frontend adds a drop zone + button above the description textarea; on success it fills the field and shows an amber notice.

**Tech Stack:** FastAPI `UploadFile`/`Form` for multipart, Anthropic document content block API, vanilla JS `FormData`, existing `AsyncAnthropic` client in `pre_bid_calc.py`.

---

## File Map

| File | Change |
|---|---|
| `backend/agents/pre_bid_calc.py` | Add `BLUEPRINT_EXTRACTION_SYSTEM`, `BLUEPRINT_EXTRACTION_PROMPT`, `preprocess_blueprint()` |
| `backend/api/routes.py` | Add `POST /api/estimate/preprocess-pdf` endpoint + `_wiki_enrich_scope()` fire-and-forget helper |
| `backend/agents/wiki_manager.py` | Add `enrich_scope_from_blueprint()` |
| `frontend/dist/index.html` | PDF drop zone + Preprocess button + JS handler in estimate tab |
| `tests/test_routes.py` | Tests for new endpoint |

---

## Task 1: Add extraction prompts and `preprocess_blueprint()` to `pre_bid_calc.py`

**Files:**
- Modify: `backend/agents/pre_bid_calc.py`
- Test: `tests/test_routes.py` (written in Task 4)

### Context

`pre_bid_calc.py` currently has a module-level `client = AsyncAnthropic()` and `SYSTEM_PROMPT` constant. We add two new prompt constants and one new async function below the existing `SYSTEM_PROMPT` block (around line 91), before the `run_prebid_calc` functions. The function sends the PDF bytes to Claude using the document content block format — no new dependencies needed.

- [ ] **Step 1: Add the two extraction prompt constants**

Add this block to `backend/agents/pre_bid_calc.py` immediately after the closing `"""` of `SYSTEM_PROMPT` (after line 90):

```python
# ── Blueprint extraction prompts ──────────────────────────────────────────────

BLUEPRINT_EXTRACTION_SYSTEM = (
    "You are a construction takeoff assistant for TakeoffAI by answerd.it. "
    "Your job is to read construction plans and extract a plain-English project description "
    "that a cost estimator can use to generate a line-item bid estimate.\n\n"
    "Write in the voice of an experienced contractor describing the job to their estimator. "
    "Be specific and quantitative. Lead with the single most important number "
    "(total sqft, CY, LF, or units — whichever is primary for this trade). "
    "Do not include contract terms, owner names, bid dates, or submission requirements. "
    "Do not use adjectives without numbers behind them."
)

BLUEPRINT_EXTRACTION_PROMPT = (
    "Review these construction plans and extract a project description "
    "for a {trade_type} estimate. The project is located in zip code {zip_code}.\n\n"
    "Structure your response in this order:\n"
    "1. Total size (sqft, CY, LF, or units — whichever is primary for this trade)\n"
    "2. Building type and occupancy\n"
    "3. Structural system and key materials called out in the plans\n"
    "4. Scope of work — what is being built or installed\n"
    "5. Room counts or system counts (fixtures, panels, openings, etc.)\n"
    "6. Site conditions or access constraints visible in the plans\n"
    "7. Explicit exclusions noted in the plans or specs\n\n"
    "Write as a single paragraph or short bulleted list. Keep it under 200 words. "
    "If a dimension or quantity is not clearly shown in the plans, "
    "omit it rather than guess."
)
```

- [ ] **Step 2: Add `preprocess_blueprint()` function**

Add this function after the `BLUEPRINT_EXTRACTION_PROMPT` constant (still in `pre_bid_calc.py`):

```python
async def preprocess_blueprint(
    pdf_bytes: bytes,
    zip_code: str,
    trade_type: str = "general",
) -> str:
    """
    Send a blueprint PDF to Claude and extract an estimate-ready project description.
    Returns plain text draft suitable for the Pre-Bid Estimate description field.
    """
    import base64
    from backend.config import settings

    response = await client.messages.create(
        model=settings.claude_model,
        max_tokens=1024,
        system=BLUEPRINT_EXTRACTION_SYSTEM,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": base64.standard_b64encode(pdf_bytes).decode("utf-8"),
                        },
                    },
                    {
                        "type": "text",
                        "text": BLUEPRINT_EXTRACTION_PROMPT.format(
                            zip_code=zip_code,
                            trade_type=trade_type,
                        ),
                    },
                ],
            }
        ],
    )
    return response.content[0].text.strip()
```

- [ ] **Step 3: Verify the file still imports cleanly**

```bash
cd /Users/bevo/Documents/answerD.it/TakeoffAI
uv run python -c "from backend.agents.pre_bid_calc import preprocess_blueprint, BLUEPRINT_EXTRACTION_SYSTEM, BLUEPRINT_EXTRACTION_PROMPT; print('ok')"
```

Expected output: `ok`

- [ ] **Step 4: Commit**

```bash
git add backend/agents/pre_bid_calc.py
git commit -m "feat: add blueprint extraction prompts and preprocess_blueprint() to pre_bid_calc"
```

---

## Task 2: Add `POST /api/estimate/preprocess-pdf` endpoint to `routes.py`

**Files:**
- Modify: `backend/api/routes.py`

### Context

The existing estimate endpoints use JSON bodies and Pydantic models. This endpoint uses multipart form data because it receives a file. FastAPI handles this with `UploadFile` and `Form` parameters directly on the handler function — no Pydantic model. Add it after the existing `estimate` endpoint (around line 98). The wiki fire-and-forget helper follows the same pattern as the existing `_wiki_enrich_estimate()` at the bottom of the file.

- [ ] **Step 1: Add the import for `UploadFile` and `File` to `routes.py`**

`routes.py` already imports from `fastapi`. Update that import line to add `File`, `Form`, and `UploadFile`:

```python
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Security, UploadFile
```

- [ ] **Step 2: Add `preprocess_blueprint` to the imports from `pre_bid_calc`**

Find the existing import line:
```python
from backend.agents.pre_bid_calc import run_prebid_calc
```

Replace with:
```python
from backend.agents.pre_bid_calc import preprocess_blueprint, run_prebid_calc
```

- [ ] **Step 3: Add the endpoint after the existing `estimate` endpoint**

Insert this block after the closing of the `estimate` endpoint (after line 98):

```python
_MAX_PDF_BYTES = 32 * 1024 * 1024  # 32 MB


@router.post("/estimate/preprocess-pdf")
@limiter.limit("10/minute")
async def estimate_preprocess_pdf(
    request: Request,
    pdf: UploadFile = File(...),
    zip_code: str = Form(..., min_length=5, max_length=10),
    trade_type: str = Form(default="general"),
    job_slug: Optional[str] = Form(default=None),
):
    """
    Read a blueprint PDF and return an estimate-ready draft description.
    Optionally updates the wiki job Scope section if job_slug is provided.
    """
    if pdf.content_type not in ("application/pdf", "application/octet-stream"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted.")

    pdf_bytes = await pdf.read()
    if len(pdf_bytes) > _MAX_PDF_BYTES:
        raise HTTPException(status_code=400, detail="File too large. Maximum size is 32MB.")
    if not pdf_bytes:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    try:
        draft = await preprocess_blueprint(pdf_bytes, zip_code, trade_type)
    except Exception as exc:
        logging.exception("preprocess_blueprint failed")
        raise HTTPException(status_code=500, detail="Internal server error") from exc

    if job_slug:
        asyncio.ensure_future(_wiki_enrich_scope(job_slug, draft))

    return {"draft": draft}
```

- [ ] **Step 4: Add the wiki fire-and-forget helper at the bottom of `routes.py`**

Add this alongside the other `_wiki_enrich_*` helpers at the bottom of the file:

```python
async def _wiki_enrich_scope(job_slug: str, draft_text: str) -> None:
    try:
        from backend.agents.wiki_manager import enrich_scope_from_blueprint
        await enrich_scope_from_blueprint(job_slug, draft_text)
    except Exception:
        logging.exception(
            "wiki enrich_scope_from_blueprint failed for %s (non-fatal)", job_slug
        )
```

- [ ] **Step 5: Verify the server starts cleanly**

```bash
uv run python -c "from backend.api.routes import router; print('ok')"
```

Expected output: `ok`

- [ ] **Step 6: Commit**

```bash
git add backend/api/routes.py
git commit -m "feat: add POST /api/estimate/preprocess-pdf endpoint"
```

---

## Task 3: Add `enrich_scope_from_blueprint()` to `wiki_manager.py`

**Files:**
- Modify: `backend/agents/wiki_manager.py`

### Context

`wiki_manager.py` has a public functions section starting at line 139. All public functions follow the same pattern: `try/except Exception` wrapper, log on failure, fire-and-forget safe. The function reads the job page, replaces the `## Scope` section body with the blueprint-extracted draft text, and writes the page back. If no job page exists yet, it does nothing (logs a warning).

- [ ] **Step 1: Add `enrich_scope_from_blueprint()` to `wiki_manager.py`**

Add this function in the public functions section of `backend/agents/wiki_manager.py`, after `create_job()`:

```python
async def enrich_scope_from_blueprint(job_slug: str, draft_text: str) -> None:
    """
    Overwrite the ## Scope section of a job page with blueprint-extracted draft text.
    Fire-and-forget safe — all errors are caught and logged.
    """
    try:
        page_path = JOBS_DIR / f"{job_slug}.md"
        if not page_path.exists():
            logger.warning("enrich_scope_from_blueprint: job page not found for %s", job_slug)
            return

        meta, body = _parse_frontmatter(page_path)

        # Replace existing ## Scope section, or prepend one if absent
        scope_section = f"## Scope\n\n{draft_text.strip()}"
        scope_pattern = re.compile(r"## Scope\n[\s\S]*?(?=\n## |\Z)", re.MULTILINE)

        if scope_pattern.search(body):
            body = scope_pattern.sub(scope_section, body, count=1)
        else:
            body = scope_section + "\n\n" + body.lstrip()

        _write_page(page_path, meta, body)
        logger.info("enrich_scope_from_blueprint: updated Scope for %s", job_slug)
    except Exception:
        logger.exception("enrich_scope_from_blueprint failed for %s (non-fatal)", job_slug)
```

- [ ] **Step 2: Verify the import works**

```bash
uv run python -c "from backend.agents.wiki_manager import enrich_scope_from_blueprint; print('ok')"
```

Expected output: `ok`

- [ ] **Step 3: Commit**

```bash
git add backend/agents/wiki_manager.py
git commit -m "feat: add enrich_scope_from_blueprint() to wiki_manager"
```

---

## Task 4: Add tests for the new endpoint

**Files:**
- Modify: `tests/test_routes.py`

### Context

Tests use `@pytest.mark.anyio` (not `asyncio`). The existing route tests use `monkeypatch` to set `API_KEY` and construct an `AsyncClient` inline, or use the shared `client` fixture from `conftest.py`. For the PDF endpoint, we need to mock `preprocess_blueprint` to avoid real API calls, and build a minimal valid PDF bytes fixture inline.

A minimal valid PDF is just enough bytes to pass content-type and size checks — we mock the actual Claude call so the content doesn't matter.

- [ ] **Step 1: Add the three new tests to `tests/test_routes.py`**

Add these tests at the end of the file:

```python
@pytest.mark.anyio
async def test_preprocess_pdf_wrong_type(monkeypatch):
    """Non-PDF file should return 400."""
    monkeypatch.setenv("API_KEY", "test-key")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post(
            "/api/estimate/preprocess-pdf",
            files={"pdf": ("plans.txt", b"not a pdf", "text/plain")},
            data={"zip_code": "76801", "trade_type": "general"},
            headers={"X-API-Key": "test-key"},
        )
    assert resp.status_code == 400
    assert "PDF" in resp.json()["detail"]


@pytest.mark.anyio
async def test_preprocess_pdf_too_large(monkeypatch):
    """File over 32MB should return 400."""
    monkeypatch.setenv("API_KEY", "test-key")
    oversized = b"%PDF-1.4" + b"x" * (32 * 1024 * 1024 + 1)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post(
            "/api/estimate/preprocess-pdf",
            files={"pdf": ("big.pdf", oversized, "application/pdf")},
            data={"zip_code": "76801", "trade_type": "general"},
            headers={"X-API-Key": "test-key"},
        )
    assert resp.status_code == 400
    assert "32MB" in resp.json()["detail"]


@pytest.mark.anyio
async def test_preprocess_pdf_success(monkeypatch):
    """Valid PDF with mocked Claude call should return a draft string."""
    monkeypatch.setenv("API_KEY", "test-key")
    minimal_pdf = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\n%%EOF"

    with patch(
        "backend.api.routes.preprocess_blueprint",
        new=AsyncMock(return_value="18,000 sqft 3-story commercial office building"),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post(
                "/api/estimate/preprocess-pdf",
                files={"pdf": ("plans.pdf", minimal_pdf, "application/pdf")},
                data={"zip_code": "76801", "trade_type": "general"},
                headers={"X-API-Key": "test-key"},
            )

    assert resp.status_code == 200
    data = resp.json()
    assert "draft" in data
    assert "18,000 sqft" in data["draft"]
```

- [ ] **Step 2: Run only the new tests to verify they pass**

```bash
uv run pytest tests/test_routes.py::test_preprocess_pdf_wrong_type tests/test_routes.py::test_preprocess_pdf_too_large tests/test_routes.py::test_preprocess_pdf_success -v
```

Expected output: 3 passed

- [ ] **Step 3: Run the full test suite to confirm nothing is broken**

```bash
uv run pytest -v
```

Expected: all tests pass (same count as before + 3 new)

- [ ] **Step 4: Commit**

```bash
git add tests/test_routes.py
git commit -m "test: add preprocess-pdf endpoint tests"
```

---

## Task 5: Add PDF drop zone and Preprocess button to the frontend

**Files:**
- Modify: `frontend/dist/index.html`

### Context

The estimate tab HTML starts at line 701. The description textarea is inside a `<div class="field full">` at line 707. We insert the drop zone as a new `<div class="field full">` block directly above the description field, inside the same `<div class="form-grid">`. The JS handler goes in the `<script>` block alongside the existing estimate JS.

The endpoint expects `multipart/form-data` — use `FormData`, not `JSON.stringify`. The `zip_code` and `trade_type` values are read from the existing `est-zip` and `est-trade` fields at preprocess time.

- [ ] **Step 1: Add the PDF drop zone HTML above the description field**

Find this exact block in `frontend/dist/index.html`:

```html
        <div class="field full">
          <label for="est-desc">Project Description</label>
          <textarea id="est-desc" placeholder="e.g. 2,400 sqft single-story commercial office build-out with open floor plan, 2 restrooms, break room. Includes framing, drywall, electrical, plumbing rough-in, and painting." rows="4"></textarea>
        </div>
```

Replace with:

```html
        <div class="field full">
          <label>Blueprint PDF <span style="color:var(--text-muted)">(optional — generates a draft description)</span></label>
          <div id="pdf-drop-zone" style="
            border:2px dashed var(--border);border-radius:var(--radius);
            padding:1.1rem;text-align:center;cursor:pointer;
            transition:border-color .2s;font-size:0.83rem;color:var(--text-muted)
          ">
            Drop PDF here or <span style="color:var(--amber);text-decoration:underline">click to browse</span>
            &nbsp;·&nbsp; Max 32MB &nbsp;·&nbsp; 1–5 pages works best
            <div id="pdf-filename" style="color:var(--green);margin-top:4px;font-size:0.8rem"></div>
          </div>
          <input id="pdf-file-input" type="file" accept=".pdf,application/pdf" style="display:none" />
          <div id="pdf-error" style="display:none;color:var(--red);font-size:0.8rem;margin-top:4px"></div>
          <button id="pdf-preprocess-btn" class="btn btn-secondary btn-sm"
            style="display:none;margin-top:0.5rem">
            Preprocess PDF
          </button>
        </div>

        <div class="field full">
          <label for="est-desc">Project Description</label>
          <textarea id="est-desc" placeholder="e.g. 2,400 sqft single-story commercial office build-out with open floor plan, 2 restrooms, break room. Includes framing, drywall, electrical, plumbing rough-in, and painting." rows="4"></textarea>
          <div id="pdf-draft-notice" style="display:none;margin-top:5px;font-size:0.78rem;
            color:var(--amber);background:var(--amber-glow);border:1px solid var(--amber-dim);
            border-radius:4px;padding:4px 10px">
            Draft extracted from blueprint — review and edit before estimating
          </div>
        </div>
```

- [ ] **Step 2: Add the PDF drop zone JS handler in the `<script>` block**

Find the comment `/* ── API key ───` in the script block and add the following block immediately before it:

```javascript
  /* ── Blueprint PDF preprocessing ──────────────────────────────────── */

  const pdfDropZone    = document.getElementById('pdf-drop-zone');
  const pdfFileInput   = document.getElementById('pdf-file-input');
  const pdfFilename    = document.getElementById('pdf-filename');
  const pdfError       = document.getElementById('pdf-error');
  const pdfPreBtn      = document.getElementById('pdf-preprocess-btn');
  const pdfDraftNotice = document.getElementById('pdf-draft-notice');

  pdfDropZone.addEventListener('click', () => pdfFileInput.click());

  pdfDropZone.addEventListener('dragover', e => {
    e.preventDefault();
    pdfDropZone.style.borderColor = 'var(--amber)';
  });

  pdfDropZone.addEventListener('dragleave', () => {
    pdfDropZone.style.borderColor = 'var(--border)';
  });

  pdfDropZone.addEventListener('drop', e => {
    e.preventDefault();
    pdfDropZone.style.borderColor = 'var(--border)';
    const file = e.dataTransfer.files[0];
    if (file) _setPdfFile(file);
  });

  pdfFileInput.addEventListener('change', () => {
    if (pdfFileInput.files[0]) _setPdfFile(pdfFileInput.files[0]);
  });

  function _setPdfFile(file) {
    pdfError.style.display = 'none';
    pdfDraftNotice.style.display = 'none';
    pdfFilename.textContent = file.name;
    pdfPreBtn.style.display = 'inline-block';
  }

  pdfPreBtn.addEventListener('click', async () => {
    const file = pdfFileInput.files[0] || null;
    const droppedFile = pdfDropZone._droppedFile || null;
    const pdf = file || droppedFile;

    const zipVal   = document.getElementById('est-zip').value.trim();
    const tradeVal = document.getElementById('est-trade').value;

    pdfError.style.display = 'none';
    if (!pdfFilename.textContent) {
      pdfError.textContent = 'No PDF selected.';
      pdfError.style.display = 'block';
      return;
    }
    if (!zipVal) {
      pdfError.textContent = 'Enter a zip code before preprocessing.';
      pdfError.style.display = 'block';
      return;
    }

    setLoading(pdfPreBtn, true, 'Preprocess PDF');

    try {
      const fd = new FormData();
      fd.append('pdf', pdfFileInput.files[0]);
      fd.append('zip_code', zipVal);
      fd.append('trade_type', tradeVal);
      const slug = sessionStorage.getItem('takeoffai_job_slug');
      if (slug) fd.append('job_slug', slug);

      const res = await fetch(`${API_BASE}/api/estimate/preprocess-pdf`, {
        method: 'POST',
        headers: { 'X-API-Key': hdrApiKey.value },
        body: fd,
      });

      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `Server error ${res.status}`);
      }

      const data = await res.json();
      document.getElementById('est-desc').value = data.draft || '';
      pdfDraftNotice.style.display = 'block';
      toast('Blueprint processed — review the description below');
    } catch (err) {
      pdfError.textContent = err.message || 'Preprocess failed.';
      pdfError.style.display = 'block';
    } finally {
      setLoading(pdfPreBtn, false, 'Preprocess PDF');
    }
  });

  // Support drag-and-drop file reference without a file input update
  pdfDropZone.addEventListener('drop', e => {
    if (e.dataTransfer.files[0]) {
      pdfDropZone._droppedFile = e.dataTransfer.files[0];
      // Also inject into the file input for FormData use
      const dt = new DataTransfer();
      dt.items.add(e.dataTransfer.files[0]);
      pdfFileInput.files = dt.files;
    }
  }, true);
```

- [ ] **Step 3: Open the frontend and verify the drop zone renders**

```bash
open /Users/bevo/Documents/answerD.it/TakeoffAI/frontend/dist/index.html
```

Confirm:
- Drop zone appears above the description field in the Pre-Bid Estimate tab
- Clicking the drop zone opens a file picker filtered to PDF
- Selecting a PDF shows the filename and reveals the Preprocess PDF button
- No JS errors in the browser console

- [ ] **Step 4: Run the full test suite one final time**

```bash
uv run pytest -v
```

Expected: all tests pass

- [ ] **Step 5: Commit**

```bash
git add -f frontend/dist/index.html
git commit -m "feat: add blueprint PDF drop zone and Preprocess button to estimate tab"
```

---

## Self-Review Checklist

**Spec coverage:**
- ✅ `POST /api/estimate/preprocess-pdf` endpoint — Task 2
- ✅ `preprocess_blueprint()` with `BLUEPRINT_EXTRACTION_SYSTEM` + `BLUEPRINT_EXTRACTION_PROMPT` — Task 1
- ✅ `enrich_scope_from_blueprint()` wiki integration — Task 3
- ✅ PDF drop zone + Preprocess button + amber notice — Task 5
- ✅ 400 on wrong type, 400 on oversized, 422 on missing fields, 200 success — Task 4
- ✅ Fire-and-forget wiki hook with `job_slug` — Task 2 Step 4
- ✅ Rate limit 10/minute — Task 2 Step 3
- ✅ No PDF stored — design decision, not persisted anywhere

**Signatures consistent across tasks:**
- `preprocess_blueprint(pdf_bytes: bytes, zip_code: str, trade_type: str) -> str` — defined Task 1, imported Task 2, mocked Task 4 ✅
- `enrich_scope_from_blueprint(job_slug: str, draft_text: str) -> None` — defined Task 3, called Task 2 ✅
- `_wiki_enrich_scope(job_slug, draft)` helper — defined and called Task 2 ✅
