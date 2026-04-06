# Price Verification & Self-Calibration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add web-sourced price verification, human audit/override queue, post-job deviation tracking, Brier-scored win probability calibration, and automatic agent red-flagging to TakeoffAI.

**Architecture:** A standalone `price_verifier.py` agent fetches supplier pages (Home Depot, Lowe's) with httpx and falls back to DuckDuckGo web search; Claude extracts unit prices from raw HTML. Three trigger paths (background post-judge, on-demand API, nightly batch) all write to two new SQLite tables. Two new functions in `feedback_loop.py` close the post-job loop. All existing files receive additive changes only — no rewrites.

**Tech Stack:** Python 3.11+, FastAPI, aiosqlite, httpx, anthropic SDK, APScheduler, pytest + pytest-asyncio

---

## File Map

| File | Status | Responsibility |
|---|---|---|
| `backend/api/main.py` | MODIFY | Add new table DDL; include verification router; start/stop APScheduler |
| `backend/agents/price_verifier.py` | CREATE | Supplier lookup, web fallback, confidence decision, CSV auto-update, audit writes |
| `backend/agents/feedback_loop.py` | MODIFY | Append `record_actual_outcome()`, `get_agent_accuracy_report()`, `_compute_brier_score()` |
| `backend/agents/judge.py` | MODIFY | Append fire-and-forget verification background task after `update_client_profile` |
| `backend/api/verification.py` | CREATE | 6 API endpoints: verify, audit list, queue list, queue resolve, outcome submit, accuracy report |
| `backend/scheduler.py` | CREATE | APScheduler config; nightly 2am batch job calling `verify_line_items` for every CSV row |
| `pyproject.toml` | MODIFY | Add `apscheduler>=3.10.0` to dependencies |
| `tests/__init__.py` | CREATE | Empty — marks tests as package |
| `tests/test_price_verifier.py` | CREATE | Unit tests for verifier phases, deviation math, CSV update, audit writes |
| `tests/test_feedback_loop_calibration.py` | CREATE | Unit tests for `record_actual_outcome`, Brier score, red-flag logic |
| `tests/test_verification_api.py` | CREATE | Integration tests for all 6 verification endpoints |

---

## Task 1: Add `apscheduler` Dependency

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add apscheduler to pyproject.toml**

Open `pyproject.toml`. In the `[project] dependencies` list, add after the `httpx` line:

```toml
    "apscheduler>=3.10.0",
```

- [ ] **Step 2: Install the dependency**

```bash
cd "/Users/bevo/Library/CloudStorage/GoogleDrive-kroberts2007@gmail.com/My Drive/TakeoffAI"
uv add apscheduler
```

Expected: `uv.lock` updates; no errors.

- [ ] **Step 3: Verify import works**

```bash
uv run python -c "from apscheduler.schedulers.asyncio import AsyncIOScheduler; print('ok')"
```

Expected: `ok`

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "feat: add apscheduler dependency for nightly price verification batch"
```

---

## Task 2: Extend Database Schema

**Files:**
- Modify: `backend/api/main.py`

- [ ] **Step 1: Write the failing test**

Create `tests/__init__.py` (empty):

```bash
mkdir -p "/Users/bevo/Library/CloudStorage/GoogleDrive-kroberts2007@gmail.com/My Drive/TakeoffAI/tests"
touch "/Users/bevo/Library/CloudStorage/GoogleDrive-kroberts2007@gmail.com/My Drive/TakeoffAI/tests/__init__.py"
```

Create `tests/test_db_schema.py`:

```python
import asyncio
import tempfile
from pathlib import Path
import aiosqlite
import pytest


@pytest.mark.asyncio
async def test_price_audit_table_exists():
    """price_audit table should be created by the DDL in main.py."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    # Import and run the DDL
    from backend.api.main import _CREATE_TABLES
    async with aiosqlite.connect(db_path) as db:
        await db.executescript(_CREATE_TABLES)
        await db.commit()
        async with db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='price_audit'"
        ) as cur:
            row = await cur.fetchone()
    assert row is not None, "price_audit table not created"


@pytest.mark.asyncio
async def test_review_queue_table_exists():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    from backend.api.main import _CREATE_TABLES
    async with aiosqlite.connect(db_path) as db:
        await db.executescript(_CREATE_TABLES)
        await db.commit()
        async with db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='review_queue'"
        ) as cur:
            row = await cur.fetchone()
    assert row is not None, "review_queue table not created"


@pytest.mark.asyncio
async def test_price_audit_columns():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    from backend.api.main import _CREATE_TABLES
    async with aiosqlite.connect(db_path) as db:
        await db.executescript(_CREATE_TABLES)
        await db.commit()
        async with db.execute("PRAGMA table_info(price_audit)") as cur:
            cols = {row[1] for row in await cur.fetchall()}
    expected = {
        "id", "triggered_by", "tournament_id", "line_item", "unit",
        "ai_unit_cost", "verified_low", "verified_high", "verified_mid",
        "deviation_pct", "sources", "source_count", "flagged", "auto_updated", "created_at"
    }
    assert expected.issubset(cols), f"Missing columns: {expected - cols}"
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
cd "/Users/bevo/Library/CloudStorage/GoogleDrive-kroberts2007@gmail.com/My Drive/TakeoffAI"
uv run pytest tests/test_db_schema.py -v
```

Expected: FAIL — `price_audit` table does not exist yet.

- [ ] **Step 3: Add new table DDL to main.py**

Open `backend/api/main.py`. Find `_CREATE_TABLES` and append the two new tables inside the string, after the existing `tournament_entries` table:

```python
_CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS bid_tournaments (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id           TEXT,
    project_description TEXT NOT NULL,
    zip_code            TEXT NOT NULL,
    created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
    status              TEXT NOT NULL DEFAULT 'pending'
);

CREATE TABLE IF NOT EXISTS tournament_entries (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    tournament_id   INTEGER NOT NULL REFERENCES bid_tournaments(id),
    agent_name      TEXT NOT NULL,
    total_bid       REAL,
    line_items_json TEXT,
    won             INTEGER NOT NULL DEFAULT 0,
    score           REAL,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS price_audit (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    triggered_by    TEXT NOT NULL,
    tournament_id   INTEGER,
    line_item       TEXT NOT NULL,
    unit            TEXT NOT NULL,
    ai_unit_cost    REAL NOT NULL,
    verified_low    REAL,
    verified_high   REAL,
    verified_mid    REAL,
    deviation_pct   REAL,
    sources         TEXT,
    source_count    INTEGER DEFAULT 0,
    flagged         INTEGER DEFAULT 0,
    auto_updated    INTEGER DEFAULT 0,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS review_queue (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    audit_id        INTEGER NOT NULL REFERENCES price_audit(id),
    line_item       TEXT NOT NULL,
    unit            TEXT NOT NULL,
    ai_unit_cost    REAL NOT NULL,
    verified_mid    REAL NOT NULL,
    deviation_pct   REAL NOT NULL,
    sources         TEXT,
    status          TEXT NOT NULL DEFAULT 'pending',
    reviewer_notes  TEXT,
    resolved_at     DATETIME,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);
"""
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
uv run pytest tests/test_db_schema.py -v
```

Expected: 3 PASSED

- [ ] **Step 5: Commit**

```bash
git add backend/api/main.py tests/__init__.py tests/test_db_schema.py
git commit -m "feat: add price_audit and review_queue tables to SQLite schema"
```

---

## Task 3: Build `price_verifier.py` — Core + Supplier Lookup

**Files:**
- Create: `backend/agents/price_verifier.py`
- Create: `tests/test_price_verifier.py`

- [ ] **Step 1: Write failing tests for supplier lookup**

Create `tests/test_price_verifier.py`:

```python
import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest


@pytest.mark.asyncio
async def test_fetch_supplier_price_returns_float_on_success():
    """_fetch_supplier_price returns a float when Claude extracts a price."""
    mock_response = MagicMock()
    mock_response.text = "2.45"
    mock_message = MagicMock()
    mock_message.content = [mock_response]

    with patch("backend.agents.price_verifier.anthropic_client") as mock_client:
        mock_client.messages.create.return_value = mock_message
        with patch("backend.agents.price_verifier.httpx") as mock_httpx:
            mock_http_response = MagicMock()
            mock_http_response.status_code = 200
            mock_http_response.text = "<html>price $2.45 per LF</html>"
            mock_httpx.AsyncClient.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=mock_http_response
            )
            from backend.agents.price_verifier import _fetch_supplier_price
            result = await _fetch_supplier_price("Framing Lumber (2x4x8)", "LF", "homedepot")
    assert isinstance(result, float)
    assert result == 2.45


