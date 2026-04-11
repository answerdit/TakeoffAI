"""
Wiki Manager — TakeoffAI
LLM-maintained Obsidian knowledge base for job tracking and institutional memory.

All wiki I/O goes through this module. No other code writes to the wiki/ directory.

Internal structure:
  _wiki_io       — path constants, frontmatter parsing, file writes, slug helpers
  _wiki_llm      — Anthropic client, _synthesize(), schema loading
  _wiki_jobs     — job page lifecycle (create, enrich, cascade)
  _wiki_entities — client, personality, and material page writers
  _wiki_lint     — vault health checks
"""

# ── Re-export public surface ─────────────────────────────────────────────────
# All callers (wiki_routes.py, routes.py) import from here — sub-modules are private.

from backend.agents._wiki_entities import _seed_personality_page, update_material_page
from backend.agents._wiki_io import (
    CLIENTS_DIR,
    JOBS_DIR,
    MATERIALS_DIR,
    PERSONALITIES_DIR,
    WIKI_DIR,
    _make_job_slug,
    _parse_frontmatter,
    _write_page,
    read_page,
)
from backend.agents._wiki_jobs import (
    cascade_outcome,
    create_job,
    enrich_estimate,
    enrich_scope_from_blueprint,
    enrich_tournament,
    record_bid_decision,
)
from backend.agents._wiki_lint import lint
from backend.agents._wiki_llm import _synthesize

__all__ = [
    # Public API
    "JOBS_DIR",
    "read_page",
    "create_job",
    "enrich_estimate",
    "enrich_scope_from_blueprint",
    "enrich_tournament",
    "record_bid_decision",
    "cascade_outcome",
    "update_material_page",
    "lint",
    # Private symbols re-exported for tests
    "WIKI_DIR",
    "CLIENTS_DIR",
    "MATERIALS_DIR",
    "PERSONALITIES_DIR",
    "_parse_frontmatter",
    "_write_page",
    "_make_job_slug",
    "_synthesize",
    "_seed_personality_page",
]
