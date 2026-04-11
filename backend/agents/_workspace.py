"""
Google Workspace integration — TakeoffAI
Wraps `gws` CLI calls for Gmail notifications, Calendar events, and Sheets logging.

All public functions are async fire-and-forget wrappers.
Every function is a no-op when settings.gws_enabled is False or the required
config value (email, sheet ID) is empty — safe to call unconditionally.

Prerequisites:
  brew install googleworkspace-cli
  gws auth login -s gmail,calendar,sheets
"""

import asyncio
import json
import logging
import shutil
import subprocess
from datetime import datetime, timezone
from typing import Optional

from backend.config import settings

log = logging.getLogger(__name__)


def _validate_gws_bin() -> None:
    """Warn at startup if gws_bin cannot be resolved to an executable."""
    if settings.gws_enabled and not shutil.which(settings.gws_bin):
        log.warning(
            "GWS_BIN=%r not found on PATH — Google Workspace calls will fail. "
            "Install gws or set GWS_BIN to the full path.",
            settings.gws_bin,
        )


_validate_gws_bin()


# ── Low-level CLI wrapper ─────────────────────────────────────────────────────


def _gws(*args: str) -> dict:
    """
    Run a `gws` CLI command synchronously (call via asyncio.to_thread).
    Returns parsed JSON on success, empty dict on any failure.
    Logs warnings but never raises — all workspace calls are best-effort.
    """
    cmd = [settings.gws_bin] + list(args)
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            log.warning(
                "gws command failed (exit %d): %s\nstderr: %s",
                result.returncode,
                " ".join(cmd),
                result.stderr[:400],
            )
            return {}
        try:
            return json.loads(result.stdout) if result.stdout.strip() else {}
        except json.JSONDecodeError:
            return {"output": result.stdout.strip()}
    except FileNotFoundError:
        log.error(
            "gws CLI not found at '%s'. Install: brew install googleworkspace-cli",
            settings.gws_bin,
        )
        return {}
    except subprocess.TimeoutExpired:
        log.warning("gws command timed out: %s", " ".join(cmd))
        return {}
    except Exception as exc:
        log.warning("gws command error: %s", exc)
        return {}


def _safe_val(v: str) -> str:
    """Sanitize a value for CSV-style sheet append: replace commas with semicolons."""
    return str(v or "").replace(",", ";").replace("\n", " ").strip()


def _sanitize_arg(v: str, max_len: int = 200) -> str:
    """Strip control characters and truncate CLI argument values."""
    # Remove newlines, carriage returns, and null bytes that could confuse the CLI
    v = v.replace("\r", " ").replace("\n", " ").replace("\x00", "")
    # Strip leading/trailing whitespace
    v = v.strip()
    return v[:max_len]


# ── Gmail ─────────────────────────────────────────────────────────────────────


async def _gmail_send(subject: str, body: str) -> None:
    """Send a notification email. No-op if gws_enabled or gws_notify_email is unset."""
    if not settings.gws_enabled or not settings.gws_notify_email:
        return
    await asyncio.to_thread(
        _gws,
        "gmail",
        "+send",
        "--to",
        settings.gws_notify_email,
        "--subject",
        subject,
        "--body",
        body,
    )


async def notify_job_created(
    *,
    job_slug: str,
    client_id: str,
    project_name: str,
    description: str,
    zip_code: str,
    trade_type: str,
) -> None:
    safe_project_name = _sanitize_arg(project_name)
    safe_job_slug = _sanitize_arg(job_slug)
    safe_client_id = _sanitize_arg(client_id)
    safe_zip_code = _sanitize_arg(zip_code)
    safe_trade_type = _sanitize_arg(trade_type)
    safe_description = _sanitize_arg(description, max_len=600)
    await _gmail_send(
        subject=f"[TakeoffAI] New prospect: {safe_project_name}",
        body=(
            f"A new job prospect has been created.\n\n"
            f"Job:      {safe_project_name}\n"
            f"Slug:     {safe_job_slug}\n"
            f"Client:   {safe_client_id}\n"
            f"Zip:      {safe_zip_code}\n"
            f"Trade:    {safe_trade_type}\n\n"
            f"{safe_description}"
        ),
    )


async def notify_bid_submitted(
    *,
    job_slug: str,
    client_id: str,
    our_bid: float,
) -> None:
    safe_job_slug = _sanitize_arg(job_slug)
    safe_client_id = _sanitize_arg(client_id)
    await _gmail_send(
        subject=f"[TakeoffAI] Bid submitted — {safe_job_slug}",
        body=(
            f"A bid has been submitted.\n\n"
            f"Job:      {safe_job_slug}\n"
            f"Client:   {safe_client_id}\n"
            f"Our Bid:  ${our_bid:,.2f}\n"
        ),
    )


