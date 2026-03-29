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
