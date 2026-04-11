"""
Wiki job lifecycle — TakeoffAI
All job page operations: create, enrich (estimate/tournament/scope/bid), cascade outcome.
"""

import asyncio
import json
import logging
import re
from datetime import date
from typing import Optional

from backend.agents import _wiki_entities as _ent
from backend.agents import _wiki_io as _io
from backend.agents import _wiki_llm as _llm

logger = logging.getLogger(__name__)


async def create_job(
    client_id: str,
    project_name: str,
    description: str,
    zip_code: str,
    trade_type: str = "general",
) -> dict:
    """
    Create a new job wiki page at prospect status.
    Also creates the client page if it doesn't exist yet.
    Returns dict with job_slug and status.
    """
    today = date.today().isoformat()
    slug = _io._make_job_slug(client_id, project_name, today)
    page_path = _io.JOBS_DIR / f"{slug}.md"

    body = await _llm._synthesize(
        context=(
            f"Project: {project_name}\n"
            f"Client: {client_id}\n"
            f"Description: {description}\n"
            f"Location ZIP: {zip_code}\n"
            f"Trade: {trade_type}"
        ),
        instruction=(
            "Write the initial wiki page for this job. Include:\n"
            "1. A markdown H1 title combining the project name and location\n"
            "2. A ## Scope section summarizing the project description\n"
            "3. A ## Links section with a wikilink to the client page: [[clients/{client_id}]]\n"
            "Do not include frontmatter."
        ).format(client_id=client_id),
    )

    meta = {
        "status": "prospect",
        "client": client_id,
        "date": today,
        "trade": trade_type,
        "zip": zip_code,
        "tags": ["job", "prospect", trade_type],
        "our_bid": None,
        "estimate_total": None,
        "estimate_low": None,
        "estimate_high": None,
        "tournament_id": None,
        "winner_personality": None,
        "band_low": None,
        "band_high": None,
        "actual_cost": None,
        "outcome_date": None,
    }

    await asyncio.to_thread(_io._write_page, page_path, meta, body)
    _ent._ensure_client_page(client_id)

    return {"job_slug": slug, "status": "prospect"}


async def enrich_scope_from_blueprint(job_slug: str, draft_text: str) -> None:
    """
    Overwrite the ## Scope section of a job page with blueprint-extracted draft text.
    Fire-and-forget safe — all errors are caught and logged.
    """
    try:
        page_path = _io._safe_job_path(job_slug)
        if page_path is None:
            return
        if not page_path.exists():
            logger.warning("enrich_scope_from_blueprint: job page not found for %s", job_slug)
            return

        meta, body = await asyncio.to_thread(_io.read_page, page_path)

        scope_section = f"## Scope\n\n{draft_text.strip()}"
        scope_pattern = re.compile(r"## Scope\n[\s\S]*?(?=\n## |\Z)", re.MULTILINE)

        if scope_pattern.search(body):
            body = scope_pattern.sub(scope_section, body, count=1)
        else:
            body = scope_section + "\n\n" + body.lstrip()

        await asyncio.to_thread(_io._write_page, page_path, meta, body)
        logger.info("enrich_scope_from_blueprint: updated Scope for %s", job_slug)
    except Exception:
        logger.exception("enrich_scope_from_blueprint failed for %s (non-fatal)", job_slug)


async def enrich_estimate(job_slug: str, estimate_data: dict) -> None:
    """
    Append Estimate section to a job page and update status to 'estimated'.
    No-op if the job page doesn't exist (fire-and-forget safe).
    """
    try:
        page_path = _io._safe_job_path(job_slug)
        if page_path is None:
            return
        if not page_path.exists():
            logger.debug("enrich_estimate: job page %s not found, skipping", job_slug)
            return

        meta, body = await asyncio.to_thread(_io.read_page, page_path)

        meta["status"] = "estimated"
        meta["estimate_total"] = estimate_data.get("total_bid")
        meta["estimate_low"] = estimate_data.get("estimate_low")
        meta["estimate_high"] = estimate_data.get("estimate_high")
        tags = meta.get("tags", ["job"])
        meta["tags"] = ["job", "estimated"] + [
            t for t in tags if t not in ("job", "prospect", "estimated")
        ]

        section = await _llm._synthesize(
            context=(
                f"Existing page:\n{body}\n\n"
                f"Estimate data:\n{json.dumps(estimate_data, indent=2, default=str)}"
            ),
            instruction=(
                "Write a ## Estimate section to append to this job page. Summarize:\n"
                "- Total bid amount and confidence level\n"
                "- Key line items and where costs are concentrated\n"
                "- The estimate range (low to high) and what it means for risk\n"
                "Do not repeat the Scope section. Do not include frontmatter."
            ),
        )

        body = _io._append_section(body, section)
        await asyncio.to_thread(_io._write_page, page_path, meta, body)
    except Exception:
        logger.exception("enrich_estimate: failed for job %s", job_slug)