@pytest.mark.asyncio
async def test_fetch_supplier_price_returns_none_on_http_error():
    """_fetch_supplier_price returns None when the HTTP request fails."""
    with patch("backend.agents.price_verifier.httpx") as mock_httpx:
        mock_httpx.AsyncClient.return_value.__aenter__.return_value.get = AsyncMock(
            side_effect=Exception("connection refused")
        )
        from backend.agents.price_verifier import _fetch_supplier_price
        result = await _fetch_supplier_price("Framing Lumber (2x4x8)", "LF", "homedepot")
    assert result is None


@pytest.mark.asyncio
async def test_fetch_supplier_price_returns_none_when_claude_returns_no_price():
    """_fetch_supplier_price returns None when Claude says no price found."""
    mock_response = MagicMock()
    mock_response.text = "NO_PRICE"
    mock_message = MagicMock()
    mock_message.content = [mock_response]

    with patch("backend.agents.price_verifier.anthropic_client") as mock_client:
        mock_client.messages.create.return_value = mock_message
        with patch("backend.agents.price_verifier.httpx") as mock_httpx:
            mock_http_response = MagicMock()
            mock_http_response.status_code = 200
            mock_http_response.text = "<html>no results</html>"
            mock_httpx.AsyncClient.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=mock_http_response
            )
            from backend.agents.price_verifier import _fetch_supplier_price
            result = await _fetch_supplier_price("Unobtainium Beam", "EA", "homedepot")
    assert result is None
```

- [ ] **Step 2: Run to confirm FAIL**

```bash
uv run pytest tests/test_price_verifier.py -v
```

Expected: ImportError — `price_verifier` module does not exist yet.

- [ ] **Step 3: Create `backend/agents/price_verifier.py` with supplier lookup**

```python
"""
Price Verifier Agent — TakeoffAI
Verifies LLM-generated unit prices against web sources.

Three trigger paths: background (post-judge), on-demand (API), nightly batch (CSV).
All results written to price_audit table; deviations >5% flagged to review_queue.
"""

import asyncio
import csv
import json
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import quote_plus

import httpx
from anthropic import Anthropic

anthropic_client = Anthropic()

DB_PATH = str(Path(__file__).parent.parent / "data" / "takeoffai.db")
CSV_PATH = Path(__file__).parent.parent / "data" / "material_costs.csv"

DEVIATION_THRESHOLD_PCT = 5.0   # flag if abs deviation > 5%
AGREEMENT_SPREAD_PCT = 10.0     # sources "agree" if within 10% of each other
MIN_SOURCES_FOR_AUTO_UPDATE = 3

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

_SUPPLIER_URLS = {
    "homedepot": "https://www.homedepot.com/s/{query}",
    "lowes":     "https://www.lowes.com/search?searchTerm={query}",
}


async def _fetch_supplier_price(
    item: str,
    unit: str,
    supplier: str,
) -> Optional[float]:
    """
    Fetch a supplier search page and ask Claude to extract a unit price.

    Returns float price or None on any failure.
    """
    url_template = _SUPPLIER_URLS.get(supplier)
    if not url_template:
        return None

    url = url_template.format(query=quote_plus(item))

    try:
        async with httpx.AsyncClient(headers=_HEADERS, timeout=15.0, follow_redirects=True) as client:
            response = await client.get(url)
            if response.status_code != 200:
                return None
            html = response.text[:12_000]  # first 12KB is enough for search results
    except Exception:
        return None

    # Ask Claude to extract the price from the HTML snippet
    try:
        msg = anthropic_client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=64,
            system=(
                "You are a price extraction assistant. "
                "Given HTML from a supplier website, find the unit price for the specified item. "
                "Return ONLY the numeric price as a decimal (e.g. '2.45'). "
                "If no clear price is found, return exactly 'NO_PRICE'. No other text."
            ),
            messages=[{
                "role": "user",
                "content": (
                    f"Item: {item}\nUnit: {unit}\n\n"
                    f"HTML:\n{html}"
                ),
            }],
        )
        raw = msg.content[0].text.strip()
        if raw == "NO_PRICE":
            return None
        # Strip any currency symbols and parse
        numeric = re.sub(r"[^\d.]", "", raw)
        return float(numeric) if numeric else None
    except Exception:
        return None
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_price_verifier.py -v
```

Expected: 3 PASSED

- [ ] **Step 5: Commit**

```bash
git add backend/agents/price_verifier.py tests/test_price_verifier.py
git commit -m "feat: add price_verifier with supplier lookup phase"
```

---

## Task 4: Build `price_verifier.py` — Web Fallback + Confidence + CSV Update

**Files:**
- Modify: `backend/agents/price_verifier.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_price_verifier.py`:

```python
@pytest.mark.asyncio
async def test_web_search_fallback_returns_prices():
    """_web_search_price parses prices from DuckDuckGo HTML."""
    mock_response = MagicMock()
    mock_response.text = "2.50,2.60,2.55"
    mock_message = MagicMock()
    mock_message.content = [mock_response]

    with patch("backend.agents.price_verifier.anthropic_client") as mock_client:
        mock_client.messages.create.return_value = mock_message
        with patch("backend.agents.price_verifier.httpx") as mock_httpx:
            mock_http_response = MagicMock()
            mock_http_response.status_code = 200
            mock_http_response.text = "<html>lumber $2.50 per LF...</html>"
            mock_httpx.AsyncClient.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=mock_http_response
            )
            from backend.agents.price_verifier import _web_search_price
            prices = await _web_search_price("Framing Lumber (2x4x8)", "LF")
    assert isinstance(prices, list)
    assert all(isinstance(p, float) for p in prices)


def test_compute_deviation():
    """deviation_pct = (ai - mid) / mid * 100."""
    from backend.agents.price_verifier import _compute_deviation
    assert _compute_deviation(ai=1.10, verified_mid=1.00) == pytest.approx(10.0)
    assert _compute_deviation(ai=0.90, verified_mid=1.00) == pytest.approx(-10.0)
    assert _compute_deviation(ai=1.00, verified_mid=1.00) == pytest.approx(0.0)


def test_sources_agree_true():
    from backend.agents.price_verifier import _sources_agree
    assert _sources_agree([1.00, 1.05, 1.08]) is True   # max spread < 10%


def test_sources_agree_false():
    from backend.agents.price_verifier import _sources_agree
    assert _sources_agree([1.00, 1.50, 2.00]) is False   # spread > 10%


def test_update_seed_csv(tmp_path):
    """_update_seed_csv rewrites the matching row in material_costs.csv."""
    csv_path = tmp_path / "material_costs.csv"
    csv_path.write_text(
        "item,unit,low_cost,high_cost,trade_category\n"
        "Framing Lumber (2x4x8),LF,0.45,0.75,Framing\n"
        "Concrete (3000 PSI),CY,135.00,175.00,Concrete\n"
    )

    from backend.agents.price_verifier import _update_seed_csv
    updated = _update_seed_csv(
        item="Framing Lumber (2x4x8)",
        new_low=0.55,
        new_high=0.90,
        csv_path=csv_path,
    )
    assert updated is True
    rows = list(csv.DictReader(csv_path.open()))
    lumber = next(r for r in rows if r["item"] == "Framing Lumber (2x4x8)")
    assert float(lumber["low_cost"]) == pytest.approx(0.55)
    assert float(lumber["high_cost"]) == pytest.approx(0.90)
    # Other rows unchanged
    concrete = next(r for r in rows if r["item"] == "Concrete (3000 PSI)")
    assert float(concrete["low_cost"]) == pytest.approx(135.00)
