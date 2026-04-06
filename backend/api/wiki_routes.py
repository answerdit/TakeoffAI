"""
TakeoffAI — Wiki & Job Tracking route definitions.
Job pipeline CRUD and wiki lint endpoint.
"""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from slowapi import Limiter
from slowapi.util import get_remote_address

from backend.agents import wiki_manager

logger = logging.getLogger(__name__)

limiter = Limiter(key_func=get_remote_address, default_limits=["60/minute"])

wiki_router = APIRouter()


# ── Request models ───────────────────────────────────────────────────────────

class JobCreateRequest(BaseModel):
    client_id: str = Field(..., min_length=1, description="Client identifier")
    project_name: str = Field(..., min_length=3, description="Human-readable project name")
    description: str = Field(..., min_length=10, description="Project description")
    zip_code: str = Field(..., min_length=5, max_length=10, description="Project ZIP code")
    trade_type: str = Field(default="general", description="Primary trade type")


class JobUpdateRequest(BaseModel):
    job_slug: str = Field(..., min_length=1, description="Job slug from create response")
    status: str = Field(..., description="New status: bid-submitted, won, lost, or closed")
    our_bid: Optional[float] = Field(default=None, ge=0, description="Bid amount (required for bid-submitted)")
    actual_cost: Optional[float] = Field(default=None, ge=0, description="Actual cost (required for closed)")
    notes: Optional[str] = Field(default="", description="Optional notes")


# ── Endpoints ────────────────────────────────────────────────────────────────

@wiki_router.post("/job/create")
@limiter.limit("10/minute")
async def job_create(request: Request, req: JobCreateRequest):
    """Create a new job at prospect status."""
    try:
        result = await wiki_manager.create_job(
            client_id=req.client_id,
            project_name=req.project_name,
            description=req.description,
            zip_code=req.zip_code,
            trade_type=req.trade_type,
        )
        return result
    except Exception as exc:
        logger.exception("job_create failed")
        raise HTTPException(status_code=500, detail="Internal server error") from exc


@wiki_router.post("/job/update")
@limiter.limit("10/minute")
async def job_update(request: Request, req: JobUpdateRequest):
    """Advance a job's status. Triggers wiki cascade for won/lost/closed."""
    valid_update_statuses = {"bid-submitted", "won", "lost", "closed"}
    if req.status not in valid_update_statuses:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status '{req.status}'. Must be one of: {', '.join(sorted(valid_update_statuses))}",
        )

    if req.status == "bid-submitted" and req.our_bid is None:
        raise HTTPException(status_code=400, detail="our_bid is required for bid-submitted status")

    if req.status == "closed" and req.actual_cost is None:
        raise HTTPException(status_code=400, detail="actual_cost is required for closed status")

    page_path = wiki_manager.JOBS_DIR / f"{req.job_slug}.md"
    if not page_path.exists():
        raise HTTPException(status_code=404, detail=f"Job '{req.job_slug}' not found")

    try:
        if req.status == "bid-submitted":
            await wiki_manager.record_bid_decision(
                job_slug=req.job_slug,
                our_bid=req.our_bid,
                notes=req.notes or "",
            )
        elif req.status in ("won", "lost", "closed"):
            await wiki_manager.cascade_outcome(
                job_slug=req.job_slug,
                status=req.status,
                actual_cost=req.actual_cost,
                notes=req.notes or "",
            )

        meta, _ = wiki_manager._read_page(page_path)
        return meta
    except Exception as exc:
        logger.exception("job_update failed")
        raise HTTPException(status_code=500, detail="Internal server error") from exc


@wiki_router.get("/job/{slug}")
async def job_get(slug: str):
    """Return a job's frontmatter as JSON."""
    page_path = wiki_manager.JOBS_DIR / f"{slug}.md"
    if not page_path.exists():
        raise HTTPException(status_code=404, detail=f"Job '{slug}' not found")

    meta, _ = wiki_manager._read_page(page_path)
    meta["job_slug"] = slug
    return meta


@wiki_router.get("/jobs")
async def jobs_list(status: Optional[str] = None):
    """List all jobs. Optional status filter: 'active' excludes closed and lost."""
    if not wiki_manager.JOBS_DIR.exists():
        return []

    results = []
    for path in wiki_manager.JOBS_DIR.glob("*.md"):
        meta, _ = wiki_manager._read_page(path)
        meta["job_slug"] = path.stem

        if status == "active" and meta.get("status") in ("closed", "lost"):
            continue
        elif status and status != "active" and meta.get("status") != status:
            continue

        results.append(meta)

    return results


@wiki_router.get("/wiki/lint")
async def wiki_lint():
    """Run wiki health check. Returns structured report."""
    try:
        report = wiki_manager.lint()
        return report
    except Exception as exc:
        logger.exception("wiki_lint failed")
        raise HTTPException(status_code=500, detail="Internal server error") from exc
