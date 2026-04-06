"""Shared pytest fixtures for TakeoffAI tests."""

import pytest
from httpx import AsyncClient, ASGITransport

from backend.api.main import app


@pytest.fixture
async def client():
    """AsyncClient with full app lifespan (creates DB tables)."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        async with app.router.lifespan_context(app):
            yield c
