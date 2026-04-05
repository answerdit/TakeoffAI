import os
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.routes import router
from backend.api.upload import upload_router
from backend.config import settings

DB_PATH = settings.db_path

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

CREATE INDEX IF NOT EXISTS idx_entries_tournament_id ON tournament_entries(tournament_id);
CREATE INDEX IF NOT EXISTS idx_tournaments_client_id ON bid_tournaments(client_id);
CREATE INDEX IF NOT EXISTS idx_tournaments_status ON bid_tournaments(status);
"""


_MIGRATIONS = [
    # migration 1: add agent output columns to tournament_entries
    """ALTER TABLE tournament_entries ADD COLUMN margin_pct REAL""",
    """ALTER TABLE tournament_entries ADD COLUMN confidence TEXT""",
    """ALTER TABLE tournament_entries ADD COLUMN trade_type TEXT""",
    """ALTER TABLE tournament_entries ADD COLUMN overhead_pct REAL""",
    # migration 2: add judge metadata to bid_tournaments
    """ALTER TABLE bid_tournaments ADD COLUMN judged_at DATETIME""",
    """ALTER TABLE bid_tournaments ADD COLUMN judge_mode TEXT""",
    """ALTER TABLE bid_tournaments ADD COLUMN actual_winning_bid REAL""",
    """ALTER TABLE bid_tournaments ADD COLUMN human_notes TEXT""",
    # migration 3: add rfp_text to bid_tournaments
    """ALTER TABLE bid_tournaments ADD COLUMN rfp_text TEXT""",
    # migration 4: temperature ensemble — add temperature and is_consensus columns
    """ALTER TABLE tournament_entries ADD COLUMN temperature REAL DEFAULT 0.7""",
    """ALTER TABLE tournament_entries ADD COLUMN is_consensus INTEGER DEFAULT 0""",
]


async def _run_migrations(db: aiosqlite.Connection) -> None:
    """Run pending schema migrations. Uses PRAGMA user_version as schema version counter."""
    async with db.execute("PRAGMA user_version") as cur:
        row = await cur.fetchone()
    current_version = row[0] if row else 0

    for i, migration_sql in enumerate(_MIGRATIONS):
        version = i + 1
        if current_version >= version:
            continue
        try:
            await db.execute(migration_sql)
            await db.execute(f"PRAGMA user_version = {version}")
            await db.commit()
        except Exception as e:
            # Column may already exist (e.g., from manual schema work)
            if "duplicate column" in str(e).lower():
                await db.execute(f"PRAGMA user_version = {version}")
                await db.commit()
            else:
                raise


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure data directory exists
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(_CREATE_TABLES)
        await db.execute("PRAGMA foreign_keys = ON")
        await db.execute("PRAGMA journal_mode = WAL")
        await db.commit()
        await _run_migrations(db)
    yield


app = FastAPI(
    title="TakeoffAI",
    description="AI-powered construction pre-bid estimation and bid-winning strategy — by answerd.it",
    version="0.1.0",
    lifespan=lifespan,
)

ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000,http://localhost:5173").split(",")

if "*" in ALLOWED_ORIGINS and os.getenv("APP_ENV", "development") != "development":
    raise RuntimeError("Wildcard ALLOWED_ORIGINS cannot be used with allow_credentials=True in non-dev environments")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["POST", "GET", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)

app.include_router(router, prefix="/api")
app.include_router(upload_router, prefix="/api")


@app.get("/api/health")
async def health():
    from backend.config import settings
    if not settings.anthropic_api_key or settings.anthropic_api_key == "sk-ant-your-key-here":
        return {"status": "degraded", "reason": "ANTHROPIC_API_KEY not configured", "product": "TakeoffAI", "company": "answerd.it"}
    return {"status": "ok", "product": "TakeoffAI", "company": "answerd.it"}
