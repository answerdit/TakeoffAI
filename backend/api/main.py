from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.routes import router

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
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure data directory exists
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(_CREATE_TABLES)
        await db.commit()
    yield


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
    allow_methods=["POST", "GET", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)

app.include_router(router, prefix="/api")


@app.get("/api/health")
async def health():
    from backend.config import settings
    if not settings.anthropic_api_key or settings.anthropic_api_key == "sk-ant-your-key-here":
        return {"status": "degraded", "reason": "ANTHROPIC_API_KEY not configured", "product": "TakeoffAI", "company": "answerd.it"}
    return {"status": "ok", "product": "TakeoffAI", "company": "answerd.it"}
