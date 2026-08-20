# Local test console — design

**Date:** 2026-08-20  
**Status:** Implemented  
**Scope:** Same-origin developer UI to exercise `POST /jobs` / `GET /jobs/{id}` / artifact download. Not a product editor.

## Out of scope (v1 PRD)

Editing timeline, beat reordering, voiceover, dialogue, durations other than 4×10s, login, job history list, WebSockets.

## Shape

- Served by the existing FastAPI process at `GET /`.
- Static HTML/CSS/JS under `app/static/`.
- `GET /ui/config` returns `{ "tenant_id": "<from TENANT_ID env>" }` so the page never hardcodes a tenant.
- Browser sends `X-Tenant-Id` and a fresh `Idempotency-Key` (UUID) on each Generate.
- Poll `GET /jobs/{id}` every 2s until a terminal status.
- Artifact playback uses `fetch` + blob URL because `GET /jobs/{id}/artifacts/...` requires `X-Tenant-Id` (a `<video src>` cannot set that header).

## Terminal statuses (stop polling)

`BIBLE_LOCKED`, `SHOTS_READY`, `DELIVERED`, `PARTIAL`, `FAILED`, `FAILED_NO_PROGRESS`, `ESCALATED`.

## Errors

Show API `code`, `message`, and `trace_id` from the error envelope.
