# Translation Task Automation

This repository now provides:

- V1: n8n direct multi-model calls (legacy, rollback only)
- V2 (overwritten by V3.2 behavior): n8n orchestration + OpenClaw intelligence

## Paths

- Project repo: `/Users/Code/workflow/translation`
- Translation root: `/Users/ivy/Library/CloudStorage/OneDrive-Personal/Translation Task`

Expected subfolders:

- `Arabic Source`
- `Glossery`
- `Previously Translated`
- `Translated -EN`

Review folder:

- `Translated -EN/_REVIEW/{job_id}`

## Active workflow files (V2 filenames, V3.2 behavior)

- `workflows/WF-00-Orchestrator-V2.json`
- `workflows/WF-20-OpenClaw-Orchestrator-V2.json`
- `workflows/WF-30-Manual-Review-Deliver-V2.json`
- `workflows/WF-99-Error-Audit-V2.json`

V2 reuses existing:

- `workflows/WF-10-Ingest-Classify.json`

## Active scripts (OpenClaw-side intelligence)

- `scripts/openclaw_translation_orchestrator.py`
- `scripts/openclaw_quality_gate.py`
- `scripts/openclaw_artifact_writer.py`

## Environment configuration

### n8n env

Use `.env` (or `config/env.example`) for orchestration-only settings.

Important: In V2, n8n no longer stores model provider API keys.

Required variables:

- `TRANSLATION_ROOT`
- `PYTHON_BIN`
- `OPENCLAW_BASE_URL`
- `OPENCLAW_HOOK_TOKEN`
- `OPENCLAW_AGENT_ID`
- `WF10_WORKFLOW_ID`
- `WF20_V2_WORKFLOW_ID`
- `WF30_V2_WORKFLOW_ID`
- `WF99_V2_WORKFLOW_ID`

### OpenClaw provider secrets

Use `config/openclaw.providers.env.example` as template.
Keep provider keys only in OpenClaw runtime/profile.

## OpenClaw setup

1. Configure hook + fallback chain:

```bash
cd /Users/Code/workflow/translation
OPENCLAW_HOOK_TOKEN="<strong-random-token>" ./scripts/setup_openclaw_v2.sh
```

What this script does:

- Creates `translator-main`, `translator-diff`, `translator-draft`, `translator-qa` agents if missing.
- Enables hooks and sets `hooks.path=/hooks`.
- Sets hook token and primary/fallback model chain.

2. Restart gateway:

```bash
openclaw gateway --force
```

3. Health check:

```bash
openclaw health --json
```

## n8n setup

1. Start n8n with env loaded:

```bash
cd /Users/Code/workflow/translation
set -a
source .env
set +a
n8n start
```

2. Open `http://localhost:5678` and import workflows in this order:

- `WF-99-Error-Audit-V2`
- `WF-10-Ingest-Classify`
- `WF-20-OpenClaw-Orchestrator-V2`
- `WF-30-Manual-Review-Deliver-V2`
- `WF-00-Orchestrator-V2`

3. Bind credentials for IMAP/Email/WhatsApp.

4. Fill workflow IDs in `.env`, then restart n8n.

## Runtime behavior (V3.2, on V2 filenames)

1. n8n detects task event (email or scheduled poll).
2. n8n normalizes all discovered DOCX into `candidate_files` (not fixed to one scenario).
3. n8n calls OpenClaw `/hooks/agent` with `job_id + candidate_files + review_dir`.
4. OpenClaw classifies task type and estimates duration:
   - `REVISION_UPDATE`
   - `NEW_TRANSLATION`
   - `BILINGUAL_REVIEW`
   - `EN_ONLY_EDIT`
   - `MULTI_FILE_BATCH`
5. Timeout policy:
   - `runtime_timeout = min(estimated_minutes * 1.3, 45)`
   - `long_task_capped` flag when capped.
6. OpenClaw runs self-check loop (max 3 rounds):
   - Codex write
   - Gemini review
   - iterative resolve
   - stop on `double_pass=true` or round 3.
7. Artifacts are written to `_REVIEW/{job_id}`:
   - `Draft A (Preserve).docx`
   - `Draft B (Reflow).docx`
   - `Review Brief.docx`
   - `.system/Task Brief.md`
   - `.system/Delta Summary.json`
   - `.system/Model Scores.json`
   - `.system/quality_report.json`
   - `.system/openclaw_result.json`
8. n8n opens manual review gate only for `status=review_pending`.
9. You edit manually and save `*_manual*.docx` or `*_edited*.docx`.
10. Approve callback copies manual file to `Translated -EN`.

## Security requirements (mandatory before production)

- Rotate any previously exposed API keys.
- Rotate gateway token.
- Use dedicated `hooks.token` different from gateway token.
- Keep hook endpoint loopback/tailnet only.

## Quick daily SOP

1. Put source files in `Arabic Source` (or receive by email trigger).
2. Wait for `_REVIEW/{job_id}` artifacts.
3. Edit `English V2 Draft.docx` manually.
4. Save as `*_manual*.docx` in same review folder.
5. Trigger `approve_manual` callback.
6. Confirm final file appears in `Translated -EN`.

## Tests

```bash
PYTHONPATH=/Users/Code/workflow/translation python3 -m unittest discover -s tests -v
```

## Prerequisites

- `n8n`
- `openclaw` CLI/gateway
- `python3` + `python-docx`
- `jq` (used by setup script)

## Dedup strategy

- Dedupe key: `event_hash + file_fingerprint`
- Result: same event + same file version is skipped; same event + new file content is processed.

## Rollback

- Disable `WF-*-V2`
- Re-enable legacy `WF-*`
- No data migration required
