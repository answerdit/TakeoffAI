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