```

- [ ] **Step 2: Run to confirm FAIL**

```bash
uv run pytest tests/test_price_verifier.py -v -k "fallback or deviation or agree or csv"
```

Expected: ImportError or AttributeError — functions not yet defined.

- [ ] **Step 3: Add web fallback + helpers to `price_verifier.py`**

Append to `backend/agents/price_verifier.py` after `_fetch_supplier_price`:

```python
async def _web_search_price(item: str, unit: str) -> list[float]:
    """
    Search DuckDuckGo for current unit prices. Returns list of floats found.
    Falls back to empty list on any failure.
    """
    query = quote_plus(f"{item} price per {unit} 2026 construction material cost")
    url = f"https://html.duckduckgo.com/html/?q={query}"

    try:
        async with httpx.AsyncClient(headers=_HEADERS, timeout=15.0, follow_redirects=True) as client:
            response = await client.get(url)
            if response.status_code != 200:
                return []
            html = response.text[:20_000]
    except Exception:
        return []

    try:
        msg = anthropic_client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=128,
            system=(
                "You are a price extraction assistant reading web search results. "
                "Extract all distinct unit prices you can find for the specified item. "
                "Return ONLY a comma-separated list of decimals (e.g. '2.45,2.60,2.55'). "
                "If no prices found, return 'NO_PRICE'. No other text."
            ),
            messages=[{
                "role": "user",
                "content": (
                    f"Item: {item}\nUnit: {unit}\n\n"
                    f"Search results HTML:\n{html}"
                ),
            }],
        )
        raw = msg.content[0].text.strip()
        if raw == "NO_PRICE":
            return []
        prices = []
        for part in raw.split(","):
            numeric = re.sub(r"[^\d.]", "", part.strip())
            if numeric:
                try:
                    prices.append(float(numeric))
                except ValueError:
                    pass
        return prices
    except Exception:
        return []


def _compute_deviation(ai: float, verified_mid: float) -> float:
    """Percent deviation of AI price from verified midpoint. Positive = AI over-estimated."""
    if verified_mid == 0:
        return 0.0
    return round((ai - verified_mid) / verified_mid * 100, 4)


def _sources_agree(prices: list[float]) -> bool:
    """True if all prices are within AGREEMENT_SPREAD_PCT of each other."""
    if len(prices) < 2:
        return True
    lo, hi = min(prices), max(prices)
    if lo == 0:
        return False
    spread_pct = (hi - lo) / lo * 100
    return spread_pct <= AGREEMENT_SPREAD_PCT


