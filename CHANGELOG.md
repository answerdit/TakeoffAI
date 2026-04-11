# Changelog

## 2026-04-11 — Wiki capture pipeline (close the retrieval loop)

The historical retrieval path was previously half-built: `historical_retrieval.py`
existed and `tournament.py` already imported `get_comparable_jobs` to inject
comparables into personality prompts, but `wiki/jobs/` was empty because
nothing ever wrote closed job pages automatically. This release wires the
write side end-to-end, so the retrieval corpus fills itself whenever a
tournament is judged in HISTORICAL mode.

**Schema.** Migration 5 adds `wiki_job_slug TEXT` to `bid_tournaments`,
linking each tournament row to its wiki job page.

**Auto-stub on tournament run.** `create_job_stub()` (new, no-LLM) writes a
skeleton wiki job page at `wiki/jobs/<slug>.md` every time
`POST /api/tournament/run` fires without a pre-existing `job_slug`. The
stub shares the exact frontmatter shape as `create_job()` so every
downstream reader works identically against stub-seeded pages. Slugs are
collision-safe via numeric suffix bumping. The `_wiki_capture_tournament`
helper in `routes.py` stubs the page, persists the slug onto the
tournament row, and then calls `enrich_tournament` to append the
tournament narrative section.

**Cascade on judge.** `judge_tournament` now reads `wiki_job_slug` from the
tournament row. When mode is HISTORICAL and a slug is present, it fires
`cascade_outcome(status="closed", actual_cost=actual_winning_bid)` as a
background task. HUMAN and AUTO modes intentionally don't cascade — neither
carries a realized-cost signal. The judge response dict now includes
`wiki_job_slug` so downstream callers can see the linkage.

**Test isolation fixes.** Two pre-existing tournament tests were leaking
real files into the vault because they mocked `run_tournament` but didn't
patch `JOBS_DIR` — a regression exposed by the new unconditional capture.
`test_rate_limiter_returns_429_after_burst` also had a latent bug where it
made real Anthropic calls on every burst iteration (this was the cause of
suite hangs observed this week). Both fixed.

**Retrieval coverage.** Seven new unit tests pin the score formula
(`10.0 + 5.0 + 2.0 + 3×jaccard`), all three resolved statuses, zip3 prefix
matching, numeric-zip frontmatter handling, limit enforcement, empty-zip
graceful skip, and `.gitkeep` ignore.

**Verified end-to-end** on 2026-04-11 with tournament 401 (kitchen remodel,
Austin 78701): stub auto-created → tournament enriched → judged at
`$20,500` → cascade closed page with `actual_cost=20500.0` → retrieval
returned the job on the next query with similarity score 17.04. The first
real member of the self-fed corpus is now in the vault.

## 2026-04-10 — Tournament accuracy hybrid rollout

Consensus entries now carry historical accuracy metadata, and the frontend
surfaces it. Consensus ordering can optionally be re-ranked by that accuracy
behind a feature flag.

**Phase 1 — annotate only.** Every consensus entry returned from
`POST /api/tournament/run` now includes `avg_deviation_pct`, `closed_job_count`,
and `is_accuracy_flagged`, sourced from the client profile's calibration
history. The response also includes a top-level `accuracy_recommended_agent`
(lowest-deviation non-flagged agent, or `null` if the client has no data).
Default consensus order is unchanged.

**Phase 2 — feature-flagged re-ranking.** When
`TOURNAMENT_ACCURACY_RERANK_ENABLED=true` *and* the recommended agent has
closed at least `TOURNAMENT_ACCURACY_RERANK_MIN_JOBS` jobs (default 5),
consensus entries are re-ordered: non-flagged agents with data (ascending by
deviation) → agents with no data → red-flagged agents. The response carries
`rerank_active: true` so callers can distinguish "sorted by accuracy" from
"default order".

**Frontend.** The tournament results panel renders a "Sorted by accuracy" pill
when `rerank_active` is true, a per-card accuracy meta line
(`±X.X% · N jobs`), and a red-tinted FLAGGED state for agents whose deviation
crossed the red-flag threshold. Clients with no annotation data see the exact
same cards as before — zero regression surface.

**Verified end-to-end** on 2026-04-10 against a seeded client profile with
real Anthropic API calls: recommended agent was correctly identified, rerank
gate fired, and the cheapest-but-flagged agent was correctly demoted to the
bottom of the consensus order despite producing the lowest raw bid.

Relevant commits: `4eced5a`, `8796e92`, `1c052e8`, `834c7a6`.
