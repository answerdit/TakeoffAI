"""
TakeoffAI — Bid Data Upload Router
Accepts CSV, Excel, and manual JSON uploads of historical bid records.
Thin HTTP layer; parsing and persistence delegated to helpers below.
"""

import io
from typing import Optional

import pandas as pd
from fastapi import APIRouter, Form, HTTPException, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from backend.agents.feedback_loop import update_client_profile_from_upload

upload_router = APIRouter(prefix="/upload", tags=["upload"])

# ── Schema ────────────────────────────────────────────────────────────────────

TEMPLATE_COLS = [
    "project_name",
    "description",
    "location",
    "zip_code",
    "bid_date",
    "trade_type",
    "your_bid_amount",
    "won",
    "winning_bid_amount",
    "actual_cost",
    "notes",
]

REQUIRED_COLS = {"project_name", "zip_code", "bid_date", "your_bid_amount"}

_SAMPLE_ROW = {
    "project_name": "Smith Office Remodel",
    "description": "Interior renovation 2400SF — new partitions, drop ceiling, LVP flooring",
    "location": "Abilene TX",
    "zip_code": "79601",
    "bid_date": "2024-03-15",
    "trade_type": "general",
    "your_bid_amount": 142800,
    "won": "true",
    "winning_bid_amount": 138500,
    "actual_cost": 128000,
    "notes": "Won on best-value pricing vs. two other GCs",
}


# ── Row parser ────────────────────────────────────────────────────────────────

def _parse_bid_row(raw: dict, row_num: int) -> tuple[Optional[dict], Optional[str]]:
    """
    Validate and coerce one bid row from a DataFrame row dict.
    Returns (bid_dict, None) on success or (None, error_string) on failure.
    """
    # Normalize keys
    row = {
        str(k).strip().lower().replace(" ", "_"): v
        for k, v in raw.items()
        if v is not None and str(v).strip() not in ("", "nan", "NaN", "None")
    }

    missing = REQUIRED_COLS - set(row.keys())
    if missing:
        return None, f"Row {row_num}: missing required fields: {', '.join(sorted(missing))}"

    for field in REQUIRED_COLS:
        if not str(row.get(field, "")).strip():
            return None, f"Row {row_num}: empty required field '{field}'"

    try:
        your_bid = float(str(row["your_bid_amount"]).replace(",", "").replace("$", ""))
    except (ValueError, TypeError):
        return None, f"Row {row_num}: invalid your_bid_amount '{row['your_bid_amount']}'"

    won_raw = str(row.get("won", "false")).lower().strip()
    won = won_raw in ("true", "1", "yes", "y", "won", "x")

    def _optional_float(key: str) -> Optional[float]:
        val = row.get(key)
        if val is None:
            return None
        try:
            return float(str(val).replace(",", "").replace("$", ""))
        except (ValueError, TypeError):
            return None

    return {
        "project_name": str(row.get("project_name", "")).strip(),
        "description": str(row.get("description", "")).strip(),
        "location": str(row.get("location", "")).strip(),
        "zip_code": str(row.get("zip_code", "")).strip(),
        "bid_date": str(row.get("bid_date", "")).strip(),
        "trade_type": str(row.get("trade_type", "general")).strip() or "general",
        "your_bid_amount": your_bid,
        "won": won,
        "winning_bid_amount": _optional_float("winning_bid_amount"),
        "actual_cost": _optional_float("actual_cost"),
        "notes": str(row.get("notes", "")).strip(),
    }, None


def _process_dataframe(df: pd.DataFrame) -> tuple[list[dict], list[str]]:
    """Parse all rows from a DataFrame; return (valid_bids, error_list)."""
    # Normalize column names
    df.columns = df.columns.str.strip().str.lower().str.replace(r"\s+", "_", regex=True)

    bids, errors = [], []
    for i, row in enumerate(df.to_dict("records"), start=2):  # row 1 = header
        bid, err = _parse_bid_row(row, i)
        if bid:
            bids.append(bid)
        else:
            errors.append(err)
    return bids, errors


def _import_summary(client_id: str, bids: list[dict], errors: list[str]) -> dict:
    """Run the feedback loop update and return a summary response."""
    won_count = sum(1 for b in bids if b.get("won"))
    profile = update_client_profile_from_upload(client_id, bids)
    return {
        "status": "ok",
        "client_id": client_id,
        "rows_imported": len(bids),
        "rows_won": won_count,
        "rows_skipped": len(errors),
        "errors": errors,
        "profile_summary": {
            "total_winning_examples": len(profile.get("winning_examples", [])),
            "avg_winning_bid": profile.get("stats", {}).get("avg_winning_bid", 0),
            "upload_stats": profile.get("upload_stats", {}),
        },
    }


