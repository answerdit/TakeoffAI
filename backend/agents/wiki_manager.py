"""
Wiki Manager — TakeoffAI
LLM-maintained Obsidian knowledge base for job tracking and institutional memory.

All wiki I/O goes through this module. No other code writes to the wiki/ directory.
"""

import asyncio
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
