# Web LLM Gateway (Gemini Primary, ChatGPT Fallback) - Design

Date: 2026-02-24

## Why This Refactor

The existing translation pipeline could block or fail without clear evidence when the model runtime/CLI was unavailable (e.g., quota errors, unsupported subcommands). This refactor moves the primary translation/review execution to a Playwright-driven, observable "Web LLM Gateway" that drives real web UIs:

- Primary: Gemini web (generator + reviewer)
- Fallback: ChatGPT web (generator + reviewer)

The Python pipeline remains the source of truth for:

- XLSX/DOCX unit extraction and preserve-write
- Job queue, state machine, artifacts manifest, `_VERIFY` output rules
- Quality gating and multi-round convergence

## Goals

- Web UI driving is the default for generation and review (Gemini primary, ChatGPT fallback).
- Support DOCX + XLSX preserve translation outputs.
- Up to 3 rounds, early stop on pass.
- Each round: one generation + one automatic review. No in-round regeneration loops.
- Preflight before `running`: detect gateway/login issues and fail fast to `needs_attention`.
- Observability: persist prompt/response/meta + screenshot per web call under `_VERIFY/<job_id>`.
- Tauri can start/stop gateway and display per-provider status + login.

## Non-Goals

- CI will not run real web E2E. Tests use mocks/stubs for gateway calls.
- App is not the primary user interface; Telegram command protocol stays.

## Architecture

### Components

1. Web LLM Gateway (FastAPI + Playwright)
   - One process managing multiple providers (`gemini_web`, `chatgpt_web`)
   - One provider call at a time per provider (internal lock)
   - Persistent profile directories per provider for cookies/sessions

2. Python Worker + Pipeline
   - Queue claim -> `preflight` -> `running`
   - Preflight calls gateway session check/login for the primary provider
   - Orchestrator executes up to 3 rounds (generate + review per round)
   - Preserve-write artifacts to `_VERIFY/<job_id>` and record manifest

3. Tauri Dashboard
   - Start/stop gateway, show status
   - Per-provider login triggers via IPC
   - Shows `preflight` job state to explain why a job didn't enter `running`

### Data Flow (High Level)

1. Ingest creates job + attachments -> `queued`
2. Worker claims -> job `preflight`
3. Pipeline preflight:
   - Calls `POST /session/login` with `{ provider: <primary>, interactive: false }`
   - If not ready: job -> `needs_attention` with `gateway_login_required:<provider>`
4. Orchestrator:
   - Round 1..3:
     - Generate (Gemini web; fallback ChatGPT web)
     - Review (Gemini web; fallback ChatGPT web)
     - On pass: stop early
     - On fail: feed findings + retry hints into next round
5. Artifact writer:
   - XLSX: preserve-write to `Final.xlsx` (single file) or `<stem>_translated.xlsx` (multi)
   - DOCX: preserve-write to `Final.docx` (+ `Final-Reflow.docx` where applicable)
6. Pipeline marks job:
   - `review_ready` when passed
   - `needs_attention` when max rounds exhausted

## Web LLM Gateway API

- `GET /health`
  - Returns `{ ok, providers: { gemini_web: {...}, chatgpt_web: {...} }, primary_provider, version }`

- `POST /session/login`
  - `{ provider, interactive, timeout_seconds }`
  - Ensures browser can launch; checks session state; optionally waits for interactive login.

- `GET /session/diagnose?provider=...`
  - Snapshot for selector drift debugging (screenshot + selector probe info).

- `POST /v1/chat/completions`
  - OpenAI-compatible surface with additional fields:
    - `provider`, `job_id`, `round`, `operation_id`, `batch_id`
    - `new_chat` (defaults to per-request new chat)
    - `format_contract` (schema enforcement + repair prompting)
  - Response includes `meta.gateway` (provider, url, extract method, durations, screenshot path)

## Observability Contract

When `OPENCLAW_WEB_GATEWAY_TRACE=1` (default), each completion persists:

- JSON: prompt/messages, response text, meta, errors
- Screenshot: last known stable UI state

Location:

`<work_root>/Translated -EN/_VERIFY/<job_id>/.system/web_calls/<provider>/...`

## Config

- `OPENCLAW_WEB_LLM_PRIMARY=gemini_web`
- `OPENCLAW_WEB_LLM_FALLBACK=chatgpt_web`
- `OPENCLAW_WEB_SESSION_MODE=per_request`
- `OPENCLAW_WEB_GATEWAY_BASE_URL=http://127.0.0.1:8765`
- `OPENCLAW_WEB_GATEWAY_PROFILES_DIR=~/.openclaw/runtime/translation/web-profiles`
- `OPENCLAW_WEB_GATEWAY_TRACE=1`
- `OPENCLAW_WEB_GATEWAY_PREFLIGHT=1`

## Testing Strategy

- Unit tests (Python): orchestrator/pipeline/queue behavior uses mocked gateway preflight and mocked completion calls.
- Unit tests (Tauri TS/Rust): typecheck + `cargo check` ensures IPC payloads match.
- Manual E2E (local):
  - Start gateway
  - Perform interactive login in Tauri per provider
  - Run XLSX/DOCX jobs and verify traces under `_VERIFY/.../.system/web_calls/...`

