"""
PreBidCalc Agent — TakeoffAI
Parses a project description and generates a line-item cost estimate.

Inputs:  project description (text), zip code, trade type, overhead %, margin %
Outputs: line-item estimate with materials, labor, burden, overhead, margin, total
"""

import asyncio
import base64
import csv
from pathlib import Path

from anthropic import AsyncAnthropic

from backend.agents.utils import call_with_json_retry, parse_llm_json
from backend.config import settings

client = AsyncAnthropic(api_key=settings.anthropic_api_key or None)

# ── Load seed material costs with mtime-based cache ──────────────────────────
# Reloads only when the CSV file changes on disk (picked up from PriceVerifier
# updates). Avoids blocking file I/O on every async call / tournament fanout.
# The formatted table string is also cached under the same mtime key so
# tournament fan-out (30 concurrent calls) pays zero I/O and zero string
# rebuilds on cache hits.

_CSV_PATH = Path(__file__).parent.parent / "data" / "material_costs.csv"
_costs_cache: list[dict] = []
_costs_mtime: float = 0.0
_costs_table_str: str = ""
_costs_refresh_lock = asyncio.Lock()


def _sync_load() -> list[dict]:
    """Stat + file read. Must only be called via asyncio.to_thread — never directly
    on the event loop — so neither the stat syscall nor the file read block async I/O.
    Fast path (cache hit): returns immediately after one stat call.
    Slow path (CSV changed): re-reads the file and invalidates the formatted-string cache.
    """
    global _costs_cache, _costs_mtime, _costs_table_str
    if not _CSV_PATH.exists():
        return []
    try:
        mtime = _CSV_PATH.stat().st_mtime
    except OSError:
        return _costs_cache
    if mtime == _costs_mtime and _costs_cache:
        return _costs_cache
    with open(_CSV_PATH, newline="", encoding="utf-8") as f:
        _costs_cache = list(csv.DictReader(f))
    _costs_mtime = mtime
    _costs_table_str = ""  # invalidate formatted-string cache
    return _costs_cache


async def _ensure_costs_fresh() -> None:
    """Refresh the material cost cache off the event loop.

    Cold start (cache empty): serialises behind a lock so only one coroutine
    does the file read; the rest re-check on release and return early.

    Warm path (cache populated): always delegates to _sync_load() via
    asyncio.to_thread() so the mtime check runs off the event loop.  _sync_load
    returns immediately on a cache hit (one stat syscall, no file read), and
    reloads automatically when material_costs.csv changes — e.g. after a
    nightly PriceVerifier update.  Skipping this call on the warm path would
    cause stale pricing to be served indefinitely after the first load.
    """
    if not _costs_cache:
        # Cold start: serialise to prevent thundering herd.
        async with _costs_refresh_lock:
            if not _costs_cache:  # re-check: another coroutine may have loaded while we waited
                await asyncio.to_thread(_sync_load)
        return
    # Warm path: mtime check off-loop — fast no-op on hit, reload on CSV change.
    await asyncio.to_thread(_sync_load)