def _update_seed_csv(
    item: str,
    new_low: float,
    new_high: float,
    csv_path: Path = CSV_PATH,
) -> bool:
    """
    Update low_cost and high_cost for a matching item row in material_costs.csv.
    Uses a temp-file-then-rename pattern for atomicity.
    Returns True if row was found and updated, False otherwise.
    """
    if not csv_path.exists():
        return False

    rows = list(csv.DictReader(csv_path.open(newline="", encoding="utf-8")))
    fieldnames = list(rows[0].keys()) if rows else ["item", "unit", "low_cost", "high_cost", "trade_category"]

    updated = False
    for row in rows:
        if row["item"].strip().lower() == item.strip().lower():
            row["low_cost"] = str(round(new_low, 4))
            row["high_cost"] = str(round(new_high, 4))
            updated = True

    if not updated:
        return False

    # Write atomically via temp file
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".csv", dir=csv_path.parent,
        delete=False, newline="", encoding="utf-8"
    ) as tmp:
        writer = csv.DictWriter(tmp, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
        tmp_path = Path(tmp.name)

    tmp_path.replace(csv_path)
    return True
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_price_verifier.py -v
```

Expected: All passing (or skip supplier tests if network is unavailable — mocks handle it).

- [ ] **Step 5: Commit**

```bash
git add backend/agents/price_verifier.py tests/test_price_verifier.py
git commit -m "feat: add web search fallback, deviation math, and CSV auto-update to price_verifier"
```

---

## Task 5: Build `price_verifier.py` — `verify_line_items()` Main Function

**Files:**
- Modify: `backend/agents/price_verifier.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_price_verifier.py`:

```python
@pytest.mark.asyncio
async def test_verify_line_items_writes_audit_record(tmp_path):
    """verify_line_items writes one audit record per line item to price_audit."""
    import aiosqlite

    db_path = str(tmp_path / "test.db")
    from backend.api.main import _CREATE_TABLES
    async with aiosqlite.connect(db_path) as db:
        await db.executescript(_CREATE_TABLES)
        await db.commit()

    line_items = [{
        "description": "Framing Lumber (2x4x8)",
        "unit": "LF",
        "unit_material_cost": 0.60,
        "unit_labor_cost": 0.20,
        "quantity": 1000,
        "subtotal": 800.0,
    }]

    with patch("backend.agents.price_verifier.DB_PATH", db_path):
        with patch("backend.agents.price_verifier._fetch_supplier_price", AsyncMock(return_value=0.65)):
            with patch("backend.agents.price_verifier._web_search_price", AsyncMock(return_value=[])):
                from backend.agents.price_verifier import verify_line_items
                records = await verify_line_items(line_items, triggered_by="on_demand")

    assert len(records) == 1
    assert records[0]["line_item"] == "Framing Lumber (2x4x8)"
    assert records[0]["ai_unit_cost"] == pytest.approx(0.60)

    async with aiosqlite.connect(db_path) as db:
        async with db.execute("SELECT COUNT(*) FROM price_audit") as cur:
            count = (await cur.fetchone())[0]
    assert count == 1


@pytest.mark.asyncio
async def test_verify_line_items_flags_deviation_over_5_pct(tmp_path):
    """Items with >5% deviation are flagged and inserted into review_queue."""
    import aiosqlite

    db_path = str(tmp_path / "test.db")
    from backend.api.main import _CREATE_TABLES
    async with aiosqlite.connect(db_path) as db:
        await db.executescript(_CREATE_TABLES)
        await db.commit()

    # AI said $0.60, web says $1.00 → deviation = -40% → flagged
    line_items = [{
        "description": "Framing Lumber (2x4x8)",
        "unit": "LF",
        "unit_material_cost": 0.60,
        "unit_labor_cost": 0.20,
        "quantity": 1000,
        "subtotal": 800.0,
    }]

    with patch("backend.agents.price_verifier.DB_PATH", db_path):
        with patch("backend.agents.price_verifier._fetch_supplier_price",
                   AsyncMock(side_effect=[1.00, 1.02])):  # 2 supplier hits
            with patch("backend.agents.price_verifier._web_search_price",
                       AsyncMock(return_value=[0.99])):  # 1 web hit → 3 total, agree
                from backend.agents.price_verifier import verify_line_items
                records = await verify_line_items(line_items, triggered_by="on_demand")

    assert records[0]["flagged"] == 1

    async with aiosqlite.connect(db_path) as db:
        async with db.execute("SELECT COUNT(*) FROM review_queue WHERE status='pending'") as cur:
            count = (await cur.fetchone())[0]
    assert count == 1


@pytest.mark.asyncio
async def test_verify_line_items_auto_updates_csv_with_3_agreeing_sources(tmp_path):
    """3+ agreeing sources → auto_updated=1 and CSV is rewritten."""
    import aiosqlite

    db_path = str(tmp_path / "test.db")
    csv_path = tmp_path / "material_costs.csv"
    csv_path.write_text(
        "item,unit,low_cost,high_cost,trade_category\n"
        "Framing Lumber (2x4x8),LF,0.45,0.75,Framing\n"
    )

    from backend.api.main import _CREATE_TABLES
    async with aiosqlite.connect(db_path) as db:
        await db.executescript(_CREATE_TABLES)
        await db.commit()

    line_items = [{
        "description": "Framing Lumber (2x4x8)",
        "unit": "LF",
        "unit_material_cost": 0.60,
        "unit_labor_cost": 0.20,
        "quantity": 1000,
        "subtotal": 800.0,
    }]

    with patch("backend.agents.price_verifier.DB_PATH", db_path):
        with patch("backend.agents.price_verifier.CSV_PATH", csv_path):
            with patch("backend.agents.price_verifier._fetch_supplier_price",
                       AsyncMock(side_effect=[1.00, 1.02])):
                with patch("backend.agents.price_verifier._web_search_price",
                           AsyncMock(return_value=[0.98])):
                    from backend.agents.price_verifier import verify_line_items
                    records = await verify_line_items(line_items, triggered_by="nightly")

    assert records[0]["auto_updated"] == 1
    rows = list(csv.DictReader(csv_path.open()))
    lumber = next(r for r in rows if r["item"] == "Framing Lumber (2x4x8)")
    # low_cost = min of verified prices, high_cost = max
    assert float(lumber["low_cost"]) == pytest.approx(0.98)
    assert float(lumber["high_cost"]) == pytest.approx(1.02)
```

- [ ] **Step 2: Run to confirm FAIL**

```bash
uv run pytest tests/test_price_verifier.py -v -k "writes_audit or flags_deviation or auto_updates"
```

Expected: ImportError — `verify_line_items` not yet defined.

- [ ] **Step 3: Add `verify_line_items` to `price_verifier.py`**

Append to `backend/agents/price_verifier.py`:

```python
async def _write_audit_record(
    db_path: str,
    triggered_by: str,
    tournament_id: Optional[int],
    line_item: str,
    unit: str,
    ai_unit_cost: float,
    all_prices: list[float],
    sources_meta: list[dict],
) -> dict:
    """Write one row to price_audit and optionally to review_queue. Returns audit dict."""
    import aiosqlite

    source_count = len(all_prices)
    verified_low = min(all_prices) if all_prices else None
    verified_high = max(all_prices) if all_prices else None
    verified_mid = round(sum(all_prices) / len(all_prices), 4) if all_prices else None
    deviation_pct = _compute_deviation(ai_unit_cost, verified_mid) if verified_mid else None
    flagged = 1 if (deviation_pct is not None and abs(deviation_pct) > DEVIATION_THRESHOLD_PCT) else 0
    sources_json = json.dumps(sources_meta)

    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            """
            INSERT INTO price_audit
              (triggered_by, tournament_id, line_item, unit, ai_unit_cost,
               verified_low, verified_high, verified_mid, deviation_pct,
               sources, source_count, flagged)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (triggered_by, tournament_id, line_item, unit, ai_unit_cost,
             verified_low, verified_high, verified_mid, deviation_pct,
             sources_json, source_count, flagged),
        ) as cur:
            audit_id = cur.lastrowid
        await db.commit()

        if flagged:
            await db.execute(
                """
                INSERT INTO review_queue
                  (audit_id, line_item, unit, ai_unit_cost, verified_mid,
                   deviation_pct, sources)
                VALUES (?,?,?,?,?,?,?)
                """,
                (audit_id, line_item, unit, ai_unit_cost, verified_mid,
                 deviation_pct, sources_json),
            )
            await db.commit()

    return {
        "audit_id": audit_id,
        "line_item": line_item,
        "unit": unit,
        "ai_unit_cost": ai_unit_cost,
        "verified_low": verified_low,
        "verified_high": verified_high,
        "verified_mid": verified_mid,
        "deviation_pct": deviation_pct,
        "source_count": source_count,
        "flagged": flagged,
        "auto_updated": 0,
    }


async def verify_line_items(
    line_items: list[dict],
    triggered_by: str,
    tournament_id: Optional[int] = None,
) -> list[dict]:
    """
    Verify unit prices for a list of estimate line items against web sources.

    For each item:
    1. Try Home Depot + Lowe's supplier lookup (Phase 1)
    2. If < 2 results, add DuckDuckGo web search prices (Phase 2)
    3. If 3+ agreeing sources (within 10%): auto-update material_costs.csv
    4. Write audit record; flag + queue if deviation > 5%

    Returns list of audit record dicts.
    """
    records = []

    for item in line_items:
        description = item.get("description", "")
        unit = item.get("unit", "EA")
        ai_unit_cost = float(item.get("unit_material_cost", 0.0))

        if not description or ai_unit_cost == 0:
            continue

        all_prices: list[float] = []
        sources_meta: list[dict] = []
        retrieved_at = datetime.now(timezone.utc).isoformat()

        # Phase 1: Supplier sites
        for supplier in ("homedepot", "lowes"):
            price = await _fetch_supplier_price(description, unit, supplier)
            if price is not None:
                all_prices.append(price)
                sources_meta.append({
                    "source": supplier,
                    "price": price,
                    "retrieved_at": retrieved_at,
                })

        # Phase 2: Web search fallback if < 2 results
        if len(all_prices) < 2:
            web_prices = await _web_search_price(description, unit)
            for p in web_prices[:3]:  # cap at 3 web results
                all_prices.append(p)
                sources_meta.append({
                    "source": "web_search",
                    "price": p,
                    "retrieved_at": retrieved_at,
                })

        # Phase 3: Confidence decision
        auto_updated = 0
        if len(all_prices) >= MIN_SOURCES_FOR_AUTO_UPDATE and _sources_agree(all_prices):
            updated = _update_seed_csv(
                item=description,
                new_low=min(all_prices),
                new_high=max(all_prices),
            )
            if updated:
                auto_updated = 1

        record = await _write_audit_record(
            db_path=DB_PATH,
            triggered_by=triggered_by,
            tournament_id=tournament_id,
            line_item=description,
            unit=unit,
            ai_unit_cost=ai_unit_cost,
            all_prices=all_prices,
            sources_meta=sources_meta,
        )
        record["auto_updated"] = auto_updated

        # Persist auto_updated flag to DB
        if auto_updated:
            import aiosqlite
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    "UPDATE price_audit SET auto_updated = 1 WHERE id = ?",
                    (record["audit_id"],),
                )
                await db.commit()

        records.append(record)

    return records
```

- [ ] **Step 4: Run all price verifier tests**

```bash
uv run pytest tests/test_price_verifier.py -v
```

Expected: All PASSED (network calls are mocked).

- [ ] **Step 5: Commit**

```bash
git add backend/agents/price_verifier.py tests/test_price_verifier.py
git commit -m "feat: complete verify_line_items with audit writes, flagging, and CSV auto-update"
```

---

## Task 6: Extend `feedback_loop.py` — Calibration Functions

**Files:**
- Modify: `backend/agents/feedback_loop.py`
- Create: `tests/test_feedback_loop_calibration.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_feedback_loop_calibration.py`:

```python
import json
import tempfile
from pathlib import Path
import pytest


def _make_profile(client_id: str, tmp_path: Path) -> Path:
    profile = {
        "client_id": client_id,
        "created_at": "2026-01-01T00:00:00",
        "winning_examples": [],
        "agent_elo": {a: 1000 for a in
                      ["conservative", "balanced", "aggressive", "historical_match", "market_beater"]},
        "stats": {
            "total_tournaments": 0,
            "win_rate_by_agent": {},
            "avg_winning_bid": 0.0,
            "avg_winning_margin": 0.0,
            "wins_by_agent": {},
        },
    }
    path = tmp_path / f"{client_id}.json"
    path.write_text(json.dumps(profile))
    return path


def test_compute_brier_score_perfect():
    from backend.agents.feedback_loop import _compute_brier_score
    # Predicted 1.0 for wins, 0.0 for losses → score = 0
    score = _compute_brier_score([1.0, 1.0, 0.0], [1, 1, 0])
    assert score == pytest.approx(0.0)


def test_compute_brier_score_worst():
    from backend.agents.feedback_loop import _compute_brier_score
    # Predicted 0.0 for wins → score = 1.0
    score = _compute_brier_score([0.0, 0.0], [1, 1])
    assert score == pytest.approx(1.0)


def test_compute_brier_score_empty():
    from backend.agents.feedback_loop import _compute_brier_score
    score = _compute_brier_score([], [])
    assert score is None


def test_record_actual_outcome_updates_calibration(tmp_path, monkeypatch):
    """record_actual_outcome writes deviation history and computes Brier score."""
    import aiosqlite
    import asyncio
    import tempfile as tf

    # Patch PROFILES_DIR to tmp_path
    import backend.agents.feedback_loop as fl
    monkeypatch.setattr(fl, "PROFILES_DIR", tmp_path)
    profile_path = _make_profile("client1", tmp_path)

    # Create an in-memory SQLite DB with tournament data
    db_path = str(tmp_path / "test.db")
    from backend.api.main import _CREATE_TABLES

    async def setup_db():
        async with aiosqlite.connect(db_path) as db:
            await db.executescript(_CREATE_TABLES)
            await db.execute(
                "INSERT INTO bid_tournaments (id, client_id, project_description, zip_code) VALUES (?,?,?,?)",
                (1, "client1", "40x60 metal building", "76801")
            )
            for agent, bid in [("conservative", 170000), ("balanced", 155000),
                                ("aggressive", 140000), ("historical_match", 160000),
                                ("market_beater", 152000)]:
                await db.execute(
                    "INSERT INTO tournament_entries (tournament_id, agent_name, total_bid, won) VALUES (?,?,?,?)",
                    (1, agent, bid, 1 if agent == "balanced" else 0)
                )
            await db.commit()

    asyncio.get_event_loop().run_until_complete(setup_db())

    monkeypatch.setattr(fl, "DB_PATH", db_path)

    result = fl.record_actual_outcome(
        client_id="client1",
        tournament_id=1,
        actual_cost=150000.0,
        won=True,
        win_probability=0.65,
    )

    assert "calibration" in result
    cal = result["calibration"]
    assert "agent_deviation_history" in cal
    assert "balanced" in cal["agent_deviation_history"]
    # balanced bid 155000, actual 150000 → deviation = (155000-150000)/150000*100 = 3.33%
    assert cal["agent_deviation_history"]["balanced"][-1] == pytest.approx(3.33, abs=0.1)
    assert "brier_score" in cal


def test_record_actual_outcome_red_flags_high_deviation_agent(tmp_path, monkeypatch):
    """Agent with >5% avg deviation over last 5 jobs gets red-flagged."""
    import aiosqlite
    import asyncio
    import backend.agents.feedback_loop as fl

    monkeypatch.setattr(fl, "PROFILES_DIR", tmp_path)

    # Pre-seed profile with 4 existing deviations >5% for 'aggressive'
    profile = json.loads(_make_profile("client2", tmp_path).read_text())
    profile["calibration"] = {
        "win_prob_predictions": [],
        "win_prob_actuals": [],
        "brier_score": None,
        "confidence_accuracy": {},
        "agent_deviation_history": {
            "conservative": [1.0, 1.5, 0.8, 1.2],
            "balanced": [0.5, 1.0, 0.3, 0.8],
            "aggressive": [7.0, 8.0, 6.5, 9.0],  # already 4 high deviations
            "historical_match": [0.5, 0.3, 0.8, 0.2],
            "market_beater": [2.0, 1.5, 1.8, 2.2],
        },
        "red_flagged_agents": [],
    }
    (tmp_path / "client2.json").write_text(json.dumps(profile))

    db_path = str(tmp_path / "test2.db")
    from backend.api.main import _CREATE_TABLES

    async def setup_db():
        async with aiosqlite.connect(db_path) as db:
            await db.executescript(_CREATE_TABLES)
            await db.execute(
                "INSERT INTO bid_tournaments (id, client_id, project_description, zip_code) VALUES (?,?,?,?)",
                (2, "client2", "office buildout", "76801")
            )
            for agent, bid in [("conservative", 105000), ("balanced", 100500),
                                ("aggressive", 115000), ("historical_match", 101000),
                                ("market_beater", 102000)]:
                await db.execute(
                    "INSERT INTO tournament_entries (tournament_id, agent_name, total_bid, won) VALUES (?,?,?,?)",
                    (2, agent, bid, 1 if agent == "balanced" else 0)
                )
            await db.commit()

    asyncio.get_event_loop().run_until_complete(setup_db())
    monkeypatch.setattr(fl, "DB_PATH", db_path)

    # aggressive bid 115000, actual 100000 → deviation = 15% → 5th high deviation
    result = fl.record_actual_outcome(
        client_id="client2",
        tournament_id=2,
        actual_cost=100000.0,
        won=True,
        win_probability=0.70,
    )

    assert "aggressive" in result["calibration"]["red_flagged_agents"]


def test_get_agent_accuracy_report(tmp_path, monkeypatch):
    """get_agent_accuracy_report returns per-agent avg deviation and flag status."""
    import backend.agents.feedback_loop as fl
    monkeypatch.setattr(fl, "PROFILES_DIR", tmp_path)

    profile = json.loads(_make_profile("client3", tmp_path).read_text())
    profile["calibration"] = {
        "win_prob_predictions": [0.7, 0.6],
        "win_prob_actuals": [1, 0],
        "brier_score": 0.185,
        "confidence_accuracy": {},
        "agent_deviation_history": {
            "conservative": [1.0, 2.0, 1.5],
            "balanced": [0.5, 0.3, 0.4],
            "aggressive": [8.0, 7.5, 9.0],
            "historical_match": [0.2, 0.1, 0.3],
            "market_beater": [3.0, 2.5, 2.8],
        },
        "red_flagged_agents": ["aggressive"],
    }
    (tmp_path / "client3.json").write_text(json.dumps(profile))

    report = fl.get_agent_accuracy_report("client3")
    assert report["aggressive"]["avg_deviation_pct"] == pytest.approx(8.167, abs=0.01)
    assert report["aggressive"]["red_flagged"] is True
    assert report["balanced"]["red_flagged"] is False
    assert report["brier_score"] == pytest.approx(0.185)
    assert "recommended_agent" in report
```

- [ ] **Step 2: Run to confirm FAIL**

```bash
uv run pytest tests/test_feedback_loop_calibration.py -v
```

Expected: ImportError — `_compute_brier_score` not yet defined.

- [ ] **Step 3: Append calibration functions to `feedback_loop.py`**

At the end of `backend/agents/feedback_loop.py`, append:

```python
# ── Calibration & Accuracy (appended — no existing functions modified) ────────

import aiosqlite as _aiosqlite  # local alias to avoid top-level import conflict

DB_PATH = str(Path(__file__).parent.parent / "data" / "takeoffai.db")

RED_FLAG_DEVIATION_THRESHOLD = 5.0   # % average deviation to red-flag an agent
RED_FLAG_LOOKBACK = 5                # number of most recent jobs to consider


def _compute_brier_score(
    predictions: list[float],
    actuals: list[int],
) -> Optional[float]:
    """
    Brier Score = (1/N) * sum((f_i - o_i)^2)
    f_i: predicted win probability (0–1)
    o_i: actual outcome (1=won, 0=lost)
    Lower is better; < 0.25 = well-calibrated.
    Returns None if no data.
    """
    from typing import Optional
    if not predictions or len(predictions) != len(actuals):
        return None
    n = len(predictions)
    return round(sum((p - a) ** 2 for p, a in zip(predictions, actuals)) / n, 4)


def record_actual_outcome(
    client_id: str,
    tournament_id: int,
    actual_cost: float,
    won: bool,
    win_probability: Optional[float] = None,
) -> dict:
    """
    Record actual job outcome and update calibration data.

    - Loads all agent entries for tournament_id from SQLite
    - Computes per-agent deviation: (agent_bid - actual_cost) / actual_cost * 100
    - Appends to calibration.agent_deviation_history (keeps last 5 per agent)
    - Red-flags agents whose last 5 deviations average > RED_FLAG_DEVIATION_THRESHOLD
    - Appends win_probability prediction + actual outcome; recomputes Brier score
    - Writes updated profile to disk; returns updated profile dict
    """
    import asyncio
    import aiosqlite

    async def _load_entries():
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT agent_name, total_bid FROM tournament_entries WHERE tournament_id = ?",
                (tournament_id,),
            ) as cur:
                return [dict(r) for r in await cur.fetchall()]

    entries = asyncio.get_event_loop().run_until_complete(_load_entries())

    if not entries:
        raise ValueError(f"No entries found for tournament {tournament_id}")

    path = _profile_path(client_id)
    profile = json.loads(path.read_text()) if path.exists() else _empty_profile(client_id)

    # Initialise calibration block if absent
    cal = profile.setdefault("calibration", {
        "win_prob_predictions": [],
        "win_prob_actuals": [],
        "brier_score": None,
        "confidence_accuracy": {},
        "agent_deviation_history": {agent: [] for agent in ALL_AGENTS},
        "red_flagged_agents": [],
    })
    cal.setdefault("agent_deviation_history", {agent: [] for agent in ALL_AGENTS})
    cal.setdefault("red_flagged_agents", [])

    # Compute per-agent deviation
    for entry in entries:
        agent = entry["agent_name"]
        bid = float(entry.get("total_bid") or 0.0)
        if actual_cost > 0 and bid > 0:
            dev = round((bid - actual_cost) / actual_cost * 100, 4)
        else:
            dev = 0.0
        history = cal["agent_deviation_history"].setdefault(agent, [])
        history.append(dev)
        # Keep only last RED_FLAG_LOOKBACK entries
        cal["agent_deviation_history"][agent] = history[-RED_FLAG_LOOKBACK:]

    # Red-flag agents
    red_flagged = set(cal.get("red_flagged_agents", []))
    for agent in ALL_AGENTS:
        history = cal["agent_deviation_history"].get(agent, [])
        if len(history) >= RED_FLAG_LOOKBACK:
            avg_dev = sum(abs(d) for d in history) / len(history)
            if avg_dev > RED_FLAG_DEVIATION_THRESHOLD:
                red_flagged.add(agent)
            else:
                red_flagged.discard(agent)
    cal["red_flagged_agents"] = sorted(red_flagged)

    # Win probability calibration
    if win_probability is not None:
        cal["win_prob_predictions"].append(float(win_probability))
        cal["win_prob_actuals"].append(1 if won else 0)
    cal["brier_score"] = _compute_brier_score(
        cal["win_prob_predictions"],
        cal["win_prob_actuals"],
    )

    path.write_text(json.dumps(profile, indent=2))
    return profile


def get_agent_accuracy_report(client_id: str) -> dict:
    """
    Return per-agent accuracy statistics and calibration data for a client.

    Returns dict with:
    - Per-agent: avg_deviation_pct (last 5 jobs), red_flagged bool, deviation_history
    - brier_score: overall win probability calibration score
    - recommended_agent: agent with lowest avg deviation (not red-flagged)
    """
    path = _profile_path(client_id)
    if not path.exists():
        raise ValueError(f"Client profile '{client_id}' not found")

    profile = json.loads(path.read_text())
    cal = profile.get("calibration", {})
    deviation_history = cal.get("agent_deviation_history", {})
    red_flagged = set(cal.get("red_flagged_agents", []))

    report: dict = {}
    for agent in ALL_AGENTS:
        history = deviation_history.get(agent, [])
        recent = history[-RED_FLAG_LOOKBACK:] if history else []
        avg_dev = round(sum(abs(d) for d in recent) / len(recent), 4) if recent else None
        report[agent] = {
            "avg_deviation_pct": avg_dev,
            "deviation_history": recent,
            "red_flagged": agent in red_flagged,
        }

    # Recommended: lowest avg deviation, not red-flagged
    ranked = [
        (a, report[a]["avg_deviation_pct"])
        for a in ALL_AGENTS
        if report[a]["avg_deviation_pct"] is not None
        and not report[a]["red_flagged"]
    ]
    ranked.sort(key=lambda x: x[1])
    report["recommended_agent"] = ranked[0][0] if ranked else None
    report["brier_score"] = cal.get("brier_score")
    report["win_prob_predictions_count"] = len(cal.get("win_prob_predictions", []))

    return report
```

Also add `Optional` import at the top of `feedback_loop.py` if not present:

```python
from typing import Optional
```

- [ ] **Step 4: Run calibration tests**

```bash
uv run pytest tests/test_feedback_loop_calibration.py -v
```

Expected: All PASSED

- [ ] **Step 5: Commit**

```bash
git add backend/agents/feedback_loop.py tests/test_feedback_loop_calibration.py
git commit -m "feat: add record_actual_outcome, get_agent_accuracy_report, and Brier scoring to feedback_loop"
```

---

## Task 7: Hook Verifier into `judge.py`

**Files:**
- Modify: `backend/agents/judge.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_judge_hook.py`:

```python
import pytest
from unittest.mock import AsyncMock, patch, MagicMock


@pytest.mark.asyncio
async def test_judge_triggers_background_verification():
    """After judging, verify_line_items is called as a background task."""
    called_args = {}

    async def fake_verify(line_items, triggered_by, tournament_id=None):
        called_args["triggered_by"] = triggered_by
        called_args["tournament_id"] = tournament_id
        return []

    import json
    import tempfile
    import aiosqlite
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "test.db")
        from backend.api.main import _CREATE_TABLES
        async with aiosqlite.connect(db_path) as db:
            await db.executescript(_CREATE_TABLES)
            await db.execute(
                "INSERT INTO bid_tournaments (id, client_id, project_description, zip_code, status) "
                "VALUES (?,?,?,?,?)",
                (1, "client1", "test project", "76801", "pending")
            )
            line_items_json = json.dumps({
                "line_items": [
                    {"description": "Concrete", "unit": "CY", "unit_material_cost": 150.0,
                     "quantity": 10, "subtotal": 1500.0}
                ]
            })
            await db.execute(
                "INSERT INTO tournament_entries (tournament_id, agent_name, total_bid, line_items_json, won) "
                "VALUES (?,?,?,?,?)",
                (1, "balanced", 10000.0, line_items_json, 0)
            )
            await db.commit()

        with patch("backend.agents.judge.DB_PATH", db_path):
            with patch("backend.agents.judge.asyncio.to_thread", new=AsyncMock(return_value={})):
                with patch("backend.agents.judge.verify_line_items", new=fake_verify):
                    from backend.agents.judge import judge_tournament
                    await judge_tournament(
                        tournament_id=1,
                        winner_agent_name="balanced",
                    )
                    # Give background task a moment to run
                    import asyncio
                    await asyncio.sleep(0.05)

    assert called_args.get("triggered_by") == "background"
    assert called_args.get("tournament_id") == 1
```

- [ ] **Step 2: Run to confirm FAIL**

```bash
uv run pytest tests/test_judge_hook.py -v
```

Expected: FAIL — `verify_line_items` not imported or not called.

- [ ] **Step 3: Modify `judge.py` to add background verification hook**

In `backend/agents/judge.py`, add the import at the top (after existing imports):

```python
from backend.agents.price_verifier import verify_line_items
```

Then at the end of `judge_tournament()`, after the feedback loop block, add:

```python
    # ── Background price verification of winning estimate ─────────────────────
    if winner_entry:
        raw_estimate = winner_entry.get("line_items_json", "{}")
        try:
            estimate = json.loads(raw_estimate) if isinstance(raw_estimate, str) else raw_estimate
        except Exception:
            estimate = {}
        line_items = estimate.get("line_items", [])
        if line_items:
            asyncio.create_task(
                verify_line_items(
                    line_items=line_items,
                    triggered_by="background",
                    tournament_id=tournament_id,
                )
            )
```

- [ ] **Step 4: Run test**

```bash
uv run pytest tests/test_judge_hook.py -v
```

Expected: PASSED

- [ ] **Step 5: Commit**

```bash
git add backend/agents/judge.py tests/test_judge_hook.py
git commit -m "feat: trigger background price verification after judge_tournament completes"
```

---

## Task 8: Build `verification.py` API Router

**Files:**
- Create: `backend/api/verification.py`
- Create: `tests/test_verification_api.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_verification_api.py`:

```python
import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import aiosqlite
import pytest
from fastapi.testclient import TestClient


def make_test_app(db_path: str):
    """Create a minimal FastAPI app with the verification router wired up."""
    from fastapi import FastAPI
    from backend.api.verification import verification_router
    import backend.api.verification as vmod
    vmod.DB_PATH = db_path
    app = FastAPI()
    app.include_router(verification_router, prefix="/api")
    return app


@pytest.fixture
def db_and_client(tmp_path):
    db_path = str(tmp_path / "test.db")
    from backend.api.main import _CREATE_TABLES
    import asyncio
    asyncio.get_event_loop().run_until_complete(
        _setup_db(db_path, _CREATE_TABLES)
    )
    app = make_test_app(db_path)
    with TestClient(app) as client:
        yield db_path, client


async def _setup_db(db_path, ddl):
    async with aiosqlite.connect(db_path) as db:
        await db.executescript(ddl)
        await db.commit()


def test_get_audit_empty(db_and_client):
    _, client = db_and_client
    resp = client.get("/api/verify/audit")
    assert resp.status_code == 200
    assert resp.json() == []


def test_get_queue_empty(db_and_client):
    _, client = db_and_client
    resp = client.get("/api/verify/queue")
    assert resp.status_code == 200
    assert resp.json() == []


def test_verify_estimate_endpoint(db_and_client):
    db_path, client = db_and_client
    with patch("backend.api.verification.verify_line_items", new=AsyncMock(return_value=[{
        "audit_id": 1, "line_item": "Concrete", "unit": "CY",
        "ai_unit_cost": 150.0, "verified_mid": 160.0,
        "deviation_pct": -6.25, "flagged": 1, "source_count": 3, "auto_updated": 0,
    }])):
        resp = client.post("/api/verify/estimate", json={
            "line_items": [{"description": "Concrete", "unit": "CY", "unit_material_cost": 150.0}]
        })
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["flagged"] == 1


def test_patch_queue_resolve(db_and_client):
    db_path, client = db_and_client

    import asyncio
    async def seed_queue():
        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                "INSERT INTO price_audit (triggered_by, line_item, unit, ai_unit_cost, source_count) "
                "VALUES (?,?,?,?,?)", ("test", "Lumber", "LF", 0.60, 0)
            )
            await db.execute(
                "INSERT INTO review_queue (audit_id, line_item, unit, ai_unit_cost, verified_mid, deviation_pct) "
                "VALUES (?,?,?,?,?,?)", (1, "Lumber", "LF", 0.60, 1.00, -40.0)
            )
            await db.commit()

    asyncio.get_event_loop().run_until_complete(seed_queue())

    resp = client.patch("/api/verify/queue/1", json={
        "status": "approved",
        "reviewer_notes": "confirmed price increase"
    })
    assert resp.status_code == 200
    assert resp.json()["status"] == "approved"


def test_post_outcome(db_and_client, tmp_path):
    db_path, client = db_and_client

    import asyncio
    async def seed_tournament():
        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                "INSERT INTO bid_tournaments (id, client_id, project_description, zip_code) "
                "VALUES (?,?,?,?)", (10, "clientX", "test project", "76801")
            )
            await db.execute(
                "INSERT INTO tournament_entries (tournament_id, agent_name, total_bid, won) "
                "VALUES (?,?,?,?)", (10, "balanced", 100000.0, 1)
            )
            await db.commit()

    asyncio.get_event_loop().run_until_complete(seed_tournament())

    with patch("backend.api.verification.record_actual_outcome", return_value={"calibration": {}}):
        resp = client.post("/api/verify/outcome", json={
            "client_id": "clientX",
            "tournament_id": 10,
            "actual_cost": 95000.0,
            "won": True,
            "win_probability": 0.65,
        })
    assert resp.status_code == 200


def test_get_accuracy_report(db_and_client, tmp_path, monkeypatch):
    _, client = db_and_client
    with patch("backend.api.verification.get_agent_accuracy_report", return_value={
        "conservative": {"avg_deviation_pct": 1.5, "red_flagged": False, "deviation_history": []},
        "balanced": {"avg_deviation_pct": 0.4, "red_flagged": False, "deviation_history": []},
        "aggressive": {"avg_deviation_pct": 8.0, "red_flagged": True, "deviation_history": []},
        "historical_match": {"avg_deviation_pct": 0.3, "red_flagged": False, "deviation_history": []},
        "market_beater": {"avg_deviation_pct": 2.1, "red_flagged": False, "deviation_history": []},
        "recommended_agent": "historical_match",
        "brier_score": 0.18,
        "win_prob_predictions_count": 5,
    }):
        resp = client.get("/api/verify/accuracy/clientX")
    assert resp.status_code == 200
    data = resp.json()
    assert data["recommended_agent"] == "historical_match"
    assert data["aggressive"]["red_flagged"] is True
```

- [ ] **Step 2: Run to confirm FAIL**

```bash
uv run pytest tests/test_verification_api.py -v
```

Expected: ImportError — `verification_router` not yet defined.

- [ ] **Step 3: Create `backend/api/verification.py`**

```python
"""
Verification API — TakeoffAI
Endpoints for price audit, review queue, on-demand verification, and calibration.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

import aiosqlite
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.agents.feedback_loop import get_agent_accuracy_report, record_actual_outcome
from backend.agents.price_verifier import verify_line_items

DB_PATH = str(Path(__file__).parent.parent / "data" / "takeoffai.db")

verification_router = APIRouter()


# ── Pydantic models ──────────────────────────────────────────────────────────

class VerifyEstimateRequest(BaseModel):
    line_items: list[dict]
    tournament_id: Optional[int] = None


class OutcomeRequest(BaseModel):
    client_id: str
    tournament_id: int
    actual_cost: float
    won: bool
    win_probability: Optional[float] = None


class QueueResolveRequest(BaseModel):
    status: Literal["approved", "rejected"]
    reviewer_notes: Optional[str] = None


# ── Endpoints ────────────────────────────────────────────────────────────────

@verification_router.post("/verify/estimate")
async def verify_estimate(req: VerifyEstimateRequest):
    """On-demand: verify line items from any estimate against web sources."""
    try:
        records = await verify_line_items(
            line_items=req.line_items,
            triggered_by="on_demand",
            tournament_id=req.tournament_id,
        )
        return records
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@verification_router.get("/verify/audit")
async def list_audit(
    flagged: Optional[int] = None,
    triggered_by: Optional[str] = None,
    line_item: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: int = 100,
):
    """List price audit records with optional filters."""
    try:
        clauses = []
        params = []
        if flagged is not None:
            clauses.append("flagged = ?")
            params.append(flagged)
        if triggered_by:
            clauses.append("triggered_by = ?")
            params.append(triggered_by)
        if line_item:
            clauses.append("line_item LIKE ?")
            params.append(f"%{line_item}%")
        if date_from:
            clauses.append("created_at >= ?")
            params.append(date_from)
        if date_to:
            clauses.append("created_at <= ?")
            params.append(date_to)

        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)

        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                f"SELECT * FROM price_audit {where} ORDER BY created_at DESC LIMIT ?",
                params,
            ) as cur:
                rows = [dict(r) for r in await cur.fetchall()]
        return rows
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@verification_router.get("/verify/queue")
async def list_queue(status: Optional[str] = "pending", limit: int = 100):
    """List review queue items."""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            if status:
                async with db.execute(
                    "SELECT * FROM review_queue WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                    (status, limit),
                ) as cur:
                    rows = [dict(r) for r in await cur.fetchall()]
            else:
                async with db.execute(
                    "SELECT * FROM review_queue ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ) as cur:
                    rows = [dict(r) for r in await cur.fetchall()]
        return rows
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@verification_router.patch("/verify/queue/{queue_id}")
async def resolve_queue_item(queue_id: int, req: QueueResolveRequest):
    """Approve or reject a flagged price deviation."""
    try:
        resolved_at = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT id FROM review_queue WHERE id = ?", (queue_id,)
            ) as cur:
                if not await cur.fetchone():
                    raise HTTPException(status_code=404, detail=f"Queue item {queue_id} not found")

            await db.execute(
                "UPDATE review_queue SET status = ?, reviewer_notes = ?, resolved_at = ? WHERE id = ?",
                (req.status, req.reviewer_notes, resolved_at, queue_id),
            )
            await db.commit()

            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM review_queue WHERE id = ?", (queue_id,)
            ) as cur:
                row = dict(await cur.fetchone())
        return row
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@verification_router.post("/verify/outcome")
async def submit_outcome(req: OutcomeRequest):
    """Submit actual job cost after closeout; updates calibration data."""
    try:
        import asyncio
        profile = await asyncio.to_thread(
            record_actual_outcome,
            client_id=req.client_id,
            tournament_id=req.tournament_id,
            actual_cost=req.actual_cost,
            won=req.won,
            win_probability=req.win_probability,
        )
        return {
            "client_id": req.client_id,
            "tournament_id": req.tournament_id,
            "calibration": profile.get("calibration", {}),
        }
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@verification_router.get("/verify/accuracy/{client_id}")
async def get_accuracy(client_id: str):
    """Return agent accuracy and win probability calibration report for a client."""
    try:
        import asyncio
        report = await asyncio.to_thread(get_agent_accuracy_report, client_id)
        return report
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_verification_api.py -v
```

Expected: All PASSED

- [ ] **Step 5: Commit**

```bash
git add backend/api/verification.py tests/test_verification_api.py
git commit -m "feat: add verification API router with audit, queue, outcome, and accuracy endpoints"
```

---

## Task 9: Build `scheduler.py` — Nightly Batch

**Files:**
- Create: `backend/scheduler.py`

- [ ] **Step 1: Create `backend/scheduler.py`**

```python
"""
Scheduler — TakeoffAI
Runs nightly batch price verification against all rows in material_costs.csv.
Uses APScheduler with AsyncIOScheduler; started/stopped by FastAPI lifespan.
"""