async def notify_tournament_judged(
    *,
    tournament_id: int,
    client_id: str,
    winner_agent: str,
    winner_bid: float,
    mode: str,
) -> None:
    safe_client_id = _sanitize_arg(client_id)
    safe_winner_agent = _sanitize_arg(winner_agent)
    safe_mode = _sanitize_arg(mode)
    await _gmail_send(
        subject=f"[TakeoffAI] Tournament #{tournament_id} judged",
        body=(
            f"Tournament #{tournament_id} has been judged.\n\n"
            f"Client:        {safe_client_id}\n"
            f"Winner agent:  {safe_winner_agent}\n"
            f"Winning bid:   ${winner_bid:,.2f}\n"
            f"Judge mode:    {safe_mode}\n"
        ),
    )


async def notify_outcome(
    *,
    job_slug: str,
    client_id: str,
    status: str,
    our_bid: Optional[float] = None,
    actual_cost: Optional[float] = None,
) -> None:
    safe_job_slug = _sanitize_arg(job_slug)
    safe_client_id = _sanitize_arg(client_id)
    safe_status = _sanitize_arg(status)
    label = {"won": "WON", "lost": "LOST", "closed": "CLOSED"}.get(safe_status, safe_status.upper())
    lines = [
        f"Job outcome recorded.\n",
        f"Job:      {safe_job_slug}",
        f"Client:   {safe_client_id}",
        f"Status:   {label}",
    ]
    if our_bid is not None:
        lines.append(f"Our Bid:  ${our_bid:,.2f}")
    if actual_cost is not None:
        lines.append(f"Actual:   ${actual_cost:,.2f}")
    if our_bid and actual_cost and status == "won":
        margin = (our_bid - actual_cost) / our_bid * 100
        lines.append(f"Margin:   {margin:.1f}%")

    await _gmail_send(
        subject=f"[TakeoffAI] {label}: {safe_job_slug}",
        body="\n".join(lines),
    )


# ── Sheets ────────────────────────────────────────────────────────────────────


async def log_tournament_to_sheet(
    *,
    tournament_id: int,
    client_id: str,
    description: str,
    consensus_entries: list[dict],
) -> None:
    """
    Append one row per consensus entry to the tournament tracking sheet.
    Columns: timestamp, tournament_id, client_id, description, agent, total_bid, confidence
    """
    if not settings.gws_enabled or not settings.gws_tournament_sheet_id:
        return

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    desc_safe = _safe_val(description[:80])

    tasks = []
    for entry in consensus_entries:
        row = ",".join(
            [
                _safe_val(ts),
                str(tournament_id),
                _safe_val(client_id),
                desc_safe,
                _safe_val(entry.get("agent_name", "")),
                str(entry.get("total_bid", "")),
                _safe_val(entry.get("confidence", "")),
            ]
        )
        tasks.append(
            asyncio.to_thread(
                _gws,
                "sheets",
                "+append",
                "--spreadsheet",
                settings.gws_tournament_sheet_id,
                "--values",
                row,
            )
        )
    await asyncio.gather(*tasks, return_exceptions=True)


async def log_price_audit_to_sheet(records: list[dict]) -> None:
    """
    Append flagged price audit records to the audit sheet.
    Columns: date, line_item, unit, ai_cost, verified_low, verified_high, deviation_pct
    Only rows with flagged=True are logged.
    """
    if not settings.gws_enabled or not settings.gws_price_audit_sheet_id:
        return

    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    flagged = [r for r in records if r.get("flagged")]
    if not flagged:
        return

    tasks = []
    for r in flagged:
        row = ",".join(
            [
                date_str,
                _safe_val(r.get("line_item", r.get("description", ""))),
                _safe_val(r.get("unit", "")),
                str(r.get("ai_unit_cost", r.get("unit_material_cost", ""))),
                str(r.get("verified_low", "")),
                str(r.get("verified_high", "")),
                str(r.get("deviation_pct", "")),
            ]
        )
        tasks.append(
            asyncio.to_thread(
                _gws,
                "sheets",
                "+append",
                "--spreadsheet",
                settings.gws_price_audit_sheet_id,
                "--values",
                row,
            )
        )
    await asyncio.gather(*tasks, return_exceptions=True)


# ── Calendar ──────────────────────────────────────────────────────────────────


async def create_bid_deadline_event(
    *,
    job_slug: str,
    project_name: str,
    due_date: str,  # ISO date: "2026-04-15"
) -> None:
    """
    Create an all-day calendar event for a bid deadline.
    No-op if gws_enabled is False or due_date is empty.
    """
    if not settings.gws_enabled or not due_date:
        return

    safe_project_name = _sanitize_arg(project_name)
    safe_job_slug = _sanitize_arg(job_slug)
    await asyncio.to_thread(
        _gws,
        "calendar",
        "+insert",
        "--summary",
        f"[TakeoffAI] Bid Due: {safe_project_name}",
        "--start",
        due_date,
        "--end",
        due_date,
        "--description",
        f"Bid deadline for job {safe_job_slug}",
        "--calendar",
        settings.gws_calendar_id,
    )
