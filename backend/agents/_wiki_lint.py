"""
Wiki lint — TakeoffAI
Health checks for the wiki vault: broken links, orphans, stale jobs, frontmatter validation.
Does NOT auto-fix anything.
"""

import re
from datetime import date

from backend.agents import _wiki_io as _io

_REQUIRED_FRONTMATTER = {
    "jobs": ["status", "client"],
    "clients": ["client_id"],
    "personalities": ["personality"],
    "materials": ["material"],
}

_STALE_STATUSES = {"estimated", "tournament-complete"}
_STALE_DAYS = 30


def lint() -> dict:
    """
    Run wiki health checks. Returns structured report dict.
    Checks: broken links, orphan pages, stale jobs, frontmatter validation.
    """
    all_pages: dict[str, object] = {}
    all_links: list[tuple[str, str]] = []
    inbound: set[str] = set()

    broken_links = []
    orphan_pages = []
    stale_jobs = []
    frontmatter_errors = []

    # Root-level files (SCHEMA.md, DASHBOARD.md) are excluded — they are
    # conventions docs, not job/client/personality/material pages.
    for subdir in [_io.JOBS_DIR, _io.CLIENTS_DIR, _io.MATERIALS_DIR, _io.PERSONALITIES_DIR]:
        if not subdir.exists():
            continue
        for p in subdir.glob("*.md"):
            rel = f"{subdir.name}/{p.stem}"
            all_pages[rel] = p

    for rel, path in all_pages.items():
        meta, body = _io._parse_frontmatter(path)
        page_type = path.parent.name

        required = _REQUIRED_FRONTMATTER.get(page_type, [])
        for field in required:
            if field not in meta:
                frontmatter_errors.append(
                    {
                        "page": rel,
                        "error": f"missing required field: {field}",
                    }
                )

        if page_type == "jobs":
            valid_statuses = {
                "prospect",
                "estimated",
                "tournament-complete",
                "bid-submitted",
                "won",
                "lost",
                "closed",
            }
            if meta.get("status") and meta["status"] not in valid_statuses:
                frontmatter_errors.append(
                    {
                        "page": rel,
                        "error": f"invalid status: {meta['status']}",
                    }
                )

            if meta.get("status") in _STALE_STATUSES and meta.get("date"):
                try:
                    job_date = date.fromisoformat(str(meta["date"]))
                    days = (date.today() - job_date).days
                    if days > _STALE_DAYS:
                        stale_jobs.append(
                            {
                                "slug": path.stem,
                                "status": meta["status"],
                                "days_stale": days,
                            }
                        )
                except (ValueError, TypeError):
                    pass

        for match in re.finditer(r"\[\[([^\]]+)\]\]", body):
            link_target = match.group(1).split("|")[0].strip()
            all_links.append((rel, link_target))
            inbound.add(link_target)

    for source, target in all_links:
        if target not in all_pages:
            broken_links.append({"page": source, "link": target})

    for rel in all_pages:
        if rel not in inbound:
            orphan_pages.append(rel)

    return {
        "orphan_pages": orphan_pages,
        "broken_links": broken_links,
        "stale_jobs": stale_jobs,
        "frontmatter_errors": frontmatter_errors,
        "contradictions": [],
    }
