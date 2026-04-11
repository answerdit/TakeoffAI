"""
Shared SQLite connection helper — TakeoffAI
Applies connection-scoped PRAGMAs on every new aiosqlite connection.
"""

import aiosqlite


async def _configure_conn(db: aiosqlite.Connection) -> None:
    """
    Apply connection-scoped PRAGMAs that SQLite resets on every new connection.
    Must be called immediately after aiosqlite.connect() in every call site.

    - foreign_keys: enforce referential integrity (off by default in SQLite)
    - journal_mode WAL: allows concurrent readers + one writer without blocking
    - synchronous NORMAL: safe durability tradeoff with WAL (fsync on checkpoint only)
    """
    await db.execute("PRAGMA foreign_keys = ON")
    await db.execute("PRAGMA journal_mode = WAL")
    await db.execute("PRAGMA synchronous = NORMAL")
