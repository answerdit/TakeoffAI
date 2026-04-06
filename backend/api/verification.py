"""
Verification API — TakeoffAI
Endpoints for price audit, review queue, on-demand verification, and calibration.
"""

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

import aiosqlite
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.agents.feedback_loop import get_agent_accuracy_report, record_actual_outcome
from backend.agents.price_verifier import verify_line_items, _update_seed_csv
from backend.scheduler import run_verification_batch

DB_PATH = str(Path(__file__).parent.parent / "data" / "takeoffai.db")

verification_router = APIRouter()


def _validate_client_id(client_id: str) -> None:
    """Reject client_id values that could enable path traversal."""
    if not re.match(r'^[a-zA-Z0-9_\-]+$', client_id):
        raise HTTPException(status_code=400, detail="Invalid client_id format")


# ── Pydantic models ──────────────────────────────────────────────────────────

class VerifyEstimateRequest(BaseModel):
    line_items: list[dict]
    tournament_id: Optional[int] = None


class OutcomeRequest(BaseModel):
    client_id: str
    tournament_id: int
    actual_cost: float
    won: bool
    win_probability: Optional[float] = None


class QueueResolveRequest(BaseModel):
    status: Literal["approved", "rejected"]
    reviewer_notes: Optional[str] = None
    custom_price: Optional[float] = None


# ── Endpoints ────────────────────────────────────────────────────────────────

@verification_router.post("/verify/estimate")
async def verify_estimate(req: VerifyEstimateRequest):
    """On-demand: verify line items from any estimate against web sources."""
    try:
        records = await verify_line_items(
            line_items=req.line_items,
            triggered_by="on_demand",
            tournament_id=req.tournament_id,
        )
        return records
    except Exception as exc:
        logging.exception("verify_estimate failed")
        raise HTTPException(status_code=500, detail="Internal server error") from exc


@verification_router.get("/verify/audit")
async def list_audit(
    flagged: Optional[int] = None,
    triggered_by: Optional[str] = None,
    line_item: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: int = 100,
):
    """List price audit records with optional filters."""
    try:
        clauses = []
        params = []
        if flagged is not None:
            clauses.append("flagged = ?")
            params.append(flagged)
        if triggered_by:
            clauses.append("triggered_by = ?")
            params.append(triggered_by)
        if line_item:
            clauses.append("line_item LIKE ?")
            params.append(f"%{line_item}%")
        if date_from:
            clauses.append("created_at >= ?")
            params.append(date_from)
        if date_to:
            clauses.append("created_at <= ?")
            params.append(date_to)

        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)

        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                f"SELECT * FROM price_audit {where} ORDER BY created_at DESC LIMIT ?",
                params,
            ) as cur:
                rows = [dict(r) for r in await cur.fetchall()]
        return rows
    except Exception as exc:
        logging.exception("list_audit failed")
        raise HTTPException(status_code=500, detail="Internal server error") from exc


@verification_router.get("/verify/queue")
async def list_queue(status: Optional[str] = "pending", limit: int = 100):
    """List review queue items."""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            if status:
                async with db.execute(
                    "SELECT * FROM review_queue WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                    (status, limit),
                ) as cur:
                    rows = [dict(r) for r in await cur.fetchall()]
            else:
                async with db.execute(
                    "SELECT * FROM review_queue ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ) as cur:
                    rows = [dict(r) for r in await cur.fetchall()]
        return rows
    except Exception as exc:
        logging.exception("list_queue failed")
        raise HTTPException(status_code=500, detail="Internal server error") from exc


@verification_router.patch("/verify/queue/{queue_id}")
async def resolve_queue_item(queue_id: int, req: QueueResolveRequest):
    """Approve or reject a flagged price deviation. Approval updates material_costs.csv."""
    try:
        resolved_at = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row

            # Load queue item joined with audit for verified prices
            async with db.execute(
                """
                SELECT rq.*, pa.verified_low, pa.verified_high
                FROM review_queue rq
                LEFT JOIN price_audit pa ON rq.audit_id = pa.id
                WHERE rq.id = ?
                """,
                (queue_id,),
            ) as cur:
                full_row = await cur.fetchone()

            if not full_row:
                raise HTTPException(status_code=404, detail=f"Queue item {queue_id} not found")

            full_row = dict(full_row)

            await db.execute(
                "UPDATE review_queue SET status = ?, reviewer_notes = ?, resolved_at = ? WHERE id = ?",
                (req.status, req.reviewer_notes, resolved_at, queue_id),
            )
            await db.commit()

            async with db.execute(
                "SELECT * FROM review_queue WHERE id = ?", (queue_id,)
            ) as cur:
                row = dict(await cur.fetchone())

        # Update seed CSV if approved
        if req.status == "approved":
            if req.custom_price is not None:
                new_low = round(req.custom_price * 0.95, 4)
                new_high = round(req.custom_price * 1.05, 4)
            else:
                new_low = full_row.get("verified_low")
                new_high = full_row.get("verified_high")

            if new_low and new_high:
                _update_seed_csv(
                    item=full_row["line_item"],
                    new_low=new_low,
                    new_high=new_high,
                )

        return row
    except HTTPException:
        raise
    except Exception as exc:
        logging.exception("resolve_queue_item failed")
        raise HTTPException(status_code=500, detail="Internal server error") from exc


@verification_router.post("/verify/outcome")
async def submit_outcome(req: OutcomeRequest):
    """Submit actual job cost after closeout; updates calibration data."""
    _validate_client_id(req.client_id)
    try:
        import asyncio
        profile = await asyncio.to_thread(
            record_actual_outcome,
            client_id=req.client_id,
            tournament_id=req.tournament_id,
            actual_cost=req.actual_cost,
            won=req.won,
            win_probability=req.win_probability,
        )
        return {
            "client_id": req.client_id,
            "tournament_id": req.tournament_id,
            "calibration": profile.get("calibration", {}),
        }
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logging.exception("submit_outcome failed")
        raise HTTPException(status_code=500, detail="Internal server error") from exc


@verification_router.get("/verify/accuracy/{client_id}")
async def get_accuracy(client_id: str):
    """Return agent accuracy and win probability calibration report for a client."""
    _validate_client_id(client_id)
    try:
        import asyncio
        report = await asyncio.to_thread(get_agent_accuracy_report, client_id)
        return report
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logging.exception("get_accuracy failed")
        raise HTTPException(status_code=500, detail="Internal server error") from exc


@verification_router.post("/verify/run")
async def run_verification():
    """On-demand: trigger verification of all material_costs.csv rows. Waits for completion."""
    try:
        result = await run_verification_batch()
        return result
    except Exception as exc:
        logging.exception("run_verification failed")
        raise HTTPException(status_code=500, detail="Internal server error") from exc
