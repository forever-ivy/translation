# Web LLM Provider Routing (ChatGPT Generate, Gemini Review) Design

**Date:** 2026-02-25

## Goal

When `OPENCLAW_WEB_GATEWAY_ENABLED=1`, select Web Gateway providers per phase:

- Generation/translation uses ChatGPT web (`chatgpt_web`) as primary.
- Verification/review uses Gemini web (`gemini_web`) as primary.

Must remain backward compatible with the existing globals:

- `OPENCLAW_WEB_LLM_PRIMARY`
- `OPENCLAW_WEB_LLM_FALLBACK`

## Non-Goals

- No UI redesign.
- No changes to the overall round policy ("one generate + one review per round").
- No new gateway protocol; keep `/v1/chat/completions` and `/session/login` contract.

## Env Contract

Existing globals (current behavior):

- `OPENCLAW_WEB_LLM_PRIMARY` (default: `gemini_web`)
- `OPENCLAW_WEB_LLM_FALLBACK` (default: `chatgpt_web`)

New optional per-phase overrides (empty => fallback to globals):

- `OPENCLAW_WEB_LLM_GENERATE_PRIMARY`
- `OPENCLAW_WEB_LLM_GENERATE_FALLBACK`
- `OPENCLAW_WEB_LLM_REVIEW_PRIMARY`
- `OPENCLAW_WEB_LLM_REVIEW_FALLBACK`

Example configuration to match the requested behavior:

```env
OPENCLAW_WEB_LLM_GENERATE_PRIMARY=chatgpt_web
OPENCLAW_WEB_LLM_GENERATE_FALLBACK=gemini_web
OPENCLAW_WEB_LLM_REVIEW_PRIMARY=gemini_web
OPENCLAW_WEB_LLM_REVIEW_FALLBACK=chatgpt_web
```

## Orchestrator Behavior

File:

- `scripts/openclaw_translation_orchestrator.py`

Provider selection rules:

- Generation path (`_codex_generate -> _execute_prompt -> _web_gateway_chat_completion`):
  - Try `GENERATE_*` chain first; if unset, use global chain.
- Review path (`_gemini_review -> _web_gateway_chat_completion` when gateway enabled):
  - Try `REVIEW_*` chain first; if unset, use global chain.
- Deduplicate providers in-order; skip empty values.
- If strict gateway mode is enabled, preserve existing failure behavior.

## Pipeline Preflight Alignment

File:

- `scripts/v4_pipeline.py`

Preflight should check login readiness for *all* providers that may be used:

- Union of generate chain + review chain (primary + fallback), de-duplicated.
- If any provider is not ready, fail fast to `needs_attention` and record which provider(s) need login.

## Tauri Settings Support

File:

- `tauri-app/src/pages/Settings.tsx`

Add 4 optional fields under Web Gateway env:

- `OPENCLAW_WEB_LLM_GENERATE_PRIMARY`
- `OPENCLAW_WEB_LLM_GENERATE_FALLBACK`
- `OPENCLAW_WEB_LLM_REVIEW_PRIMARY`
- `OPENCLAW_WEB_LLM_REVIEW_FALLBACK`

## Docs

File:

- `config/env.example`

Add global provider keys and the new per-phase overrides.

## Verification

- Python: `python -m unittest discover -s tests -p 'test_*.py'`
- Frontend: `pnpm -C tauri-app run typecheck` and `pnpm -C tauri-app test`