# ── Endpoints ─────────────────────────────────────────────────────────────────

@upload_router.post("/bids/csv")
async def upload_csv(
    file: UploadFile,
    client_id: str = Form(..., description="Client ID to update"),
):
    """Parse a CSV bid history file and import into client profile."""
    if not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="File must be a .csv")
    try:
        contents = await file.read()
        df = pd.read_csv(io.BytesIO(contents), dtype=str)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Could not parse CSV: {exc}") from exc

    if df.empty:
        raise HTTPException(status_code=422, detail="CSV file is empty")

    bids, errors = _process_dataframe(df)
    if not bids:
        raise HTTPException(status_code=422, detail={"message": "No valid rows found", "errors": errors})

    try:
        return _import_summary(client_id, bids, errors)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@upload_router.post("/bids/excel")
async def upload_excel(
    file: UploadFile,
    client_id: str = Form(..., description="Client ID to update"),
):
    """Parse an Excel (.xlsx) bid history file and import into client profile."""
    if not file.filename.lower().endswith((".xlsx", ".xls")):
        raise HTTPException(status_code=400, detail="File must be .xlsx or .xls")
    try:
        contents = await file.read()
        df = pd.read_excel(io.BytesIO(contents), dtype=str, engine="openpyxl")
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Could not parse Excel file: {exc}") from exc

    if df.empty:
        raise HTTPException(status_code=422, detail="Excel file is empty")

    bids, errors = _process_dataframe(df)
    if not bids:
        raise HTTPException(status_code=422, detail={"message": "No valid rows found", "errors": errors})

    try:
        return _import_summary(client_id, bids, errors)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


class ManualBidRecord(BaseModel):
    project_name: str
    description: str = ""
    location: str = ""
    zip_code: str
    bid_date: str
    trade_type: str = "general"
    your_bid_amount: float
    won: bool = False
    winning_bid_amount: Optional[float] = None
    actual_cost: Optional[float] = None
    notes: str = ""


class ManualUploadRequest(BaseModel):
    client_id: str
    bids: list[ManualBidRecord]


@upload_router.post("/bids/manual")
async def upload_manual(req: ManualUploadRequest):
    """Import bid records submitted as a JSON array from the manual entry table."""
    if not req.bids:
        raise HTTPException(status_code=422, detail="No bid records provided")

    bids = [b.model_dump() for b in req.bids]
    errors: list[str] = []
    valid: list[dict] = []
    for i, bid in enumerate(bids, start=1):
        _, err = _parse_bid_row(bid, i)
        if err:
            errors.append(err)
        else:
            valid.append(bid)

    if not valid:
        raise HTTPException(status_code=422, detail={"message": "No valid rows", "errors": errors})

    try:
        return _import_summary(req.client_id, valid, errors)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ── Template downloads ────────────────────────────────────────────────────────

@upload_router.get("/template/csv")
async def template_csv():
    """Download a CSV template pre-filled with one sample row."""
    sample = pd.DataFrame([_SAMPLE_ROW], columns=TEMPLATE_COLS)
    buf = io.StringIO()
    sample.to_csv(buf, index=False)
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="takeoffai_bid_template.csv"'},
    )


@upload_router.get("/template/excel")
async def template_excel():
    """Download an Excel template pre-filled with one sample row."""
    sample = pd.DataFrame([_SAMPLE_ROW], columns=TEMPLATE_COLS)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        sample.to_excel(writer, index=False, sheet_name="Bid History")
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="takeoffai_bid_template.xlsx"'},
    )


# ── Confirm import to client profile ─────────────────────────────────────────

class ImportRequest(BaseModel):
    client_id: str = "default"
    records: list[dict]


@upload_router.post("/import")
async def import_bids(req: ImportRequest):
    """Persist parsed bid records into the client profile for tournament learning."""
    from backend.agents.feedback_loop import update_client_profile_from_upload
    if not req.records:
        raise HTTPException(status_code=400, detail="No records provided.")
    result = update_client_profile_from_upload(req.client_id, req.records)
    return {"status": "imported", "client_id": req.client_id, **result}
