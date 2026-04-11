"""
Wiki entity page writers — TakeoffAI
Client, personality, and material page creation and updates.
Called by _wiki_jobs.py (cascade) and the price verifier.
"""

import asyncio
import json
import logging
import re
from datetime import date

from backend.agents import _wiki_io as _io
from backend.agents import _wiki_llm as _llm

logger = logging.getLogger(__name__)


def _ensure_client_page(client_id: str) -> None:
    """Create a minimal client page if one doesn't exist yet."""
    client_path = (_io.CLIENTS_DIR / f"{client_id}.md").resolve()
    if not client_path.is_relative_to(_io.CLIENTS_DIR.resolve()):
        logger.warning("Rejected unsafe client_id in wiki entity write: %s", client_id)
        return
    if client_path.exists():
        meta, body = _io._parse_frontmatter(client_path)
        meta["total_jobs"] = meta.get("total_jobs", 0) + 1
        _io._write_page(client_path, meta, body)
        return

    meta = {
        "client_id": client_id,
        "first_job": date.today().isoformat(),
        "total_jobs": 1,
        "wins": 0,
        "losses": 0,
        "tags": ["client"],
    }
    body = (
        f"# {client_id}\n\n"
        "## Profile\nNew client — profile will be enriched as jobs progress.\n\n"
        "## Recent Jobs\n\n"
        "## Patterns\nInsufficient data for pattern analysis.\n"
    )
    _io._write_page(client_path, meta, body)


async def _update_client_page_on_outcome(
    client_id: str,
    job_slug: str,
    job_meta: dict,
    status: str,
) -> None:
    """Update client wiki page with outcome from a job."""
    client_path = _io.CLIENTS_DIR / f"{client_id}.md"
    if not client_path.exists():
        _ensure_client_page(client_id)

    meta, body = await asyncio.to_thread(_io.read_page, client_path)

    if status == "won":
        meta["wins"] = meta.get("wins", 0) + 1
    elif status == "lost":
        meta["losses"] = meta.get("losses", 0) + 1

    updated_body = await _llm._synthesize(
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

    await asyncio.to_thread(_io._write_page, client_path, meta, updated_body)


def _seed_personality_page(personality: str) -> None:
    """Create a personality page seeded from PERSONALITY_PROMPTS."""
    from backend.agents.tournament import PERSONALITY_PROMPTS

    filename = personality.replace("_", "-")
    page_path = _io.PERSONALITIES_DIR / f"{filename}.md"
    prompt_text = PERSONALITY_PROMPTS.get(personality, "No prompt defined.")
    display_name = personality.replace("_", " ").title()

    callout_lines = "\n".join(
        f"> {line}" if line.strip() else ">" for line in prompt_text.strip().splitlines()
    )
    callout = f"> [!abstract] Bidding Strategy\n{callout_lines}"

    meta = {
        "personality": personality,
        "total_tournaments": 0,
        "wins": 0,
        "win_rate": 0.0,
        "tags": ["personality"],
    }
    body = (
        f"# {display_name}\n\n"
        f"## Philosophy\n\n{callout}\n\n"
        "## Performance\nNo data yet.\n\n"
        "## Recent Results\n\n"
        "## Evolution History\n"
    )
    _io._write_page(page_path, meta, body)


async def _update_personality_page(
    personality: str,
    job_slug: str,
    job_meta: dict,
    status: str,
) -> None:
    """Update or create a personality wiki page with outcome from a job."""
    filename = personality.replace("_", "-")
    page_path = _io.PERSONALITIES_DIR / f"{filename}.md"

    if not page_path.exists():
        _seed_personality_page(personality)

    meta, body = await asyncio.to_thread(_io.read_page, page_path)

    if status == "won":
        meta["wins"] = meta.get("wins", 0) + 1
    meta["total_tournaments"] = meta.get("total_tournaments", 0) + 1

    total = meta["total_tournaments"]
    meta["win_rate"] = round(meta.get("wins", 0) / total, 4) if total > 0 else 0.0

    updated_body = await _llm._synthesize(
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

    await asyncio.to_thread(_io._write_page, page_path, meta, updated_body)


async def update_material_page(
    item: str,
    unit: str,
    ai_unit_cost: float,
    verified_mid: float,
    deviation_pct: float,
    category: str = "general",
) -> None:
    """Create or update a material wiki page from PriceVerifier data."""
    try:
        filename = re.sub(r"[^a-zA-Z0-9\s-]", "", item.replace("_", "-"))
        filename = re.sub(r"[\s-]+", "-", filename).strip("-").lower()
        page_path = _io.MATERIALS_DIR / f"{filename}.md"

        today = date.today().isoformat()

        tags = ["material"]
        if deviation_pct >= 5:
            tags.append("price-flag")

        if page_path.exists():
            meta, body = await asyncio.to_thread(_io.read_page, page_path)
            meta["last_verified"] = today
            meta["verified_mid"] = verified_mid
            meta["deviation_pct"] = deviation_pct
            meta["tags"] = tags
        else:
            meta = {
                "material": item.lower(),
                "category": category,
                "last_verified": today,
                "seed_low": None,
                "seed_high": None,
                "verified_mid": verified_mid,
                "deviation_pct": deviation_pct,
                "tags": tags,
            }
            body = ""

        context_data = {
            "item": item,
            "unit": unit,
            "ai_unit_cost": ai_unit_cost,
            "verified_mid": verified_mid,
            "deviation_pct": deviation_pct,
        }
        updated_body = await _llm._synthesize(
            context=(
                f"Current page:\n{body}\n\n" f"New verification data:\n{json.dumps(context_data)}"
            ),
            instruction=(
                "Write or update this material page. Include:\n"
                "- ## Current Pricing — verified price, AI price, deviation\n"
                f"  {'Use a > [!warning] callout if deviation >= 10%, > [!caution] if 5-9%.' if deviation_pct >= 5 else 'No callout needed — deviation is within tolerance.'}\n"
                "- ## Deviation History — add this data point to the trend\n"
                "- ## Job Impact — note which jobs used this material (if known)\n"
                "Keep existing content and add the new data point."
            ),
        )

        await asyncio.to_thread(_io._write_page, page_path, meta, updated_body)
    except Exception:
        logger.exception("update_material_page: failed for item %s", item)