async def enrich_tournament(job_slug: str, tournament_data: dict) -> None:
    """
    Append Tournament section to a job page and update status to 'tournament-complete'.
    No-op if the job page doesn't exist.
    """
    try:
        page_path = _io._safe_job_path(job_slug)
        if page_path is None:
            return
        if not page_path.exists():
            logger.debug("enrich_tournament: job page %s not found, skipping", job_slug)
            return

        meta, body = await asyncio.to_thread(_io.read_page, page_path)

        entries = tournament_data.get("consensus_entries", [])
        bids = [e["total_bid"] for e in entries if e.get("total_bid")]

        meta["status"] = "tournament-complete"
        meta["tournament_id"] = tournament_data.get("tournament_id")
        meta["band_low"] = min(bids) if bids else None
        meta["band_high"] = max(bids) if bids else None

        meta["winner_personality"] = None
        if entries:
            winner = min(entries, key=lambda e: e.get("total_bid", float("inf")))
            meta["winner_personality"] = winner.get("agent_name")

        section = await _llm._synthesize(
            context=(
                f"Existing page:\n{body}\n\n"
                f"Tournament data:\n{json.dumps(tournament_data, indent=2, default=str)}"
            ),
            instruction=(
                "Write a ## Tournament section to append to this job page. Summarize:\n"
                "- How many agents bid and the overall band (min to max)\n"
                "- Each agent's bid and confidence, noting agreements and divergences\n"
                "- Which agent came in lowest and what strategy drove that\n"
                "- Include [[personalities/agent-name]] wikilinks for each agent\n"
                "Do not repeat earlier sections. Do not include frontmatter."
            ),
        )

        body = _io._append_section(body, section)
        await asyncio.to_thread(_io._write_page, page_path, meta, body)
    except Exception:
        logger.exception("enrich_tournament: failed for job %s", job_slug)


async def record_bid_decision(job_slug: str, our_bid: float, notes: str = "") -> None:
    """Append Bid Decision section and update status to bid-submitted."""
    try:
        page_path = _io._safe_job_path(job_slug)
        if page_path is None:
            return
        if not page_path.exists():
            logger.debug("record_bid_decision: job page %s not found, skipping", job_slug)
            return

        meta, body = await asyncio.to_thread(_io.read_page, page_path)
        meta["status"] = "bid-submitted"
        meta["our_bid"] = our_bid

        section = await _llm._synthesize(
            context=(
                f"Existing page:\n{body}\n\n" f"Bid decision: ${our_bid:,.2f}\n" f"Notes: {notes}"
            ),
            instruction=(
                "Write a ## Bid Decision section. Summarize:\n"
                "- Which bid amount was chosen and why\n"
                "- How it relates to the tournament band\n"
                "- Risk assessment for this number\n"
                "Do not repeat earlier sections."
            ),
        )

        body = _io._append_section(body, section)
        await asyncio.to_thread(_io._write_page, page_path, meta, body)
    except Exception:
        logger.exception("record_bid_decision: failed for job %s", job_slug)


async def cascade_outcome(
    job_slug: str,
    status: str,
    actual_cost: Optional[float] = None,
    notes: str = "",
) -> None:
    """
    Full cascade on outcome (won/lost/closed).
    Step 1 (job page) is required — raises on failure, cascade aborts.
    Steps 2–3 (client, personality) are best-effort — logged and skipped on failure.
    """
    page_path = _io._safe_job_path(job_slug)
    if page_path is None:
        return
    if not page_path.exists():
        logger.warning("cascade_outcome: job page %s not found", job_slug)
        return

    meta, body = await asyncio.to_thread(_io.read_page, page_path)

    # ── Step 1: Update job page ──────────────────────────────────────────
    meta["status"] = status
    meta["outcome_date"] = date.today().isoformat()
    if actual_cost is not None:
        meta["actual_cost"] = actual_cost

    context_data = {
        "status": status,
        "our_bid": meta.get("our_bid"),
        "actual_cost": actual_cost,
        "notes": notes,
    }
    section = await _llm._synthesize(
        context=f"Existing page:\n{body}\n\nOutcome data:\n{json.dumps(context_data, default=str)}",
        instruction=(
            f"Write or update the ## Outcome section for status={status}. Include:\n"
            "- The result (won/lost/closed)\n"
            "- If actual_cost is provided, analyze deviation from our_bid\n"
            "- Lessons learned or patterns observed\n"
            "Do not repeat earlier sections."
        ),
    )
    body = _io._append_section(body, section)
    await asyncio.to_thread(_io._write_page, page_path, meta, body)

    # ── Step 2: Update client page ───────────────────────────────────────
    client_id = meta.get("client")
    if client_id:
        try:
            await _ent._update_client_page_on_outcome(client_id, job_slug, meta, status)
        except Exception:
            logger.exception("cascade: failed to update client page for %s", client_id)

    # ── Step 3: Update personality pages ─────────────────────────────────
    personality = meta.get("winner_personality")
    if personality:
        try:
            await _ent._update_personality_page(personality, job_slug, meta, status)
        except Exception:
            logger.exception("cascade: failed to update personality page %s", personality)
