# Translation Automation V6.0 (Skill-First, Strict `new -> run`)

## Scope

V6.0 implements:

- OpenClaw-first orchestration (`n8n` is not the main flow).
- Skill-first pipeline with first-batch community skills:
  - `himalaya@1.0.0`
  - `openclaw-mem@2.1.0`
  - `memory-hygiene@1.0.0`
  - `sheetsmith@1.0.1`
  - `pdf-extract@1.0.0`
  - `docx-skill@1.0.2` (optional helper)
  - `clawrag@1.2.0` (primary RAG backend)
- Mandatory command protocol: `new -> (send text/files) -> run`.
- Real Codex + Gemini 3-round review loop, default reasoning `high`.
- Mandatory `kb_sync_incremental` + `kb_retrieve` before every `run`.
- Output only to `_VERIFY/{job_id}`.
- No automatic move to final delivery folder.
- Chat status replies use user-readable 6-line status cards.

Legacy commands `approve/reject` are kept as compatibility aliases and are internally redirected to `ok/no`.

## Fixed paths

- Knowledge source (read-only):
  - `/Users/ivy/Library/CloudStorage/OneDrive-Personal/Knowledge Repository`
- Working root:
  - `/Users/ivy/Library/CloudStorage/OneDrive-Personal/Translation Task`
- Verify output:
  - `/Users/ivy/Library/CloudStorage/OneDrive-Personal/Translation Task/Translated -EN/_VERIFY/{job_id}`

## Runtime layout

- Inbox:
  - `_INBOX/email/{job_id}`
  - `_INBOX/telegram/{job_id}`
- Verify bundle:
  - `Translated -EN/_VERIFY/{job_id}`
- State DB:
  - `.system/jobs/state.sqlite`
- Logs:
  - `.system/logs/*`
- KB index cache:
  - `.system/kb/*`

## Command protocol (V6 strict mode)

- `new`: create a fresh collecting job for current sender.
- `run`: start pipeline for current active collecting job.
- `status`: return user-readable status card.
- `cancel`: force-cancel the current queued/running job immediately.
- `ok`: mark job `verified` and archive your uploaded FINAL file(s) into KB reference (no file move).
- `no {reason}`: mark `needs_revision`.
- `rerun`: rerun current active job.

If `OPENCLAW_REQUIRE_NEW=1`, calling `run` before `new` is rejected.

## Produced artifacts (per job)

Under `_VERIFY/{job_id}`:

- `Final.docx`
- `Final-Reflow.docx`
- `Review Brief.docx`
- `Change Log.md`
- Spreadsheet output (only for spreadsheet tasks):
  - `Final.xlsx` (single input workbook), or
  - `*_translated.xlsx` (one per input workbook when multiple `.xlsx` inputs)
- `.system/execution_plan.json`
- `.system/quality_report.json`
- `.system/openclaw_result.json`
- `.system/Delta Summary.json`
- `.system/Model Scores.json`

## Environment

Create `/Users/Code/workflow/translation/.env.v4.local`:

```bash
V4_WORK_ROOT="/Users/ivy/Library/CloudStorage/OneDrive-Personal/Translation Task"
V4_KB_ROOT="/Users/ivy/Library/CloudStorage/OneDrive-Personal/Knowledge Repository"
V4_PYTHON_BIN="/Users/Code/workflow/translation/.venv/bin/python"

OPENCLAW_NOTIFY_TARGET="+8615071054627"
OPENCLAW_NOTIFY_CHANNEL="telegram"
OPENCLAW_NOTIFY_ACCOUNT="default"

V4_IMAP_HOST="imap.163.com"
V4_IMAP_PORT=993
V4_IMAP_USER="your_163_account@163.com"
V4_IMAP_PASSWORD="your_163_imap_authorization_code"
V4_IMAP_MAILBOX="INBOX"
V4_IMAP_FROM_FILTER="modeh@eventranz.com"
V4_IMAP_MAX_MESSAGES=5

OPENCLAW_STRICT_ROUTER=1
OPENCLAW_TRANSLATION_THINKING=high
OPENCLAW_CODEX_AGENT=translator-core
OPENCLAW_GLM_GENERATOR_AGENT=glm-reviewer
OPENCLAW_FORMAT_QA_ENABLED=0
OPENCLAW_REQUIRE_NEW=1
OPENCLAW_RAG_BACKEND=clawrag
OPENCLAW_RAG_BASE_URL=http://127.0.0.1:8080
OPENCLAW_RAG_COLLECTION=translation-kb
OPENCLAW_RAG_COLLECTION_MODE=auto
OPENCLAW_KB_ISOLATION_MODE=company_strict
OPENCLAW_KB_RERANK_FINAL_K=12
OPENCLAW_KB_RERANK_GLOSSARY_MIN=3
OPENCLAW_KB_RERANK_TERMINOLOGY_GLOSSARY_RATIO=0.4
OPENCLAW_ARCHIVE_REQUIRE_FINAL_UPLOAD=1
OPENCLAW_STATE_DB_PATH=/Users/ivy/.openclaw/runtime/translation/state.sqlite
OPENCLAW_RUN_WORKER_AUTOSTART=1
```

