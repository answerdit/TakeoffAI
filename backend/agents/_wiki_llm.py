"""
Wiki LLM synthesis layer — TakeoffAI
Single _synthesize() entry point for all wiki LLM calls.
Model and system prompt are configured here; no other module calls the Anthropic API directly.
"""

import os
from typing import Optional

from anthropic import AsyncAnthropic

from backend.agents._wiki_io import SCHEMA_PATH
from backend.config import settings

WIKI_MODEL = os.getenv("WIKI_MODEL", settings.wiki_model)

_anthropic = AsyncAnthropic(api_key=settings.anthropic_api_key or None)

_SYSTEM_BASE = (
    "You are a knowledge base writer for TakeoffAI, a construction bidding system. "
    "Write clear, specific markdown for contractors reviewing their bidding history. "
    "Include exact dollar amounts, percentages, agent names, and dates. "
    "Use [[folder/page-slug]] wikilinks for cross-references. "
    "Use Obsidian callouts for flagged data: "
    "> [!warning] for price deviations ≥10%, > [!caution] for 5–9%, "
    "> [!danger] for underbid risk, > [!tip] for positive signals. "
    "Return ONLY the markdown body content — no frontmatter, no code fences."
)

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


async def _synthesize(context: str, instruction: str) -> str:
    """
    Single LLM call to generate wiki page content.
    Returns markdown string (body only, no frontmatter).
    """
    schema = _load_schema()
    if schema:
        system = [
            {"type": "text", "text": _SYSTEM_BASE},
            {"type": "text", "text": schema, "cache_control": {"type": "ephemeral"}},
        ]
    else:
        system = _SYSTEM_BASE

    response = await _anthropic.messages.create(
        model=WIKI_MODEL,
        max_tokens=2048,
        system=system,
        messages=[
            {
                "role": "user",
                "content": f"Context:\n{context}\n\nInstruction:\n{instruction}",
            }
        ],
    )
    return response.content[0].text.strip()
