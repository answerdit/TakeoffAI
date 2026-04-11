# Changelog

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