Notes:
- `OPENCLAW_CODEX_AGENT` should point to a GPT-5.2-backed OpenClaw agent (e.g. bind `translator-core` to GPT-5.2 in OpenClaw).
- If `OPENCLAW_FORMAT_QA_ENABLED=1` or `OPENCLAW_DOCX_QA_ENABLED=1`, you also need LibreOffice (`soffice`) on PATH and `GOOGLE_API_KEY` (or `GEMINI_API_KEY`) set for Gemini Vision.
- Aesthetics warnings are controlled by `OPENCLAW_VISION_AESTHETICS_WARN_THRESHOLD` (default `0.7`); format fidelity remains the blocking gate.
- When `OPENCLAW_KB_ISOLATION_MODE=company_strict`, KB files must be stored under `Knowledge Repository/{Section}/{Company}/...` (see `docs/KB_AND_MEMORY_SYSTEM.md`).

## Install

```bash
cd /Users/Code/workflow/translation
/Users/Code/workflow/translation/.venv/bin/pip install -r requirements.txt
chmod +x scripts/setup_openclaw_v4.sh scripts/run_telegram_bot.sh scripts/run_v4_email_poll.sh scripts/run_v4_pending_reminder.sh scripts/run_v4_run_worker.sh
chmod +x scripts/install_openclaw_translation_skill.sh
```

## Setup OpenClaw

```bash
cd /Users/Code/workflow/translation
./scripts/setup_openclaw_v4.sh
./scripts/install_openclaw_translation_skill.sh
openclaw gateway --force
openclaw health --json
```

## Strict Telegram Router

Skill template:

- `/Users/Code/workflow/translation/skills/translation-router/SKILL.md`

Installed runtime skill:

- `~/.openclaw/workspace/skills/translation-router/SKILL.md`

Router bridge script:

- `/Users/Code/workflow/translation/scripts/skill_message_router.py`

What it does:

1. Parses raw Telegram message text.
2. Extracts `[media attached: ...]` file paths.
3. Removes inline `<file ...>` payload blocks (token guard).
4. Dispatches to:
   - `message-event` for task/file intake
   - `approval` for command-only messages (`new/run/status/ok/no/rerun`)

## Run

Email poll:

```bash
cd /Users/Code/workflow/translation
./scripts/run_v4_email_poll.sh
```

Run worker (executes queued `run` jobs in background):

```bash
cd /Users/Code/workflow/translation
./scripts/run_v4_run_worker.sh
```

Pending reminder:

```bash
cd /Users/Code/workflow/translation
./scripts/run_v4_pending_reminder.sh
```

Direct command test:

```bash
cd /Users/Code/workflow/translation
/Users/Code/workflow/translation/.venv/bin/python -m scripts.openclaw_v4_dispatcher \
  --work-root "/Users/ivy/Library/CloudStorage/OneDrive-Personal/Translation Task" \
  --kb-root "/Users/ivy/Library/CloudStorage/OneDrive-Personal/Knowledge Repository" \
  --notify-target "+8615071054627" \
  approval --sender "+8615071054627" --command "status"
```

## Message flow

1. Send `new` -> job enters `collecting`
2. Upload files and/or task text (email or Telegram)
3. Send `run` -> system asks you to pick a company (reply with a number), then enqueues the run (worker executes it out-of-band)
3. System emits milestones:
  - `collecting_update`
  - `run_accepted`
  - `kb_sync_started`
   - `kb_sync_done`
   - `kb_retrieve_done`
   - `intent_classified`
   - `round_1_done` (and round 2/3 if needed)
   - `review_ready` or `needs_attention`
4. You manually verify files in `_VERIFY/{job_id}`
5. Upload your FINAL file(s) as Telegram attachments (these are the ones that get archived)
6. Send `ok` -> marks `verified` and copies your FINAL file(s) into `Knowledge Repository/30_Reference/{Company}/{Project}/final/`
7. You manually move final file to destination folder (delivery remains manual)

## Troubleshooting

1. Check skill is installed:

```bash
openclaw skills list | rg translation-router
```

2. Check strict router + V6 flags:

```bash
rg "OPENCLAW_STRICT_ROUTER|OPENCLAW_TRANSLATION_THINKING|OPENCLAW_REQUIRE_NEW|OPENCLAW_RAG_BACKEND" /Users/Code/workflow/translation/.env.v4.local
```

3. Check clawrag bridge health:

```bash
/Users/Code/workflow/translation/.venv/bin/python -m scripts.skill_clawrag_bridge --base-url "http://127.0.0.1:8080" health
```

4. Check cron delivery noise is gone:

```bash
openclaw cron list --json | jq '.jobs[] | {name, lastStatus: .state.lastStatus, lastError: .state.lastError}'
```

5. If `run` says no active job:

```text
Send: new
Attach files/text
Send: run
```

## Tests

```bash
cd /Users/Code/workflow/translation
/Users/Code/workflow/translation/.venv/bin/python -m unittest discover -s tests -q
```
