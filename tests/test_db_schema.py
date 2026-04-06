import tempfile
from pathlib import Path
import aiosqlite
import pytest


@pytest.mark.asyncio
async def test_price_audit_table_exists(tmp_path):
    """price_audit table should be created by the DDL in main.py."""
    db_path = str(tmp_path / "test.db")

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
async def test_review_queue_table_exists(tmp_path):
    db_path = str(tmp_path / "test.db")
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
async def test_price_audit_columns(tmp_path):
    db_path = str(tmp_path / "test.db")
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


@pytest.mark.asyncio
async def test_review_queue_columns(tmp_path):
    db_path = str(tmp_path / "test.db")
    from backend.api.main import _CREATE_TABLES
    async with aiosqlite.connect(db_path) as db:
        await db.executescript(_CREATE_TABLES)
        await db.commit()
        async with db.execute("PRAGMA table_info(review_queue)") as cur:
            cols = {row[1] for row in await cur.fetchall()}
    expected = {
        "id", "audit_id", "line_item", "unit", "ai_unit_cost", "verified_mid",
        "deviation_pct", "sources", "status", "reviewer_notes", "resolved_at", "created_at"
    }
    assert expected.issubset(cols), f"Missing columns: {expected - cols}"
