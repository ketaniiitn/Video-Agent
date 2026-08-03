# Playbook: Onboarding a new provider

Longer-form version of `.cursor/rules/90-new-provider-onboarding.mdc` — read that rule
first for the checklist; this adds the reasoning behind each step.

- **Implement the interface, don't special-case.** The whole point of the provider
  abstraction (ADR-0004) is that pipeline code doesn't change when a provider is added or
  swapped. If you find yourself adding an `if provider == "x"` branch in a pipeline node,
  the abstraction has a gap — fix the interface, don't route around it.
- **Capability class first.** Decide what this provider is *for* (e.g. "video generation",
  "vision QC scoring") before writing code — that determines its fallback peers.
- **Circuit-break and fallback config are not optional extras.** A provider with no
  fallback configured is a single point of failure disguised as "not done yet."
- **Cost/latency benchmarks land before the first real traffic**, not after — the CI cost-
  regression gate needs a baseline to compare against.
- **Rollout, not a big-bang switch.** Even a provider swap that "should be equivalent" goes
  through the 10%-traffic rollout — see `prompt-change-rollout-playbook.md`, same
  discipline applies to provider changes.