def _format_cost_table() -> str:
    """Return the cached markdown table.  Always call _ensure_costs_fresh() first
    from an async context so the cache is guaranteed warm before this runs.
    """
    global _costs_table_str
    if _costs_table_str:
        return _costs_table_str
    # Fallback for sync callers (e.g. tests calling _build_system_prompt directly).
    costs = _sync_load()
    if not costs:
        return "(no seed data available)"
    lines = [
        "| Item | Unit | Low $/unit | High $/unit | Trade |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in costs:
        lines.append(
            f"| {row['item']} | {row['unit']} | ${row['low_cost']} | ${row['high_cost']} | {row['trade_category']} |"
        )
    _costs_table_str = "\n".join(lines)
    return _costs_table_str


# ── System prompt ─────────────────────────────────────────────────────────────

_SYSTEM_PROMPT_TEMPLATE = """You are PreBidCalc, an expert construction cost estimator for TakeoffAI by answerd.it.

Your job is to:
1. Parse the project description and extract measurable quantities (sqft, LF, units, etc.)
2. Use the reference unit costs below as anchors — interpolate within the low/high range based on project quality and region
3. Apply productivity factors (hrs/unit) and labor burden multipliers (typically 1.35–1.55x base wage)
4. Apply a regional cost index (CCI) adjustment for the given zip code (use RSMeans regional data as a mental model)
5. Apply overhead % and target margin % to arrive at a total bid number
6. Return estimate_low as the realistic low end of the total project cost (typically 5–15% below total_bid based on low-end seed costs and favorable scope assumptions) and estimate_high as the realistic high end (typically 5–15% above total_bid based on high-end seed costs and scope risk). Both must be present in every response. estimate_low must be strictly less than total_bid, and total_bid must be strictly less than estimate_high.
7. Return a clean, line-item JSON estimate

## Reference Material Unit Costs (seed data)

{cost_table}

For items not in the table, use your expert knowledge of current market rates.

Always return valid JSON in this exact format — no markdown fences, no extra text:
{{
  "project_summary": "...",
  "location": "...",
  "line_items": [
    {{
      "description": "...",
      "quantity": 0,
      "unit": "sqft|LF|EA|LS|CY|SQ|GAL",
      "unit_material_cost": 0.00,
      "unit_labor_cost": 0.00,
      "total_material": 0.00,
      "total_labor": 0.00,
      "subtotal": 0.00
    }}
  ],
  "subtotal": 0.00,
  "overhead_pct": 0,
  "overhead_amount": 0.00,
  "margin_pct": 0,
  "margin_amount": 0.00,
  "total_bid": 0.00,
  "estimate_low": 0.00,
  "estimate_high": 0.00,
  "confidence": "low|medium|high",
  "notes": "..."
}}"""


def _build_system_prompt() -> str:
    """Build system prompt with current material costs (reloaded from CSV)."""
    return _SYSTEM_PROMPT_TEMPLATE.format(cost_table=_format_cost_table())


# ── Blueprint extraction prompts ──────────────────────────────────────────────

BLUEPRINT_EXTRACTION_SYSTEM = (
    "You are a construction takeoff assistant for TakeoffAI by answerd.it. "
    "Your job is to read construction plans and extract a plain-English project description "
    "that a cost estimator can use to generate a line-item bid estimate.\n\n"
    "Write in the voice of an experienced contractor describing the job to their estimator. "
    "Be specific and quantitative. Lead with the single most important number "
    "(total sqft, CY, LF, or units — whichever is primary for this trade). "
    "Do not include contract terms, owner names, bid dates, or submission requirements. "
    "Do not use adjectives without numbers behind them."
)

# .format(zip_note=..., trade_type=...) at call time
BLUEPRINT_EXTRACTION_PROMPT = (
    "Review these construction plans and extract a project description "
    "for a {trade_type} estimate. {zip_note}\n\n"
    "Structure your response in this order:\n"
    "1. Total size (sqft, CY, LF, or units — whichever is primary for this trade)\n"
    "2. Building type and occupancy\n"
    "3. Structural system and key materials called out in the plans\n"
    "4. Scope of work — what is being built or installed\n"
    "5. Room counts or system counts (fixtures, panels, openings, etc.)\n"
    "6. Site conditions or access constraints visible in the plans\n"
    "7. Explicit exclusions noted in the plans or specs\n\n"
    "Write as a single paragraph or short bulleted list. Keep it under 200 words. "
    "If a dimension or quantity is not clearly shown in the plans, "
    "omit it rather than guess."
)


async def preprocess_blueprint(
    pdf_bytes: bytes,
    zip_code: str,
    trade_type: str = "general",
) -> str:
    """
    Send a blueprint PDF to Claude and extract an estimate-ready project description.
    Returns plain text draft suitable for the Pre-Bid Estimate description field.
    """
    from backend.config import settings

    if not pdf_bytes:
        raise ValueError("pdf_bytes must not be empty")

    encoded = base64.standard_b64encode(pdf_bytes).decode("utf-8")

    response = await client.messages.create(
        model=settings.claude_model,
        max_tokens=1024,  # prompt caps output at ~200 words; 1024 is sufficient
        system=BLUEPRINT_EXTRACTION_SYSTEM,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": encoded,
                        },
                    },
                    {
                        "type": "text",
                        "text": BLUEPRINT_EXTRACTION_PROMPT.format(
                            zip_note=(
                                f"The project is located in zip code {zip_code}."
                                if zip_code
                                else "No zip code provided — omit location-specific cost adjustments."
                            ),
                            trade_type=trade_type,
                        ),
                    },
                ],
            }
        ],
    )

    if not response.content:
        raise ValueError("Empty response from Claude on blueprint extraction")
    return response.content[0].text.strip()


# ── Agent entry point ─────────────────────────────────────────────────────────


async def run_prebid_calc(
    description: str,
    zip_code: str,
    trade_type: str = "general",
    overhead_pct: float = 20.0,
    margin_pct: float = 12.0,
) -> dict:
    """Run the PreBidCalc agent and return a structured estimate."""
    return await run_prebid_calc_with_modifier(
        description, zip_code, trade_type, overhead_pct, margin_pct, system_prompt_modifier=None
    )


async def run_prebid_calc_with_modifier(
    description: str,
    zip_code: str,
    trade_type: str = "general",
    overhead_pct: float = 20.0,
    margin_pct: float = 12.0,
    system_prompt_modifier: str | None = None,
    historical_comparables: str | None = None,
    temperature: float = 0.7,
) -> dict:
    """
    Run PreBidCalc with an optional personality modifier appended to the system prompt.
    Used by the tournament engine to inject bidding-style instructions per agent.
    """
    await _ensure_costs_fresh()
    system = _build_system_prompt()
    if system_prompt_modifier:
        system = system + f"\n\n---\n\n{system_prompt_modifier}"
    if historical_comparables:
        system = system + f"\n\n---\n\n{historical_comparables}"

    user_message = f"""Project Description: {description}
Zip Code: {zip_code}
Trade Type: {trade_type}
Overhead %: {overhead_pct}
Target Margin %: {margin_pct}

Please generate a detailed line-item cost estimate for this project."""

    from backend.config import settings

    return await call_with_json_retry(
        client,
        model=settings.claude_model,
        max_tokens=8192,
        system=[
            {
                "type": "text",
                "text": system,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user_message}],
        temperature=temperature,
    )
