# Implementation Notes (V3.2 Behavior on V2 Files)

## What changed

This repository keeps the same V2 workflow filenames, but behavior is upgraded to V3.2:

1. Full-scenario task handling:
   - `REVISION_UPDATE`
   - `NEW_TRANSLATION`
   - `BILINGUAL_REVIEW`
   - `EN_ONLY_EDIT`
   - `MULTI_FILE_BATCH`
2. Self-check loop:
   - max rounds = 3
   - stop rule = both Codex and Gemini pass (`double_pass=true`)
3. Dynamic duration:
   - estimate at runtime
   - timeout budget = `min(estimated * 1.3, 45)`
   - capped tasks flagged as `long_task_capped`
4. Manual delivery remains mandatory:
   - no `*_manual*.docx` or `*_edited*.docx` => approve blocked

## Public contracts

### HookTaskRequest

- `meta` now accepts:
  - `candidate_files[]` (primary input)
  - legacy `files` map (backward compatibility)
  - `task_type` (optional hint)

### HookTaskResponse (result JSON)

New fields:

- `plan` (`task_type`, `confidence`, `estimated_minutes`, `complexity_score`, `time_budget_minutes`)
- `iteration_count`
- `double_pass`
- `estimated_minutes`
- `runtime_timeout_minutes`
- `actual_duration_minutes`
- `status_flags[]`
- `quality_report` (`rounds[]`, `convergence_reached`, `stop_reason`)

### Artifacts written to review folder

- `Draft A (Preserve).docx`
- `Draft B (Reflow).docx`
- `Review Brief.docx`
- `.system/Task Brief.md`
- `.system/Delta Summary.json`
- `.system/Model Scores.json`
- `.system/quality_report.json`
- `.system/openclaw_result.json`

## Workflow impact

Updated files:

- `/Users/Code/workflow/translation/workflows/WF-10-Ingest-Classify.json`
- `/Users/Code/workflow/translation/workflows/WF-20-OpenClaw-Orchestrator-V2.json`
- `/Users/Code/workflow/translation/workflows/WF-30-Manual-Review-Deliver-V2.json`
- `/Users/Code/workflow/translation/workflows/WF-99-Error-Audit-V2.json`

## Operational notes

1. V2 workflow IDs and import names are unchanged.
2. If you already imported workflows, re-import and overwrite to get new node scripts.
3. OpenClaw + n8n should remain on the same machine with loopback hook URL.
4. If Gemini is unavailable, workflow flags `degraded_single_model` and continues with Codex.

