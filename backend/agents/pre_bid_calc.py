"""
PreBidCalc Agent — TakeoffAI
Parses a project description and generates a line-item cost estimate.

Inputs:  project description (text), zip code, trade type, overhead %, margin %
Outputs: line-item estimate with materials, labor, burden, overhead, margin, total
"""

import csv
import json
from pathlib import Path

from anthropic import Anthropic

client = Anthropic()

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
6. Return a clean, line-item JSON estimate

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
  "confidence": "low|medium|high",
  "notes": "..."
}}"""


# ── Agent entry point ─────────────────────────────────────────────────────────

def _parse_response(raw: str) -> dict:
    """Strip markdown fences and parse JSON from an LLM response."""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    return json.loads(raw)


def run_prebid_calc(
    description: str,
    zip_code: str,
    trade_type: str = "general",
    overhead_pct: float = 20.0,
    margin_pct: float = 12.0,
) -> dict:
    """Run the PreBidCalc agent and return a structured estimate."""
    return run_prebid_calc_with_modifier(
        description, zip_code, trade_type, overhead_pct, margin_pct, system_prompt_modifier=None
    )


def run_prebid_calc_with_modifier(
    description: str,
    zip_code: str,
    trade_type: str = "general",
    overhead_pct: float = 20.0,
    margin_pct: float = 12.0,
    system_prompt_modifier: str | None = None,
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

    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=8192,
        system=system,
        messages=[{"role": "user", "content": user_message}],
    )

    return _parse_response(response.content[0].text)
