# Translation Automation V5.2 (OpenClaw Skill-First)

## Scope

V5.2 implements:

- OpenClaw-first orchestration
- pure LLM intent classification (`TaskIntentV5`)
- real Codex + Gemini 3-round review loop
- mandatory `kb_sync_incremental` + `kb_retrieve` before every `run`
- outputs only in `_VERIFY/{job_id}`
- no auto-delivery to final folder
- contextual WhatsApp commands:
  - `run`
  - `status`
  - `ok`
  - `no {reason}`
  - `rerun`

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
  - `_INBOX/whatsapp/{job_id}`
- Verify bundle:
  - `Translated -EN/_VERIFY/{job_id}`
- State DB:
  - `.system/jobs/state.sqlite`
- Logs:
  - `.system/logs/*`
- KB index cache:
  - `.system/kb/*`

## Produced artifacts (per job)

Under `_VERIFY/{job_id}`:

- `Final.docx`
- `Final-Reflow.docx`
- `Draft A (Preserve).docx`
- `Draft B (Reflow).docx`
- `Review Brief.docx`
- `Change Log.md`
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
OPENCLAW_NOTIFY_CHANNEL="whatsapp"
OPENCLAW_NOTIFY_ACCOUNT="default"

V4_IMAP_HOST="imap.163.com"
V4_IMAP_PORT=993
V4_IMAP_USER="your_163_account@163.com"
V4_IMAP_PASSWORD="your_163_imap_authorization_code"
V4_IMAP_MAILBOX="INBOX"
V4_IMAP_FROM_FILTER="modeh@eventranz.com"
V4_IMAP_MAX_MESSAGES=5
```

## Install

```bash
cd /Users/Code/workflow/translation
/Users/Code/workflow/translation/.venv/bin/pip install -r requirements.txt
chmod +x scripts/setup_openclaw_v4.sh scripts/run_v4_email_poll.sh scripts/run_v4_pending_reminder.sh
```

## Setup OpenClaw

```bash
cd /Users/Code/workflow/translation
./scripts/setup_openclaw_v4.sh
openclaw gateway --force
openclaw health --json
```

## Run

Email poll:

```bash
cd /Users/Code/workflow/translation
./scripts/run_v4_email_poll.sh
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

1. Upload files (email or WhatsApp) -> job enters `collecting`
2. Send `run`
3. System emits milestones:
   - `collecting_update`
   - `run_accepted`
   - `kb_sync_started`
   - `kb_sync_done`
   - `intent_classified`
   - `round_1_done` (and round 2/3 if needed)
   - `review_ready` or `needs_attention`
4. You manually verify files in `_VERIFY/{job_id}`
5. Send `ok` to mark `verified` (status only, no file move)
6. You manually move final file to destination folder

## Tests

```bash
cd /Users/Code/workflow/translation
/Users/Code/workflow/translation/.venv/bin/python -m unittest discover -s tests -q
```
