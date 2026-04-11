"""
Wiki I/O primitives — TakeoffAI
Path constants, frontmatter parsing, page writing, and slug helpers.
All wiki file I/O is routed through this module.
"""

import logging
import re
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

WIKI_DIR = Path(__file__).parent.parent.parent / "wiki"
JOBS_DIR = WIKI_DIR / "jobs"
CLIENTS_DIR = WIKI_DIR / "clients"
MATERIALS_DIR = WIKI_DIR / "materials"
PERSONALITIES_DIR = WIKI_DIR / "personalities"
SCHEMA_PATH = WIKI_DIR / "SCHEMA.md"


def _safe_job_path(job_slug: str) -> Path | None:
    """Return the resolved job page path, or None if slug is unsafe."""
    page_path = (JOBS_DIR / f"{job_slug}.md").resolve()
    if not str(page_path).startswith(str(JOBS_DIR.resolve())):
        logger.warning("Rejected unsafe job_slug: %s", job_slug)
        return None
    return page_path


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


def read_page(path: Path) -> tuple[dict, str]:
    """Alias for _parse_frontmatter — reads a wiki page into (meta, body)."""
    return _parse_frontmatter(path)


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


def _make_job_slug(client_id: str, project_name: str, date_str: str) -> str:
    """
    Generate a kebab-case job slug: YYYY-MM-DD-{client}-{short-description}.
    Strips special characters, collapses whitespace, converts underscores to dashes.
    """
    raw = f"{date_str}-{client_id}-{project_name}"
    raw = raw.replace("_", "-")
    cleaned = re.sub(r"[^a-zA-Z0-9\s-]", "", raw)
    slug = re.sub(r"[\s-]+", "-", cleaned).strip("-").lower()
    return slug