import csv
import logging
from datetime import datetime
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from backend.agents.price_verifier import verify_line_items

logger = logging.getLogger(__name__)

CSV_PATH = Path(__file__).parent / "data" / "material_costs.csv"

_scheduler: AsyncIOScheduler = AsyncIOScheduler()


async def _run_nightly_verification() -> None:
    """
    Nightly job: verify every row in material_costs.csv against web sources.
    Logs a summary of updated, flagged, and failed items.
    """
    if not CSV_PATH.exists():
        logger.warning("material_costs.csv not found; skipping nightly verification")
        return

    rows = list(csv.DictReader(CSV_PATH.open(newline="", encoding="utf-8")))
    if not rows:
        logger.info("material_costs.csv is empty; nothing to verify")
        return

    # Build synthetic line items from CSV rows
    line_items = [
        {
            "description": row["item"],
            "unit": row["unit"],
            "unit_material_cost": float(row.get("low_cost", 0)),
        }
        for row in rows
        if row.get("item") and row.get("unit")
    ]

    logger.info("Starting nightly verification for %d items", len(line_items))
    start = datetime.utcnow()

    try:
        records = await verify_line_items(line_items, triggered_by="nightly")
    except Exception as exc:
        logger.error("Nightly verification failed: %s", exc)
        return

    elapsed = (datetime.utcnow() - start).total_seconds()
    updated = sum(1 for r in records if r.get("auto_updated"))
    flagged = sum(1 for r in records if r.get("flagged"))
    no_source = sum(1 for r in records if r.get("source_count", 0) == 0)

    logger.info(
        "Nightly verification complete in %.1fs: %d items | "
        "%d auto-updated | %d flagged | %d no-source",
        elapsed, len(records), updated, flagged, no_source,
    )


