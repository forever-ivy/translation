# Implementation Notes (V6.0)

## Core changes from V5.3

1. Command protocol is now strict `new -> (send text/files) -> run`.
2. Added `new` command handling in approval/ingest/router paths.
3. Sender can no longer accidentally run without active collecting job when `OPENCLAW_REQUIRE_NEW=1`.
4. Pre-run pipeline remains enforced:
   - `kb_sync_incremental`
   - `kb_retrieve`
5. Knowledge retrieval backend updated:
   - primary: `clawrag`
   - fallback: local sqlite KB retrieval (`rag_fallback_local` flag)
6. Execution uses real Codex + Gemini 3-round loop:
   - GPT-5.2 generator (via `OPENCLAW_CODEX_AGENT`, default `translator-core`)
   - Optional GLM second generator candidate (via `OPENCLAW_GLM_GENERATOR_AGENT`)
   - Gemini review
   - GPT-5.2 revision (only when findings exist)
   - Gemini re-review (only when a revision was applied)
7. Output path is `_VERIFY/{job_id}` only.
8. Verify bundle no longer emits Draft artifacts (only `Final.docx` + `Final-Reflow.docx`).
9. `ok` marks `verified` and archives user-uploaded FINAL file(s) into KB reference (still no auto-delivery copy).
10. Post-run attachments (when a job is `review_ready/needs_attention`) now prompt for destination unless explicit intent is provided (`final`/`ok` text), to prevent mistakenly treating new-task uploads as FINAL files.
11. Contextual command interface:
   - `new | run | status | ok | no {reason} | rerun`
12. Sender-active-job mapping remains persisted in SQLite.
13. WhatsApp strict router mode remains:
   - route inbound task messages to dispatcher, not free-form chat
   - extract `[media attached: ...]` local file paths
   - strip inline `<file ...>` blocks to reduce token pressure
14. Translation model calls enforce `--thinking high` by default:
   - env override: `OPENCLAW_TRANSLATION_THINKING`
15. Execution metadata includes:
   - `thinking_level`
   - `router_mode`
   - `token_guard_applied`
   - `knowledge_backend`
16. Added spreadsheet-aware execution:
   - task type `SPREADSHEET_TRANSLATION`
   - format-preserving `.xlsx` output by applying a cell translation map into a copy of the source workbook
   - output is:
     - `Final.xlsx` (single input workbook), or
     - `*_translated.xlsx` (one per input workbook when multiple `.xlsx` inputs)
   - optional Gemini Vision format QA (multi-sheet) when `OPENCLAW_FORMAT_QA_ENABLED=1`:
     - format fidelity gates pass/fail
     - aesthetics is reported as warnings (fidelity > aesthetics)
16. Setup script now installs pinned first-batch community skills from:
   - `config/skill-lock.v6.json`
17. Cron jobs continue to use `--no-deliver` to avoid noisy delivery-target errors.
18. Optional DOCX Vision QA (layout + aesthetics) when `OPENCLAW_DOCX_QA_ENABLED=1`.

## First-batch skill lock

Pinned in `config/skill-lock.v6.json`:

- `himalaya@1.0.0`
- `openclaw-mem@2.1.0`
- `memory-hygiene@1.0.0`
- `sheetsmith@1.0.1`
- `pdf-extract@1.0.0`
- `docx-skill@1.0.2` (optional)
- `clawrag@1.2.0`

## State model

Main statuses:
- `received`
- `collecting`
- `running`
- `planned`
- `missing_inputs`
- `review_ready`
- `needs_attention`
- `needs_revision`
- `verified`
- `failed`

## Runtime DB additions

Table:
- `sender_active_jobs(sender, active_job_id, updated_at)`

Purpose:
- Resolve contextual commands without explicit `job_id`.
- Enforce strict `new` flow while preserving per-sender continuity.

## Output bundle policy

Generated under:
- `/Users/ivy/Library/CloudStorage/OneDrive-Personal/Translation Task/Translated -EN/_VERIFY/{job_id}`

Never auto-copied to:
- `Translated -EN` root
- Knowledge Repository source tree

## Compatibility

Legacy command aliases remain:
- `approve` -> `ok`
- `reject` -> `no`

## User-facing status format

`status` now returns a 6-line card:

1. Job
2. Stage
3. Task + language pair
4. Input readiness
5. Round progress + double pass
6. Next command

## Known tradeoff

The preserve-mode DOCX writer replaces paragraph/cell text in-place using a translation map and keeps run formatting best-effort by reusing existing runs. For complex inline styling and deeply nested tables, final manual validation is still required (by design of V5.2 human-in-the-loop policy).

## Vision QA knobs

XLSX Vision QA:
- `OPENCLAW_FORMAT_QA_ENABLED=1` to enable
- `OPENCLAW_FORMAT_QA_THRESHOLD` (default `0.85`) for format fidelity pass/fail
- `OPENCLAW_FORMAT_QA_SHEETS_MAX` (default `6`) limits compared sheets
- `OPENCLAW_FORMAT_QA_MAX_RETRIES` (default `2`) limits auto-fix retries

DOCX Vision QA:
- `OPENCLAW_DOCX_QA_ENABLED=1` to enable
- `OPENCLAW_DOCX_QA_PAGES_MAX` (default `6`) limits compared pages
- `OPENCLAW_DOCX_QA_THRESHOLD` (defaults to `OPENCLAW_FORMAT_QA_THRESHOLD`) for format fidelity pass/fail

Shared:
- `OPENCLAW_VISION_AESTHETICS_WARN_THRESHOLD` (default `0.7`) for aesthetics warnings (never blocks by itself)
- Requires LibreOffice (`soffice`) on PATH and `GOOGLE_API_KEY` (or `GEMINI_API_KEY`) for Gemini Vision.

## Preserve payload caps

- `OPENCLAW_XLSX_TRANSLATION_MAX_CELLS` (default `2000`)
- `OPENCLAW_XLSX_MAX_CHARS_PER_CELL` (default `400`)
- `OPENCLAW_DOCX_TRANSLATION_MAX_UNITS` (default `1200`)
- `OPENCLAW_DOCX_MAX_CHARS_PER_UNIT` (default `800`)

## New installer

Use:

- `/Users/Code/workflow/translation/scripts/install_openclaw_translation_skill.sh`

It will:

1. copy `skills/translation-router/SKILL.md` into OpenClaw workspace skills
2. patch OpenClaw workspace `AGENTS.md` with strict-router policy block
3. ensure `.env.v4.local` has strict-router + high-reasoning defaults
4. restart gateway
