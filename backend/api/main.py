from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.routes import router
from backend.api.upload import upload_router
from backend.api.verification import verification_router
from backend.scheduler import start_scheduler, stop_scheduler

DB_PATH = str(Path(__file__).parent.parent / "data" / "takeoffai.db")

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


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure data directory exists
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(_CREATE_TABLES)
        await db.commit()
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(
    title="TakeoffAI",
    description="AI-powered construction pre-bid estimation and bid-winning strategy — by answerd.it",
    version="0.1.0",
    lifespan=lifespan,
)

import os

ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000,http://localhost:5173").split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["POST", "GET", "PATCH", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)

app.include_router(router, prefix="/api")
app.include_router(upload_router, prefix="/api")
app.include_router(verification_router, prefix="/api")


@app.get("/api/health")
async def health():
    from backend.config import settings
    if not settings.anthropic_api_key or settings.anthropic_api_key == "sk-ant-your-key-here":
        return {"status": "degraded", "reason": "ANTHROPIC_API_KEY not configured", "product": "TakeoffAI", "company": "answerd.it"}
    return {"status": "ok", "product": "TakeoffAI", "company": "answerd.it"}
