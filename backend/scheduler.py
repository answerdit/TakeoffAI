"""
Scheduler — TakeoffAI
Runs nightly batch price verification against all rows in material_costs.csv.
Uses APScheduler with AsyncIOScheduler; started/stopped by FastAPI lifespan.
"""

import csv
import logging
from datetime import datetime
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from backend.agents.price_verifier import verify_line_items

logger = logging.getLogger(__name__)

CSV_PATH = Path(__file__).parent / "data" / "material_costs.csv"

_scheduler: AsyncIOScheduler = AsyncIOScheduler()


async def _run_nightly_verification() -> None:
    """
    Nightly job: verify every row in material_costs.csv against web sources.
    Logs a summary of updated, flagged, and failed items.
    """
    if not CSV_PATH.exists():
        logger.warning("material_costs.csv not found; skipping nightly verification")
        return

    rows = list(csv.DictReader(CSV_PATH.open(newline="", encoding="utf-8")))
    if not rows:
        logger.info("material_costs.csv is empty; nothing to verify")
        return

    # Build synthetic line items from CSV rows
    line_items = [
        {
            "description": row["item"],
            "unit": row["unit"],
            "unit_material_cost": float(row.get("low_cost", 0)),
        }
        for row in rows
        if row.get("item") and row.get("unit")
    ]

    logger.info("Starting nightly verification for %d items", len(line_items))
    start = datetime.utcnow()

    try:
        records = await verify_line_items(line_items, triggered_by="nightly")
    except Exception as exc:
        logger.error("Nightly verification failed: %s", exc)
        return

    elapsed = (datetime.utcnow() - start).total_seconds()
    updated = sum(1 for r in records if r.get("auto_updated"))
    flagged = sum(1 for r in records if r.get("flagged"))
    no_source = sum(1 for r in records if r.get("source_count", 0) == 0)

    logger.info(
        "Nightly verification complete in %.1fs: %d items | "
        "%d auto-updated | %d flagged | %d no-source",
        elapsed, len(records), updated, flagged, no_source,
    )


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
