# TakeoffAI — Dashboard

## Active Jobs

```dataview
TABLE status, client, trade, our_bid AS "Our Bid", date
FROM "jobs"
WHERE !contains(list("won", "lost", "closed"), status)
SORT date DESC
```

## Recent Outcomes

```dataview
TABLE status, client, trade, our_bid AS "Our Bid", actual_cost AS "Actual Cost", outcome_date AS "Closed"
FROM "jobs"
WHERE contains(list("won", "lost", "closed"), status)
SORT outcome_date DESC
LIMIT 20
```

## Price Flags

```dataview
TABLE material, category, verified_mid AS "Mid Price", deviation_pct AS "Deviation %", last_verified AS "Last Verified"
FROM "materials"
WHERE deviation_pct > 5
SORT deviation_pct DESC
```

## Personality Standings

```dataview
TABLE personality, total_tournaments AS "Tournaments", wins AS "Wins", win_rate AS "Win Rate", last_evolution AS "Last Evolved"
FROM "personalities"
SORT win_rate DESC
```
