# Project Memory

Hand-authored durable context — a deliberate complement to Cursor's built-in per-project
Memories. Cursor's built-in Memories auto-capture short preference strings picked up during
chats; they're useful but shallow and not fully author-controlled. This file is the
opposite: precise, versioned, and meant to be read in full at the start of a work session,
not just retrieved fragment-by-fragment.

## What this project is

Video Agent — turns one prompt into a continuous 40-second story (4× 10s shots) with
enforced narrative and visual continuity. One of three products on the shared Entermind
platform; this repo builds Video Agent only. See `docs/architecture/*-summary.md` for the
condensed specs, and the two source PDFs (`Guidelines.pdf`, `Video-Agent.pdf`, both v1.0,
2 Aug 2026) for the originals if anything here seems to drift from them.

## How this repo is set up for Cursor

- `.cursor/rules/00`–`04` are always-applied — every non-negotiable, the model-routing
  rule, the harness/termination model, and the out-of-scope list load on every conversation.
- `.cursor/rules/10`–`18` are folder-scoped (Auto Attached) — they load only when you're
  working in the relevant `app/` subfolder, so open the right folder before asking for
  domain-specific help.
- `.cursor/rules/90`–`91` are manual — invoke with `@new-provider-onboarding` or
  `@incident-debug` when those specific situations come up.
- `docs/` holds everything that's reference material rather than enforced rule: ADRs,
  playbooks, checklists, workflows, glossary.
- `.cursor/prompts/` holds copy-paste (or `@`-reference) prompt templates for recurring
  tasks — Cursor doesn't support custom slash commands the way some other tools do, so
  these are plain files, not registered commands.

## Standing facts worth not re-explaining

- Solo developer project (as of Aug 2026) — checklists and playbooks assume self-review,
  not a second reviewer, but still enforce the platform's non-negotiables at full strength.
- Repo started greenfield in Aug 2026 — no legacy code, no existing schema to migrate from.
- Scope is Video Agent only for now; Sales Agent and SQL Agent share the platform layer but
  aren't part of this repo.
- ADR-0005 deliberately defers vector storage — don't add pgvector/Mongo Atlas speculatively.
- M1 prompt registry is a local name+version fallback in `app/prompts/registry.py`
  when Langfuse credentials are unset — deliberate interim vs `18-prompt-engineering.mdc`.
  Switch when `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` are configured.

## Update this file when

- A platform non-negotiable changes (rare — should come with a spec version bump).
- The repo's scope changes (e.g. Sales/SQL agents get added to this repo).
- A standing assumption above stops being true (e.g. a second engineer joins).
