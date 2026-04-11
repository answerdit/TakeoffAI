"""Shared pytest fixtures for TakeoffAI tests."""

import os

import pytest
from httpx import ASGITransport, AsyncClient

from backend.api.main import app

_TEST_API_KEY = "test-key"


@pytest.fixture
async def client(monkeypatch):
    """AsyncClient with full app lifespan (creates DB tables). Injects test API key."""
    monkeypatch.setenv("API_KEY", _TEST_API_KEY)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-API-Key": _TEST_API_KEY},
    ) as c:
        async with app.router.lifespan_context(app):
            yield c
