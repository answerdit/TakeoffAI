"""
Wiki Manager — TakeoffAI
LLM-maintained Obsidian knowledge base for job tracking and institutional memory.

All wiki I/O goes through this module. No other code writes to the wiki/ directory.
"""

import asyncio
import json
import logging
import os
import re
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import yaml
from anthropic import AsyncAnthropic

from backend.config import settings

logger = logging.getLogger(__name__)

WIKI_DIR = Path(__file__).parent.parent.parent / "wiki"
JOBS_DIR = WIKI_DIR / "jobs"
CLIENTS_DIR = WIKI_DIR / "clients"
MATERIALS_DIR = WIKI_DIR / "materials"
PERSONALITIES_DIR = WIKI_DIR / "personalities"
SCHEMA_PATH = WIKI_DIR / "SCHEMA.md"

WIKI_MODEL = os.getenv("WIKI_MODEL", settings.wiki_model)

_anthropic = AsyncAnthropic()

# ── Frontmatter helpers ──────────────────────────────────────────────────────


def _parse_frontmatter(path: Path) -> tuple[dict, str]:
    """
    Parse a markdown file with optional YAML frontmatter.
    Returns (metadata_dict, body_string). If file doesn't exist or has no
    frontmatter, returns ({}, "") or ({}, body).
    """
    if not path.exists():
        return {}, ""
    content = path.read_text(encoding="utf-8")
    if not content.startswith("---"):
        return {}, content
    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}, content
    try:
        meta = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        return {}, content
    body = parts[2].lstrip("\n")
    return meta, body


def _write_page(path: Path, meta: dict, body: str) -> None:
    """Write a markdown file with YAML frontmatter. Creates parent dirs if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    frontmatter = yaml.dump(meta, default_flow_style=False, sort_keys=False).strip()
    content = f"---\n{frontmatter}\n---\n\n{body}\n"
    path.write_text(content, encoding="utf-8")


def _read_page(path: Path) -> tuple[dict, str]:
    """Alias for _parse_frontmatter — reads a wiki page into (meta, body)."""
    return _parse_frontmatter(path)


# ── Schema loading (cached) ──────────────────────────────────────────────────

_schema_cache: Optional[str] = None


def _load_schema() -> str:
    """Load SCHEMA.md content, cached after first read."""
    global _schema_cache
    if _schema_cache is not None:
        return _schema_cache
    if SCHEMA_PATH.exists():
        _schema_cache = SCHEMA_PATH.read_text(encoding="utf-8")
    else:
        _schema_cache = ""
    return _schema_cache


# ── LLM synthesis ────────────────────────────────────────────────────────────

_SYSTEM_BASE = (
    "You are a knowledge base writer for TakeoffAI, a construction bidding system. "
    "Write clear, specific markdown for contractors reviewing their bidding history. "
    "Include exact dollar amounts, percentages, agent names, and dates. "
    "Use [[folder/page-slug]] wikilinks for cross-references. "
    "Return ONLY the markdown body content — no frontmatter, no code fences."
)


async def _synthesize(
    context: str,
    instruction: str,
) -> str:
    """
    Single LLM call to generate wiki page content.
    Returns markdown string (body only, no frontmatter).
    """
    schema = _load_schema()
    system = f"{_SYSTEM_BASE}\n\n{schema}" if schema else _SYSTEM_BASE

    response = await _anthropic.messages.create(
        model=WIKI_MODEL,
        max_tokens=2048,
        system=system,
        messages=[{
            "role": "user",
            "content": f"Context:\n{context}\n\nInstruction:\n{instruction}",
        }],
    )
    return response.content[0].text.strip()


# ── Section helpers ──────────────────────────────────────────────────────────


def _append_section(body: str, new_section: str) -> str:
    """
    Append a new section to the page body.
    Inserts before ## Links if that section exists, otherwise appends at end.
    """
    links_marker = "\n## Links"
    if links_marker in body:
        idx = body.index(links_marker)
        return body[:idx].rstrip() + "\n\n" + new_section.strip() + "\n" + body[idx:]
    return body.rstrip() + "\n\n" + new_section.strip() + "\n"


# ── Public functions ─────────────────────────────────────────────────────────


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
    slug = _make_job_slug(client_id, project_name, today)
    page_path = JOBS_DIR / f"{slug}.md"

    # LLM writes the initial page body (title + scope)
    body = await _synthesize(
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

    _write_page(page_path, meta, body)

    # Ensure client page exists
    _ensure_client_page(client_id)

    return {"job_slug": slug, "status": "prospect"}


def _ensure_client_page(client_id: str) -> None:
    """Create a minimal client page if one doesn't exist yet."""
    client_path = CLIENTS_DIR / f"{client_id}.md"
    if client_path.exists():
        # Increment total_jobs counter
        meta, body = _parse_frontmatter(client_path)
        meta["total_jobs"] = meta.get("total_jobs", 0) + 1
        _write_page(client_path, meta, body)
        return

    meta = {
        "client_id": client_id,
        "first_job": date.today().isoformat(),
        "total_jobs": 1,
        "wins": 0,
        "losses": 0,
    }
    body = (
        f"# {client_id}\n\n"
        "## Profile\nNew client — profile will be enriched as jobs progress.\n\n"
        "## Recent Jobs\n\n"
        "## Patterns\nInsufficient data for pattern analysis.\n"
    )
    _write_page(client_path, meta, body)


