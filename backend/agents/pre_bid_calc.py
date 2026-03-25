"""
PreBidCalc Agent — TakeoffAI
Parses a project description and generates a line-item cost estimate.

Inputs:  project description (text), zip code, trade type, overhead %, margin %
Outputs: line-item estimate with materials, labor, burden, overhead, margin, total
"""

from anthropic import Anthropic

client = Anthropic()

SYSTEM_PROMPT = """You are PreBidCalc, an expert construction cost estimator for TakeoffAI by answerd.it.

Your job is to:
1. Parse the project description and extract measurable quantities (sqft, LF, units, etc.)
2. Look up realistic unit costs for materials and labor by trade and region
3. Apply productivity factors (hrs/unit) and labor burden multipliers
4. Apply a regional cost index (CCI) adjustment for the given zip code
5. Apply overhead % and target margin % to arrive at a total bid number
6. Return a clean, line-item JSON estimate

Always return valid JSON in this format:
{
  "project_summary": "...",
  "location": "...",
  "line_items": [
    {
      "description": "...",
      "quantity": 0,
      "unit": "sqft|LF|EA|LS",
      "unit_material_cost": 0.00,
      "unit_labor_cost": 0.00,
      "total_material": 0.00,
      "total_labor": 0.00,
      "subtotal": 0.00
    }
  ],
  "subtotal": 0.00,
  "overhead_pct": 0,
  "overhead_amount": 0.00,
  "margin_pct": 0,
  "margin_amount": 0.00,
  "total_bid": 0.00,
  "confidence": "low|medium|high",
  "notes": "..."
}"""


def run_prebid_calc(
    description: str,
    zip_code: str,
    trade_type: str = "general",
    overhead_pct: float = 20.0,
    margin_pct: float = 12.0,
) -> dict:
    """Run the PreBidCalc agent and return a structured estimate."""

    user_message = f"""
Project Description: {description}
Zip Code: {zip_code}
Trade Type: {trade_type}
Overhead %: {overhead_pct}
Target Margin %: {margin_pct}

Please generate a detailed line-item cost estimate for this project.
"""

    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )

    import json
    raw = response.content[0].text
    # Strip markdown fences if present
    if raw.strip().startswith("```"):
        raw = raw.strip().split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())
