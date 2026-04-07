"""
PreBidCalc Agent — TakeoffAI
Parses a project description and generates a line-item cost estimate.

Inputs:  project description (text), zip code, trade type, overhead %, margin %
Outputs: line-item estimate with materials, labor, burden, overhead, margin, total
"""

import base64
import csv
from pathlib import Path

from anthropic import AsyncAnthropic

from backend.agents.utils import call_with_json_retry, parse_llm_json

client = AsyncAnthropic()

# ── Load seed material costs once at import time ─────────────────────────────

_CSV_PATH = Path(__file__).parent.parent / "data" / "material_costs.csv"


def _load_material_costs() -> list[dict]:
    if not _CSV_PATH.exists():
        return []
    with open(_CSV_PATH, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


_MATERIAL_COSTS: list[dict] = _load_material_costs()


def _format_cost_table() -> str:
    """Format the seed CSV as a readable table for the system prompt."""
    if not _MATERIAL_COSTS:
        return "(no seed data available)"
    lines = ["| Item | Unit | Low $/unit | High $/unit | Trade |", "| --- | --- | --- | --- | --- |"]
    for row in _MATERIAL_COSTS:
        lines.append(
            f"| {row['item']} | {row['unit']} | ${row['low_cost']} | ${row['high_cost']} | {row['trade_category']} |"
        )
    return "\n".join(lines)


# ── System prompt ─────────────────────────────────────────────────────────────

SYSTEM_PROMPT = f"""You are PreBidCalc, an expert construction cost estimator for TakeoffAI by answerd.it.

Your job is to:
1. Parse the project description and extract measurable quantities (sqft, LF, units, etc.)
2. Use the reference unit costs below as anchors — interpolate within the low/high range based on project quality and region
3. Apply productivity factors (hrs/unit) and labor burden multipliers (typically 1.35–1.55x base wage)
4. Apply a regional cost index (CCI) adjustment for the given zip code (use RSMeans regional data as a mental model)
5. Apply overhead % and target margin % to arrive at a total bid number
6. Return estimate_low as the realistic low end of the total project cost (typically 5–15% below total_bid based on low-end seed costs and favorable scope assumptions) and estimate_high as the realistic high end (typically 5–15% above total_bid based on high-end seed costs and scope risk). Both must be present in every response. estimate_low must be strictly less than total_bid, and total_bid must be strictly less than estimate_high.
7. Return a clean, line-item JSON estimate

## Reference Material Unit Costs (seed data)

{_format_cost_table()}

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

# .format(zip_code=..., trade_type=...) at call time
BLUEPRINT_EXTRACTION_PROMPT = (
    "Review these construction plans and extract a project description "
    "for a {trade_type} estimate. The project is located in zip code {zip_code}.\n\n"
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
                            zip_code=zip_code,
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
    temperature: float = 0.7,
) -> dict:
    """
    Run PreBidCalc with an optional personality modifier appended to the system prompt.
    Used by the tournament engine to inject bidding-style instructions per agent.
    """
    system = SYSTEM_PROMPT
    if system_prompt_modifier:
        system = system + f"\n\n---\n\n{system_prompt_modifier}"

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
        system=system,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": system,
                        "cache_control": {"type": "ephemeral"},
                    },
                    {
                        "type": "text",
                        "text": user_message,
                    },
                ],
            }
        ],
        temperature=temperature,
    )
