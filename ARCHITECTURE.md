# Architecture Notes

## Deployment Model

TakeoffAI is currently designed as a single-tenant deployment: one contractor
installs and runs one vault, one SQLite database, and one set of client
profiles.

`client_id` is a customer identifier inside that contractor workspace, not a
tenant boundary. Historical retrieval is therefore allowed to learn across
different customers of the same contractor.

## Historical Retrieval Intent

The retrieval path in `backend/agents/historical_retrieval.py` is intentionally
cross-customer within a single contractor install.

- Same-customer jobs receive a score bonus.
- Different-customer jobs are still eligible comparables when trade, geography,
  and description similarity match.
- This is meant to capture contractor-wide learning, not isolate customer data
  from other customers of the same contractor.

If TakeoffAI later becomes a hosted multi-tenant product, retrieval must add a
hard contractor or organization boundary before any scoring logic runs.
