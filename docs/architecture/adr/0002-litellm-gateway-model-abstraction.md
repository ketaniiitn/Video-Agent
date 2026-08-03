# ADR-0002: Single LiteLLM proxy as the only model egress

**Status:** Accepted

## Context

The platform spec requires that code never name a provider directly, so that swapping
Gemini/OpenAI/Claude models is a config change, not a code change.

## Decision

All model calls go through a single LiteLLM proxy. Application code references only logical
aliases (`reasoning-high`, `reasoning-fast`, `vision-default`, `embed-default`,
`realtime-voice`); alias-to-model mapping lives entirely at the gateway.

## Consequences

- Enables the platform's 10%-traffic rollout requirement for model/prompt changes — the
  rollout is a gateway config change, not a redeploy.
- Centralizes retry/fallback/circuit-break behaviour at one layer instead of duplicating it
  per call site.
- Requires discipline: any new call site that imports a provider SDK directly is a rule
  violation (`.cursor/rules/02-model-routing.mdc`), not a style nitpick.

## Alternatives considered

- **Direct provider SDK calls per node:** rejected — makes every model swap a multi-file
  code change and defeats the 10%-rollout requirement.
