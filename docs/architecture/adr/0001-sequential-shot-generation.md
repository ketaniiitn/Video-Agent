# ADR-0001: Generate shots sequentially, not in parallel

**Status:** Accepted

## Context

Shot generation could run in parallel (all 4 shots requested from Higgsfield at once) for
roughly a 4× latency improvement, or sequentially, one shot at a time.

## Decision

Shots generate strictly sequentially. The final frame of shot *n* conditions the generation
of shot *n+1* ("frame chaining"), which is the mechanism that carries character, wardrobe,
location, lighting, and lens identity forward across shots.

## Consequences

- Frame chaining is impossible under parallel generation, since shot *n+1* would need to
  start before shot *n*'s final frame exists.
- The p90 end-to-end latency target (≤ 8 min) must absorb 4 sequential generations plus QC
  and repair — this directly sizes the harness's iteration/time budget caps.
- Do not "optimize" this into parallel execution later without reopening this ADR — it
  would silently break continuity while looking like a pure performance win.

## Alternatives considered

- **Parallel generation + post-hoc identity correction:** rejected. QC-and-repair alone is
  not sufficient to fix full-shot identity drift after the fact; it's a detector, not a
  continuity mechanism.
- **Parallel generation with a shared reference image instead of frame chaining:** rejected
  for v1 — weaker continuity guarantee than frame-to-frame conditioning, and user-supplied
  reference characters are explicitly out of scope for v1.
