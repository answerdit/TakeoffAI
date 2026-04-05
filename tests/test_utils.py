"""Tests for backend/agents/utils.py"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock

from backend.agents.utils import parse_llm_json, call_with_json_retry


# ── parse_llm_json ────────────────────────────────────────────────────────────

def test_parse_llm_json_plain():
    raw = '{"total_bid": 12345.0}'
    assert parse_llm_json(raw) == {"total_bid": 12345.0}


def test_parse_llm_json_strips_json_fence():
    raw = '```json\n{"total_bid": 12345.0}\n```'
    assert parse_llm_json(raw) == {"total_bid": 12345.0}


def test_parse_llm_json_strips_plain_fence():
    raw = '```\n{"total_bid": 12345.0}\n```'
    assert parse_llm_json(raw) == {"total_bid": 12345.0}


def test_parse_llm_json_raises_on_bad_json():
    with pytest.raises(json.JSONDecodeError):
        parse_llm_json("not json at all")


# ── call_with_json_retry ──────────────────────────────────────────────────────

def _make_client(responses: list[str]) -> MagicMock:
    """Build a mock AsyncAnthropic client that returns each string in sequence."""
    client = MagicMock()
    client.messages.create = AsyncMock(side_effect=[
        MagicMock(content=[MagicMock(text=r)]) for r in responses
    ])
    return client


@pytest.mark.anyio
async def test_call_with_json_retry_succeeds_first_try():
    payload = '{"total_bid": 5000.0}'
    client = _make_client([payload])
    result = await call_with_json_retry(
        client,
        model="claude-sonnet-4-6",
        max_tokens=100,
        system="sys",
        messages=[{"role": "user", "content": "go"}],
        max_retries=0,
    )
    assert result == {"total_bid": 5000.0}
    assert client.messages.create.call_count == 1


@pytest.mark.anyio
async def test_call_with_json_retry_retries_on_bad_json():
    bad = "here is some prose, not JSON"
    good = '{"total_bid": 9999.0}'
    client = _make_client([bad, good])
    result = await call_with_json_retry(
        client,
        model="claude-sonnet-4-6",
        max_tokens=100,
        system="sys",
        messages=[{"role": "user", "content": "go"}],
        max_retries=1,
    )
    assert result == {"total_bid": 9999.0}
    assert client.messages.create.call_count == 2


@pytest.mark.anyio
async def test_call_with_json_retry_raises_after_all_attempts():
    client = _make_client(["bad", "also bad", "still bad"])
    with pytest.raises(ValueError, match="Failed to parse JSON"):
        await call_with_json_retry(
            client,
            model="claude-sonnet-4-6",
            max_tokens=100,
            system="sys",
            messages=[{"role": "user", "content": "go"}],
            max_retries=2,
        )
    assert client.messages.create.call_count == 3


@pytest.mark.anyio
async def test_call_with_json_retry_passes_temperature():
    """temperature kwarg must reach client.messages.create."""
    fake_response = MagicMock()
    fake_response.content = [MagicMock(text='{"result": 1}')]

    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(return_value=fake_response)

    await call_with_json_retry(
        mock_client,
        model="claude-sonnet-4-6",
        max_tokens=100,
        system="sys",
        messages=[{"role": "user", "content": "hi"}],
        temperature=0.3,
    )

    call_kwargs = mock_client.messages.create.call_args.kwargs
    assert call_kwargs["temperature"] == 0.3


@pytest.mark.anyio
async def test_call_with_json_retry_default_temperature():
    """Default temperature must be 0.7."""
    fake_response = MagicMock()
    fake_response.content = [MagicMock(text='{"result": 1}')]

    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(return_value=fake_response)

    await call_with_json_retry(
        mock_client,
        model="claude-sonnet-4-6",
        max_tokens=100,
        system="sys",
        messages=[{"role": "user", "content": "hi"}],
    )

    call_kwargs = mock_client.messages.create.call_args.kwargs
    assert call_kwargs["temperature"] == 0.7