def start_scheduler() -> None:
    """Register nightly job and start the scheduler. Called from FastAPI lifespan."""
    _scheduler.add_job(
        _run_nightly_verification,
        CronTrigger(hour=2, minute=0),
        id="nightly_price_verification",
        replace_existing=True,
        misfire_grace_time=3600,  # run up to 1hr late if server was down
    )
    _scheduler.start()
    logger.info("APScheduler started; nightly verification scheduled at 02:00")


def stop_scheduler() -> None:
    """Stop the scheduler. Called from FastAPI lifespan shutdown."""
    if _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("APScheduler stopped")
```

- [ ] **Step 2: Verify import works**

```bash
uv run python -c "from backend.scheduler import start_scheduler, stop_scheduler; print('ok')"
```

Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add backend/scheduler.py
git commit -m "feat: add nightly APScheduler batch for price verification at 02:00"
```

---

## Task 10: Wire Everything into `main.py`

**Files:**
- Modify: `backend/api/main.py`

- [ ] **Step 1: Add imports and router registration to `main.py`**

At the top of `backend/api/main.py`, add after existing imports:

```python
from backend.api.verification import verification_router
from backend.scheduler import start_scheduler, stop_scheduler
```

Update the `lifespan` context manager to start/stop the scheduler:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure data directory exists
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(_CREATE_TABLES)
        await db.commit()
    start_scheduler()
    yield
    stop_scheduler()
