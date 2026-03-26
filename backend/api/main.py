from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.routes import router

app = FastAPI(
    title="TakeoffAI",
    description="AI-powered construction pre-bid estimation and bid-winning strategy — by answerd.it",
    version="0.1.0",
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