def _make_job_slug(client_id: str, project_name: str, date_str: str) -> str:
    """
    Generate a kebab-case job slug: YYYY-MM-DD-{client}-{short-description}.
    Strips special characters, collapses whitespace, converts underscores to dashes.
    """
    raw = f"{date_str}-{client_id}-{project_name}"
    # Convert underscores to dashes
    raw = raw.replace("_", "-")
    # Remove anything that's not alphanumeric, space, or dash
    cleaned = re.sub(r"[^a-zA-Z0-9\s-]", "", raw)
    # Collapse whitespace and dashes into single dashes
    slug = re.sub(r"[\s-]+", "-", cleaned).strip("-").lower()
    return slug


async def enrich_estimate(job_slug: str, estimate_data: dict) -> None:
    """
    Append Estimate section to a job page and update status to 'estimated'.
    No-op if the job page doesn't exist (fire-and-forget safe).
    """
    try:
        page_path = JOBS_DIR / f"{job_slug}.md"
        if not page_path.exists():
            logger.debug("enrich_estimate: job page %s not found, skipping", job_slug)
            return

        meta, body = _read_page(page_path)

        meta["status"] = "estimated"
        meta["estimate_total"] = estimate_data.get("total_bid")
        meta["estimate_low"] = estimate_data.get("estimate_low")
        meta["estimate_high"] = estimate_data.get("estimate_high")

        section = await _synthesize(
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

        body = _append_section(body, section)
        _write_page(page_path, meta, body)
    except Exception:
        logger.exception("enrich_estimate: failed for job %s", job_slug)


async def enrich_tournament(job_slug: str, tournament_data: dict) -> None:
    """
    Append Tournament section to a job page and update status to 'tournament-complete'.
    No-op if the job page doesn't exist.
    """
    try:
        page_path = JOBS_DIR / f"{job_slug}.md"
        if not page_path.exists():
            logger.debug("enrich_tournament: job page %s not found, skipping", job_slug)
            return

        meta, body = _read_page(page_path)

        entries = tournament_data.get("consensus_entries", [])
        bids = [e["total_bid"] for e in entries if e.get("total_bid")]

        meta["status"] = "tournament-complete"
        meta["tournament_id"] = tournament_data.get("tournament_id")
        meta["band_low"] = min(bids) if bids else None
        meta["band_high"] = max(bids) if bids else None

        meta["winner_personality"] = None  # reset before conditional
        if entries:
            winner = min(entries, key=lambda e: e.get("total_bid", float("inf")))
            meta["winner_personality"] = winner.get("agent_name")

        section = await _synthesize(
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

        body = _append_section(body, section)
        _write_page(page_path, meta, body)
    except Exception:
        logger.exception("enrich_tournament: failed for job %s", job_slug)


async def record_bid_decision(
    job_slug: str,
    our_bid: float,
    notes: str = "",
) -> None:
    """Append Bid Decision section and update status to bid-submitted."""
    page_path = JOBS_DIR / f"{job_slug}.md"
    if not page_path.exists():
        logger.debug("record_bid_decision: job page %s not found, skipping", job_slug)
        return

    meta, body = _read_page(page_path)
    meta["status"] = "bid-submitted"
    meta["our_bid"] = our_bid

    section = await _synthesize(
        context=(
            f"Existing page:\n{body}\n\n"
            f"Bid decision: ${our_bid:,.2f}\n"
            f"Notes: {notes}"
        ),
        instruction=(
            "Write a ## Bid Decision section. Summarize:\n"
            "- Which bid amount was chosen and why\n"
            "- How it relates to the tournament band\n"
            "- Risk assessment for this number\n"
            "Do not repeat earlier sections."
        ),
    )

    body = _append_section(body, section)
    _write_page(page_path, meta, body)


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
    Step 1: Update job page
    Step 2: Update client page
    Step 3: Update personality pages
    Step 4: Update material pages (triggered by PriceVerifier separately)
    """
    page_path = JOBS_DIR / f"{job_slug}.md"
    if not page_path.exists():
        logger.warning("cascade_outcome: job page %s not found", job_slug)
        return

    meta, body = _read_page(page_path)

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
    section = await _synthesize(
        context=f"Existing page:\n{body}\n\nOutcome data:\n{json.dumps(context_data, default=str)}",
        instruction=(
            f"Write or update the ## Outcome section for status={status}. Include:\n"
            "- The result (won/lost/closed)\n"
            "- If actual_cost is provided, analyze deviation from our_bid\n"
            "- Lessons learned or patterns observed\n"
            "Do not repeat earlier sections."
        ),
    )
    body = _append_section(body, section)
    _write_page(page_path, meta, body)

    # ── Step 2: Update client page ───────────────────────────────────────
    client_id = meta.get("client")
    if client_id:
        try:
            await _update_client_page_on_outcome(client_id, job_slug, meta, status)
        except Exception:
            logger.exception("cascade: failed to update client page for %s", client_id)

    # ── Step 3: Update personality pages ─────────────────────────────────
    personality = meta.get("winner_personality")
    if personality:
        try:
            await _update_personality_page(personality, job_slug, meta, status)
        except Exception:
            logger.exception("cascade: failed to update personality page %s", personality)

    # ── Step 4: Material pages (future — triggered by PriceVerifier) ─────
    # update_material_page is called separately by PriceVerifier, not here


async def _update_client_page_on_outcome(
    client_id: str,
    job_slug: str,
    job_meta: dict,
    status: str,
) -> None:
    """Update client wiki page with outcome from a job."""
    client_path = CLIENTS_DIR / f"{client_id}.md"
    if not client_path.exists():
        _ensure_client_page(client_id)

    meta, body = _read_page(client_path)

    if status == "won":
        meta["wins"] = meta.get("wins", 0) + 1
    elif status == "lost":
        meta["losses"] = meta.get("losses", 0) + 1

    updated_body = await _synthesize(
        context=(
            f"Current client page:\n{body}\n\n"
            f"New outcome: job [[jobs/{job_slug}]] status={status}\n"
            f"Job details: {json.dumps(job_meta, default=str)}"
        ),
        instruction=(
            "Rewrite this client page body with the new outcome incorporated. Maintain:\n"
            "- ## Profile section\n"
            "- ## Win/Loss Summary with updated narrative\n"
            "- ## Recent Jobs with [[jobs/slug]] wikilink for the new job\n"
            "- ## Patterns section with any updated observations\n"
            "Keep existing job links. Add the new one."
        ),
    )

    _write_page(client_path, meta, updated_body)


async def _update_personality_page(
    personality: str,
    job_slug: str,
    job_meta: dict,
    status: str,
) -> None:
    """Update or create a personality wiki page with outcome from a job."""
    filename = personality.replace("_", "-")
    page_path = PERSONALITIES_DIR / f"{filename}.md"

    if not page_path.exists():
        _seed_personality_page(personality)

    meta, body = _read_page(page_path)

    if status == "won":
        meta["wins"] = meta.get("wins", 0) + 1
    meta["total_tournaments"] = meta.get("total_tournaments", 0) + 1

    total = meta["total_tournaments"]
    meta["win_rate"] = round(meta.get("wins", 0) / total, 4) if total > 0 else 0.0

    updated_body = await _synthesize(
        context=(
            f"Current personality page:\n{body}\n\n"
            f"New result: job [[jobs/{job_slug}]] status={status}\n"
            f"Job details: {json.dumps(job_meta, default=str)}"
        ),
        instruction=(
            "Update this personality page with the new job result. Add a short note to "
            "## Recent Results with the job wikilink, bid amount, and outcome. "
            "Update ## Performance if any new patterns are visible. "
            "Keep all existing content."
        ),
    )

    _write_page(page_path, meta, updated_body)


def _seed_personality_page(personality: str) -> None:
    """Create a personality page seeded from PERSONALITY_PROMPTS."""
    from backend.agents.tournament import PERSONALITY_PROMPTS

    filename = personality.replace("_", "-")
    page_path = PERSONALITIES_DIR / f"{filename}.md"
    prompt_text = PERSONALITY_PROMPTS.get(personality, "No prompt defined.")
    display_name = personality.replace("_", " ").title()

    meta = {
        "personality": personality,
        "total_tournaments": 0,
        "wins": 0,
        "win_rate": 0.0,
    }
    body = (
        f"# {display_name}\n\n"
        f"## Philosophy\n{prompt_text}\n\n"
        "## Performance\nNo data yet.\n\n"
        "## Recent Results\n\n"
        "## Evolution History\n"
    )
    _write_page(page_path, meta, body)