```

Add the verification router after the existing `app.include_router` calls:

```python
app.include_router(verification_router, prefix="/api")
```

- [ ] **Step 2: Start the server and confirm it boots cleanly**

```bash
cd "/Users/bevo/Library/CloudStorage/GoogleDrive-kroberts2007@gmail.com/My Drive/TakeoffAI"
uv run uvicorn backend.api.main:app --port 8000 --reload &
sleep 3
curl -s http://localhost:8000/api/health | python3 -m json.tool
```

Expected: `{"status": "ok", ...}` (or `"degraded"` if no API key, but no crash)

```bash
curl -s http://localhost:8000/api/verify/audit | python3 -m json.tool
```

Expected: `[]`

```bash
curl -s http://localhost:8000/api/verify/queue | python3 -m json.tool
```

Expected: `[]`

Kill the dev server:

```bash
kill %1
```

- [ ] **Step 3: Run full test suite**

```bash
uv run pytest tests/ -v --tb=short
```

Expected: All tests PASSED (skip any that require live network).

- [ ] **Step 4: Commit**

```bash
git add backend/api/main.py
git commit -m "feat: wire verification router and APScheduler into FastAPI lifespan"
```

---

## Task 11: End-to-End Smoke Test

**Files:**
- No new files — manual verification

- [ ] **Step 1: Start the full server**

```bash
cd "/Users/bevo/Library/CloudStorage/GoogleDrive-kroberts2007@gmail.com/My Drive/TakeoffAI"
uv run uvicorn backend.api.main:app --port 8000 &
sleep 3
```

- [ ] **Step 2: Run an estimate**

```bash
curl -s -X POST http://localhost:8000/api/estimate \
  -H "Content-Type: application/json" \
  -d '{"description":"40x60 metal building in Brownwood TX","zip_code":"76801","trade_type":"general"}' \
  | python3 -m json.tool | head -30
