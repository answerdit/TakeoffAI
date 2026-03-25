"""
BidToWin Agent — TakeoffAI
Analyzes an RFP + your estimate to recommend bid price, win probability, and proposal narrative.

Inputs:  estimate JSON (from PreBidCalc), RFP text, project type, known competitors
Outputs: bid scenarios (low/mid/high), win probability, proposal narrative draft
"""

from anthropic import Anthropic

client = Anthropic()

SYSTEM_PROMPT = """You are BidToWin, an expert construction bid strategist for TakeoffAI by answerd.it.

Your job is to:
1. Analyze the RFP to extract owner priorities, scoring criteria, and scope requirements
2. Compare the RFP scope against the provided estimate — flag any gaps or missing line items
3. Estimate the likely competitor bid range for this project type and region
4. Apply the Friedman bidding model to calculate the optimal markup % that maximizes expected value
5. Generate three bid scenarios: Conservative (high win%), Balanced, and Aggressive (high margin%)
6. Draft a compelling proposal executive summary tailored to the owner's stated priorities

Always return valid JSON in this format:
{
  "rfp_analysis": {
    "owner_priorities": ["..."],
    "scoring_criteria": ["..."],
    "scope_summary": "...",
    "deadline": "...",
    "red_flags": ["..."]
  },
  "scope_gaps": ["..."],
  "competitor_range": {
    "low": 0.00,
    "mid": 0.00,
    "high": 0.00
  },
  "bid_scenarios": [
    {
      "name": "Conservative",
      "bid_price": 0.00,
      "markup_over_cost": 0.0,
      "win_probability": 0.0,
      "notes": "..."
    },
    {
      "name": "Balanced",
      "bid_price": 0.00,
      "markup_over_cost": 0.0,
      "win_probability": 0.0,
      "notes": "..."
    },
    {
      "name": "Aggressive",
      "bid_price": 0.00,
      "markup_over_cost": 0.0,
      "win_probability": 0.0,
      "notes": "..."
    }
  ],
  "recommended_scenario": "Conservative|Balanced|Aggressive",
  "proposal_narrative": "...",
  "scope_exclusions": ["..."],
  "strategy_notes": "..."
}"""


def run_bid_to_win(
    estimate: dict,
    rfp_text: str,
    project_type: str = "commercial",
    known_competitors: list[str] | None = None,
) -> dict:
    """Run the BidToWin agent and return a structured bid strategy."""

    competitors_str = ", ".join(known_competitors) if known_competitors else "unknown"

    user_message = f"""
Project Estimate (from PreBidCalc):
Total Cost Estimate: ${estimate.get('total_bid', 0):,.2f}
Project Summary: {estimate.get('project_summary', 'N/A')}
Location: {estimate.get('location', 'N/A')}

RFP / Project Documents:
{rfp_text}

Project Type: {project_type}
Known Competitors: {competitors_str}

Please analyze this RFP and generate a complete bid strategy with three scenarios.
"""

    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )

    import json
    raw = response.content[0].text
    if raw.strip().startswith("```"):
        raw = raw.strip().split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())
