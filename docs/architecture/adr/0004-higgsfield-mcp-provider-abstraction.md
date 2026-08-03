# ADR-0004: Higgsfield behind a provider-abstraction interface, not called directly

**Status:** Accepted

## Context

Video generation depends on Higgsfield via MCP. Calling its client directly from pipeline
nodes would couple the pipeline to one vendor's API shape and failure modes.

## Decision

Pipeline nodes call a provider-abstraction interface (capability negotiation + failover).
Higgsfield is one implementation of that interface, reached via MCP.

## Consequences

- A Higgsfield API change becomes a config/adapter change, not a pipeline-code change or an
  outage.
- Future video-gen providers (if ever needed) implement the same interface rather than
  getting bespoke call sites — see `.cursor/rules/90-new-provider-onboarding.mdc`.
- The failure ladder (retry → fallback → circuit break → degrade → fail honestly) is
  implemented once, at the abstraction layer, and applies uniformly to any provider behind
  it.

## Alternatives considered

- **Direct Higgsfield MCP client calls from `generate_shot`/`repair_shot` nodes:** rejected
  — ties the pipeline's core value proposition (continuity) to one vendor's availability
  with no fallback path, and duplicates failure-handling logic per call site.