```

Expected: JSON with `line_items`, `total_bid`, `confidence`.

- [ ] **Step 3: On-demand verify those line items**

Take the `line_items` array from the response above and post it to the verify endpoint:

```bash
curl -s -X POST http://localhost:8000/api/verify/estimate \
  -H "Content-Type: application/json" \
  -d '{"line_items": [{"description":"Framing Lumber (2x4x8)","unit":"LF","unit_material_cost":0.60}]}' \
  | python3 -m json.tool
```

Expected: Array with one audit record. Check `source_count`, `deviation_pct`, `flagged`.

- [ ] **Step 4: Check the audit table**

```bash
curl -s "http://localhost:8000/api/verify/audit?limit=5" | python3 -m json.tool
```

Expected: The audit record from step 3 appears.

- [ ] **Step 5: Check the review queue (if flagged)**

```bash
curl -s "http://localhost:8000/api/verify/queue" | python3 -m json.tool
```

Expected: If deviation >5%, at least one pending item appears.

- [ ] **Step 6: Kill server and commit final**

```bash
kill %1
git add -A
git commit -m "chore: end-to-end smoke test confirmed; price verification and calibration system complete"
```

---

## Self-Review Checklist

| Spec Requirement | Covered In |
|---|---|
| Log every LLM-generated unit price | Task 5 — `_write_audit_record()` writes every price to `price_audit` |
| Human audit and override | Task 8 — `GET /api/verify/audit`, `PATCH /api/verify/queue/{id}` |
| Supplier sites first, search fallback | Task 3 + 4 — Phase 1 (HD/Lowe's) + Phase 2 (DuckDuckGo) |
| Auto-update CSV when 3+ sources agree | Task 4 + 5 — `_update_seed_csv()` + `verify_line_items()` |
| Queue for human review (1-2 or disagreeing sources) | Task 5 — `_write_audit_record()` inserts to `review_queue` when flagged |
| Background trigger post-judge | Task 7 — `asyncio.create_task(verify_line_items(...))` in `judge.py` |
| On-demand trigger | Task 8 — `POST /api/verify/estimate` |
| Nightly batch at 2am | Task 9 — `scheduler.py` with `CronTrigger(hour=2, minute=0)` |
| Deviation flag at >5% | Task 4 — `DEVIATION_THRESHOLD_PCT = 5.0` |
| Feed actual costs back after job | Task 6 + 8 — `record_actual_outcome()` + `POST /api/verify/outcome` |
| Brier score for win probability | Task 6 — `_compute_brier_score()` |
| Red-flag agents at >5% avg deviation over 5 jobs | Task 6 — `RED_FLAG_DEVIATION_THRESHOLD = 5.0`, `RED_FLAG_LOOKBACK = 5` |
| Accuracy report endpoint | Task 8 — `GET /api/verify/accuracy/{client_id}` |
| Schema `price_audit` table | Task 2 |
| Schema `review_queue` table | Task 2 |
| Client profile `calibration` block | Task 6 |
