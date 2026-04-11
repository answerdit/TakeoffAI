"""
Historical bid retrieval — TakeoffAI
Deterministic, synchronous lookup of past comparable jobs from wiki/jobs/.
No LLM calls. Empty vault → returns [] without error.

Tenancy model: TakeoffAI is single-tenant by deployment — one contractor per
install. `client_id` here is the *contractor's customer* (homeowner, GC,
property manager), not a multi-tenant boundary. Cross-customer retrieval is
intentional: pricing a kitchen remodel for customer A should learn from
kitchen remodels the same contractor did for customers B, C, and D. The
10.0 same-client bonus is recency/relationship weighting, not an isolation
gate. If TakeoffAI ever goes multi-tenant (hosted app.takeoffai.ai), this
module must grow a hard filter at the contractor level, not the client
level.
"""

import logging
from pathlib import Path

from backend.agents import _wiki_io as _io

logger = logging.getLogger(__name__)

_RESOLVED_STATUSES = {"won", "lost", "closed"}
_STOPWORDS = {
    "a",
    "an",
    "the",
    "and",
    "or",
    "but",
    "in",
    "on",
    "at",
    "to",
    "for",
    "of",
    "with",
    "is",
    "are",
    "was",
    "were",
    "be",
    "been",
    "this",
    "that",
    "it",
    "its",
    "as",
    "by",
    "from",
    "up",
    "about",
    "into",
    "through",
}


def _tokenize(text: str) -> set[str]:
    words = text.lower().split()
    return {w.strip(".,;:!?()[]\"'") for w in words if w not in _STOPWORDS and len(w) > 1}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def get_comparable_jobs(
    client_id: str,
    trade_type: str,
    description: str,
    zip_code: str = "",
    limit: int = 5,
) -> list[dict]:
    """
    Return up to `limit` past jobs most similar to the current one.
    Reads wiki/jobs/*.md, parses frontmatter, filters by status in
    ('won', 'lost', 'closed'), ranks by similarity, returns list of dicts
    with keys: project_name, date, trade, zip, our_bid, estimate_total,
    actual_cost, outcome (= status), similarity_score.
    Empty vault or no matches → returns [].
    """
    jobs_dir: Path = _io.JOBS_DIR
    if not jobs_dir.exists():
        return []

    md_files = list(jobs_dir.glob("*.md"))
    if not md_files:
        return []

    desc_tokens = _tokenize(description)
    query_zip3 = zip_code[:3] if zip_code else ""

    candidates: list[dict] = []

    for path in md_files:
        if path.name == ".gitkeep":
            continue
        try:
            meta, body = _io._parse_frontmatter(path)
        except Exception:
            logger.warning("historical_retrieval: failed to read %s, skipping", path.name)
            continue

        if not isinstance(meta, dict):
            logger.warning("historical_retrieval: bad frontmatter in %s, skipping", path.name)
            continue

        status = meta.get("status", "")
        if status not in _RESOLVED_STATUSES:
            continue

        job_trade = meta.get("trade", "")
        if job_trade != trade_type:
            continue

        score = 0.0
        if meta.get("client") == client_id:
            score += 10.0
        score += 5.0  # trade already matched (required above)

        job_zip = str(meta.get("zip", ""))
        if query_zip3 and job_zip and job_zip[:3] == query_zip3:
            score += 2.0

        body_tokens = _tokenize(body)
        jaccard = _jaccard(desc_tokens, body_tokens)
        score += jaccard * 3.0

        candidates.append(
            {
                "project_name": path.stem,
                "date": meta.get("date"),
                "trade": job_trade,
                "zip": meta.get("zip"),
                "our_bid": meta.get("our_bid"),
                "estimate_total": meta.get("estimate_total"),
                "actual_cost": meta.get("actual_cost"),
                "outcome": status,
                "similarity_score": round(score, 4),
            }
        )

    candidates.sort(key=lambda x: x["similarity_score"], reverse=True)
    return candidates[:limit]


def format_comparables_for_prompt(jobs: list[dict]) -> str:
    """
    Format a list of comparable jobs as a markdown block for injection into a
    system prompt. Empty list → returns "".
    """
    if not jobs:
        return ""

    lines = ["## Historical Comparables (your realized numbers on similar jobs)"]
    for i, job in enumerate(jobs, start=1):
        name = job.get("project_name") or "Unknown project"
        date = job.get("date") or ""
        trade = job.get("trade") or ""
        zip_code = job.get("zip") or ""
        our_bid = job.get("our_bid")
        actual_cost = job.get("actual_cost")
        outcome = job.get("outcome") or ""

        header_parts = [f"**{name}**"]
        if date:
            header_parts[0] = f"**{name}** ({date})"

        detail_parts = []
        if trade:
            detail_parts.append(f"trade: {trade}")
        if zip_code:
            detail_parts.append(f"zip: {zip_code}")

        line = f"{i}. {header_parts[0]}"
        if detail_parts:
            line += " — " + ", ".join(detail_parts)

        bid_parts = []
        if our_bid is not None:
            bid_parts.append(f"Our bid: ${float(our_bid):,.0f}")
        if outcome:
            bid_parts.append(f"Outcome: {outcome}")
        if actual_cost is not None:
            bid_parts.append(f"Actual cost: ${float(actual_cost):,.0f}")
            if our_bid is not None and float(our_bid) > 0:
                margin = (float(our_bid) - float(actual_cost)) / float(our_bid) * 100
                sign = "+" if margin >= 0 else ""
                bid_parts.append(f"Realized margin: {sign}{margin:.1f}%")

        if bid_parts:
            line += "\n   " + " → ".join(bid_parts)

        lines.append(line)

    return "\n".join(lines)
