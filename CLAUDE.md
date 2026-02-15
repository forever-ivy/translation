# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Arabic-to-English translation automation system (V6.0). Uses OpenClaw as the sole orchestrator (n8n legacy removed). Incoming translation tasks arrive via Telegram or email, go through a multi-round Codex+Gemini review pipeline, and produce verified document artifacts.

The user prefers human-in-the-loop delivery: the system writes only to `_VERIFY/{job_id}`, and the user manually moves files to the final folder.

## Agent Teams (multi-agent dev)

This repo includes a ready-to-use Agent Teams config at `.claude/settings.local.json` (for Claude Code). Roles and collaboration workflow are documented in `docs/DEV_AGENT_TEAM.md`.

## Commands

### Run tests
```bash
.venv/bin/python -m unittest discover -s tests -q
```

### Run a single test
```bash
.venv/bin/python -m unittest tests.test_skill_message_router -v
```

### Install dependencies
```bash
.venv/bin/pip install -r requirements.txt
```

### Setup OpenClaw
```bash
./scripts/setup_openclaw_v4.sh
./scripts/install_openclaw_translation_skill.sh
openclaw gateway --force
openclaw health --json
```

### Direct command test (approval)
```bash
.venv/bin/python -m scripts.openclaw_v4_dispatcher \
  --work-root "/Users/ivy/Library/CloudStorage/OneDrive-Personal/Translation Task" \
  --kb-root "/Users/ivy/Library/CloudStorage/OneDrive-Personal/Knowledge Repository" \
  --notify-target "<CHAT_ID>" \
  approval --sender "<CHAT_ID>" --command "status"
```

### Email poll / Pending reminder / Telegram bot
```bash
./scripts/run_v4_email_poll.sh
./scripts/run_v4_pending_reminder.sh
./scripts/run_telegram_bot.sh
```

## Architecture

### Entry points

Messages enter through two paths:
- **Telegram**: `telegram_bot.py` long-polls the Telegram Bot API directly (bypassing OpenClaw's broken DM routing), dispatches commands to `skill_approval.handle_command()` and files/text to the dispatcher's `message-event` subcommand. When `TELEGRAM_DIRECT_MODE=1`, all outbound notifications also go directly via Bot API.
- **Email**: `skill_email_ingest.py` polls IMAP, creates jobs from attachments.

Both feed into `openclaw_v4_dispatcher.py`, the unified CLI dispatcher with subcommands: `email-poll`, `message-event`, `run-job`, `kb-sync`, `pending-reminder`, `approval`.

### Pipeline flow

1. **Job creation** (`v4_pipeline.create_job`) — writes to SQLite, sets sender's active job
2. **KB sync** (`v4_kb.sync_kb_with_rag`) — indexes glossary/previously-translated/source docs into local SQLite chunks + ClawRAG vector store
3. **KB retrieve** (`v4_kb.retrieve_kb_with_fallback`) — queries ClawRAG first, falls back to local weighted search
4. **Intent classification** (`openclaw_translation_orchestrator.run` with `plan_only=True`) — classifies task type (REVISION_UPDATE, NEW_TRANSLATION, etc.) and checks for missing inputs
5. **Translation execution** (`openclaw_translation_orchestrator.run`) — up to 3 rounds of GPT-5.2 translation + (optional) GLM second-generator candidate + Gemini review
6. **Artifact writing** (`openclaw_artifact_writer.write_artifacts`) — produces Final.docx, Final-Reflow.docx, Review Brief, Change Log, etc. into `_VERIFY/{job_id}`
7. **Quality gate** (`openclaw_quality_gate.evaluate_quality`) — checks terminology hit rate, structure fidelity, purity, numbering against thresholds

### Command protocol (strict mode)

`new` → `(send files/text)` → `run` → system processes → `ok` / `no {reason}` / `rerun`

When `OPENCLAW_REQUIRE_NEW=1`, calling `run` before `new` is rejected. Legacy `approve`/`reject` map to `ok`/`no`.

### Key modules

- `v4_runtime.py` — `RuntimePaths` dataclass, SQLite schema (jobs, job_files, kb_files, kb_chunks, events, sender_active_jobs, mail_seen), all DB helpers, message sending via `send_telegram_direct()` (when `TELEGRAM_DIRECT_MODE=1`) or `openclaw message send`
- `telegram_bot.py` — standalone Telegram Bot API polling daemon, replaces OpenClaw's Telegram channel for DM routing
- `v4_pipeline.py` — end-to-end pipeline orchestration, milestone notifications
- `v4_kb.py` — document parsing (docx/pdf/xlsx/csv/txt/md), chunking, local search with source-group weighting, ClawRAG bridge integration
- `openclaw_translation_orchestrator.py` — LLM intent classification, multi-round Codex+Gemini translation, delta computation
- `skill_approval.py` — contextual command handler for `new/run/status/ok/no/rerun`
- `skill_message_router.py` — strict Telegram message parser and dispatcher bridge
- `task_bundle_builder.py` — infers language, version, role from filenames for candidate file classification

### State management

SQLite database (kept local at `~/.openclaw/runtime/translation/state.sqlite` when work root is cloud-backed to avoid OneDrive sync issues). Schema auto-creates on first `db_connect()`.

### Environment

Primary env file:
- `.env.v4.local` — work/kb roots, IMAP credentials, RAG backend config, strict router flags (local only; never commit)

Legacy:
- `.env` is deprecated/ignored (was used for old n8n/WhatsApp wiring)

Key env vars: `OPENCLAW_STRICT_ROUTER`, `OPENCLAW_REQUIRE_NEW`, `OPENCLAW_RAG_BACKEND`, `OPENCLAW_TRANSLATION_THINKING` (reasoning level: off/minimal/low/medium/high), `TELEGRAM_DIRECT_MODE` (1=bypass OpenClaw for Telegram send), `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_CHAT_IDS`.

### Skills (config/skill-lock.v6.json)

Required community skills: himalaya, openclaw-mem, memory-hygiene, sheetsmith, pdf-extract, clawrag. Optional: docx-skill. The translation-router skill template lives in `skills/translation-router/SKILL.md`.

### Schemas

JSON schemas in `schemas/` define the contract for job envelopes, execution plans, quality reports, delta packs, model scores, notification events, and other pipeline data structures.

## Python

- Python venv at `.venv/` — always use `.venv/bin/python` to run scripts
- Scripts are invoked as modules: `python -m scripts.module_name`
- `scripts/__init__.py` exists so the package is importable
- Dependencies: python-docx, openpyxl, pypdf (all optional-ish; pypdf and openpyxl have graceful fallbacks)
