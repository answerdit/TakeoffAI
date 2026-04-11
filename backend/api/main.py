import os
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from backend.agents._db import _configure_conn
from backend.api.routes import limiter, router, verify_api_key
from backend.api.upload import upload_router
from backend.api.verification import verification_router
from backend.api.wiki_routes import wiki_router
from backend.config import settings
from backend.scheduler import start_scheduler, stop_scheduler

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
    temperature     REAL DEFAULT 0.7,
    is_consensus    INTEGER DEFAULT 0,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_entries_tournament_id ON tournament_entries(tournament_id);
CREATE INDEX IF NOT EXISTS idx_tournaments_client_id ON bid_tournaments(client_id);
CREATE INDEX IF NOT EXISTS idx_tournaments_status ON bid_tournaments(status);

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
            await db.execute(f"PRAGMA user_version = {int(version)}")
            await db.commit()
        except Exception as e:
            # Column may already exist (e.g., from manual schema work)
            if "duplicate column" in str(e).lower():
                await db.execute(f"PRAGMA user_version = {int(version)}")
                await db.commit()
            else:
                raise


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure data directory exists
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(_CREATE_TABLES)
        await _configure_conn(db)
        await db.commit()
        await _run_migrations(db)
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(
    title="TakeoffAI",
    description="AI-powered construction pre-bid estimation and bid-winning strategy — by answerd.it",
    version="0.1.0",
    lifespan=lifespan,
)

# ── Rate limiting (slowapi) ───────────────────────────────────────────────────
# limiter is defined in routes.py (with default_limits=["60/minute"]) and
# wired into app.state so SlowAPIMiddleware can enforce global + per-endpoint limits.
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

_app_env = os.getenv("APP_ENV", "development")
_raw_origins = os.getenv("ALLOWED_ORIGINS", "")

if _app_env != "development" and not _raw_origins:
    raise RuntimeError("ALLOWED_ORIGINS env var is required in non-development mode")

ALLOWED_ORIGINS = (
    _raw_origins.split(",") if _raw_origins
    # "null" is the Origin header browsers send for file:// pages; required for
    # the dev workflow where index.html is opened directly without a local server.
    else ["http://localhost:3000", "http://localhost:5173", "null"]
)

if "*" in ALLOWED_ORIGINS:
    raise RuntimeError("Wildcard ALLOWED_ORIGINS is not permitted (incompatible with credentials)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["POST", "GET", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-API-Key"],
)

app.include_router(router, prefix="/api")
app.include_router(upload_router, prefix="/api", dependencies=[Depends(verify_api_key)])
app.include_router(verification_router, prefix="/api", dependencies=[Depends(verify_api_key)])
app.include_router(wiki_router, prefix="/api", dependencies=[Depends(verify_api_key)])


@app.get("/api/health")
async def health():
    from backend.config import settings
    if not settings.anthropic_api_key or settings.anthropic_api_key == "sk-ant-your-key-here":
        return {"status": "degraded", "product": "TakeoffAI", "company": "answerd.it"}
    return {"status": "ok", "product": "TakeoffAI", "company": "answerd.it"}
