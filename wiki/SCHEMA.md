# TakeoffAI Wiki — Schema & Conventions

This document defines the structure and rules for the TakeoffAI knowledge base.
It is injected into the LLM system prompt for all wiki page synthesis.

---

## Page Types

### Job (`wiki/jobs/`)
Tracks a single project through the bid pipeline.

**Required frontmatter:** status, client, date, trade, zip
**Optional frontmatter:** our_bid, estimate_total, estimate_low, estimate_high, tournament_id, winner_personality, band_low, band_high, actual_cost, outcome_date

**Section order:** Scope, Estimate, Tournament, Bid Decision, Outcome, Price Flags, Links

**Status values:** prospect, estimated, tournament-complete, bid-submitted, won, lost, closed

### Client (`wiki/clients/`)
Narrative profile for a contractor client.

**Required frontmatter:** client_id, first_job, total_jobs, wins, losses
**Optional frontmatter:** company, region

**Section order:** Profile, Win/Loss Summary, ELO Standings, Recent Jobs, Patterns

### Personality (`wiki/personalities/`)
Performance history for a bidding personality.

**Required frontmatter:** personality, total_tournaments, wins, win_rate
**Optional frontmatter:** current_prompt_hash, last_evolution

**Section order:** Philosophy, Performance, Recent Results, Evolution History

### Material (`wiki/materials/`)
Price tracking for flagged construction materials.

**Required frontmatter:** material, category, last_verified
**Optional frontmatter:** seed_low, seed_high, verified_mid, deviation_pct

**Section order:** Current Pricing, Deviation History, Job Impact

---

## Tags

Each page type should include `tags` in its frontmatter:

| Page type | Required tags | Optional tags |
|---|---|---|
| Job | `job`, `{status}` | `{trade_type}` |
| Client | `client` | — |
| Personality | `personality` | `red-flag` (if win_rate < 0.2) |
| Material | `material` | `price-flag` (if deviation_pct > 5) |

Example job frontmatter:
```yaml
tags:
  - job
  - estimated
  - electrical
```

---

## Callouts

Use Obsidian callouts for at-a-glance scanning in reading view.

### Price flag callouts (use in material and job pages)
```markdown
> [!warning] Price Deviation: +18%
> Verified mid-market price is $4.20/LF vs. AI seed of $3.55/LF. Flag for review.
```
Use `[!warning]` for deviations ≥ 10%, `[!caution]` for 5–9%.

### Personality strategy (use in Philosophy section)
```markdown
> [!abstract] Bidding Strategy
> Strategy description and bullet rules here.
```

### Bid risk notes (use in Bid Decision and Outcome sections)
```markdown
> [!tip] Risk Assessment
> Band spread is tight — high confidence in this number.

> [!danger] Underbid Risk
> Our bid of $X is below the band_low of $Y — investigate before submitting.
```

---

## Dashboard Note

`DASHBOARD.md` uses `dataview` query blocks. These require the **Dataview** community plugin in Obsidian. Without it, the queries render as raw code blocks.

---

## Rules

1. **Frontmatter is structured data.** All monetary values are raw numbers (no `$` prefix). Dates use ISO 8601 format (YYYY-MM-DD or full datetime).
2. **Links use folder-prefixed wikilinks.** Write `[[clients/acme-construction]]` not `[[acme-construction]]`.
3. **Job slugs are kebab-case.** Format: `YYYY-MM-DD-{client}-{short-description}`. Example: `2026-04-06-acme-parking-garage`.
4. **Section ordering is fixed.** Scope always first, Links always last, chronological sections in between.
5. **Body text is narrative.** Write for a contractor reviewing their bidding history. Be specific about dollar amounts, percentages, agent names, and dates. Avoid generic language.
6. **Cross-reference liberally.** Link to related job, client, personality, and material pages wherever relevant.
7. **Use callouts for flagged data.** Price deviations, bid risks, and underbid warnings must use the callout patterns above — not plain prose.
