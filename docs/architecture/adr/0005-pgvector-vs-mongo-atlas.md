# ADR-0005: Vector storage choice — deferred

**Status:** Proposed (intentionally left open)

## Context

The platform spec allows pgvector (default) or MongoDB Atlas behind one protocol for vector
storage. Sales Agent and SQL Agent likely need semantic retrieval; Video Agent's v1 scope
(plan → bible → sequential shots → QC → assemble) has no specified semantic-search use case
— story plans and continuity bibles are structured JSON queried relationally, not by
similarity.

## Decision

**Do not adopt a vector store for Video Agent v1.** Revisit only when a concrete retrieval
use case appears (e.g., "find past jobs with a similar visual style" or cross-job style
search) — at that point, default to pgvector per the platform spec unless a specific reason
favors MongoDB Atlas, and write a new ADR then.

## Consequences

- Avoids standing up and operating infrastructure with no current consumer.
- If a genuine similarity-search need appears mid-project, there will be a short lag to add
  it — acceptable, since it's cheaper than maintaining unused infra from day one.

## Alternatives considered

- **Adopt pgvector now, "for later":** rejected — no v1 feature consumes it, and the
  platform's own CI/cost-regression discipline argues against speculative infrastructure.
