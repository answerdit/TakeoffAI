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
    import csv
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
    """Phase 1 gets 1 supplier hit; Phase 2 fires (< 2 trigger) and adds web search;
    total 3 agreeing sources → auto_updated=1 and CSV is rewritten."""
    import aiosqlite
    import csv

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
                       AsyncMock(side_effect=[1.00, None])):
                with patch("backend.agents.price_verifier._web_search_price",
                           AsyncMock(return_value=[0.98, 1.02])):
                    from backend.agents.price_verifier import verify_line_items
                    records = await verify_line_items(line_items, triggered_by="nightly")

    assert records[0]["auto_updated"] == 1
    rows = list(csv.DictReader(csv_path.open()))
    lumber = next(r for r in rows if r["item"] == "Framing Lumber (2x4x8)")
    # low_cost = min of verified prices, high_cost = max
    assert float(lumber["low_cost"]) == pytest.approx(0.98)
    assert float(lumber["high_cost"]) == pytest.approx(1.02)
