"""
Scheduler — TakeoffAI
Runs nightly batch price verification against all rows in material_costs.csv.
Uses APScheduler with AsyncIOScheduler; started/stopped by FastAPI lifespan.
"""

import csv
import logging
from datetime import datetime, timezone
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from backend.agents.price_verifier import verify_line_items

logger = logging.getLogger(__name__)

CSV_PATH = Path(__file__).parent / "data" / "material_costs.csv"

_scheduler: AsyncIOScheduler = AsyncIOScheduler()


async def run_verification_batch(triggered_by: str = "on_demand") -> dict:
    """
    Verify all rows in material_costs.csv against web sources.
    Returns a summary dict. Called by both the nightly scheduler and the
    on-demand API endpoint POST /api/verify/run.
    """
    if not CSV_PATH.exists():
        return {
            "status": "skipped",
            "reason": "material_costs.csv not found",
            "items_checked": 0,
            "flagged": 0,
            "auto_updated": 0,
            "duration_seconds": 0.0,
            "triggered_at": datetime.now(timezone.utc).isoformat(),
        }

    with CSV_PATH.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    line_items = [
        {
            "description": row["item"],
            "unit": row["unit"],
            "unit_material_cost": float(row.get("low_cost", 0)),
        }
        for row in rows
        if row.get("item") and row.get("unit")
    ]

    triggered_at = datetime.now(timezone.utc)
    records = await verify_line_items(line_items, triggered_by=triggered_by)
    elapsed = (datetime.now(timezone.utc) - triggered_at).total_seconds()

    # Log flagged items to Google Sheet (no-op if GWS_ENABLED is False)
    try:
        from backend.agents._workspace import log_price_audit_to_sheet

        await log_price_audit_to_sheet(records)
    except Exception:
        logger.exception("log_price_audit_to_sheet failed (non-fatal)")

    return {
        "status": "complete",
        "items_checked": len(records),
        "flagged": sum(1 for r in records if r.get("flagged")),
        "auto_updated": sum(1 for r in records if r.get("auto_updated")),
        "duration_seconds": round(elapsed, 1),
        "triggered_at": triggered_at.isoformat(),
    }


async def _run_nightly_verification() -> None:
    """
    Nightly job: verify every row in material_costs.csv against web sources.
    Delegates to run_verification_batch() and logs the summary.
    """
    try:
        result = await run_verification_batch(triggered_by="nightly")
        logger.info(
            "Nightly verification: %s items checked | %s flagged | %s auto-updated | %.1fs",
            result["items_checked"],
            result["flagged"],
            result["auto_updated"],
            result["duration_seconds"],
        )
    except Exception as exc:
        logger.error("Nightly verification failed: %s", exc, exc_info=True)


def start_scheduler() -> None:
    """Register nightly job and start the scheduler. Called from FastAPI lifespan."""
    _scheduler.add_job(
        _run_nightly_verification,
        CronTrigger(hour=2, minute=0),
        id="nightly_price_verification",
        replace_existing=True,
        misfire_grace_time=3600,  # run up to 1hr late if server was down
    )
    _scheduler.start()
    logger.info("APScheduler started; nightly verification scheduled at 02:00")


def stop_scheduler() -> None:
    """Stop the scheduler. Called from FastAPI lifespan shutdown."""
    if _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("APScheduler stopped")
