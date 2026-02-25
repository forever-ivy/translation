# Web LLM Provider Routing Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Allow Web Gateway provider selection to differ between generation (translation) and review (verification), e.g. generate via ChatGPT web and review via Gemini web.

**Architecture:** Add optional per-phase env overrides and a small helper to compute provider chains. Use the chain in `_codex_generate` and `_gemini_review`. Align pipeline preflight and Tauri Settings to expose the new env keys.

**Tech Stack:** Python (OpenClaw orchestrator + pipeline), React/TS (Tauri UI), `.env` config.

---

### Task 1: Add Per-Phase Provider Env + Chain Helper

**Files:**
- Modify: `scripts/openclaw_translation_orchestrator.py`
- Test: `tests/test_openclaw_translation_orchestrator.py`

**Step 1: Write the failing test**

Add a unit test that patches the new module vars to:

- generate primary = `chatgpt_web`
- review primary = `gemini_web`

Mock `_web_gateway_chat_completion` and assert:

- `_codex_generate(...)` calls gateway with `provider="chatgpt_web"` first
- `_gemini_review(...)` calls gateway with `provider="gemini_web"` first

Run: `python -m unittest tests.test_openclaw_translation_orchestrator -v`
Expected: FAIL (env vars/helper not implemented).

**Step 2: Implement provider chain helper**

Implement module-level env reads and:

- `_web_provider_chain(purpose: Literal["generate","review"]) -> list[str]`
- Backward compatible: empty override falls back to `OPENCLAW_WEB_LLM_PRIMARY/FALLBACK`
- De-dup providers, skip empty values

Wire it into:

- `_codex_generate` web gateway provider loop
- `_gemini_review` web gateway provider loop

**Step 3: Run tests**

Run: `python -m unittest tests.test_openclaw_translation_orchestrator -v`
Expected: PASS.

**Step 4: Commit**

Run:

```bash
git add scripts/openclaw_translation_orchestrator.py tests/test_openclaw_translation_orchestrator.py
git commit -m "feat: split web gateway providers for generate vs review"
```

### Task 2: Align Pipeline Preflight With Provider Chains

**Files:**
- Modify: `scripts/v4_pipeline.py`
- (Optional) Test: `tests/test_v4_pipeline.py`

**Step 1: Implement provider union**

Build provider list = union(generate chain + review chain), primary+fallback, de-dup.

Preflight behavior:

- Check `/session/login` (interactive=false) for each provider.
- If any fails: `needs_attention` with error tokens `gateway_login_required:<provider>`.
- Milestone message lists providers needing login.

**Step 2: Verify**

Run: `python -m unittest tests.test_v4_pipeline -v`
Expected: PASS (tests commonly disable preflight via env).

**Step 3: Commit**

```bash
git add scripts/v4_pipeline.py
git commit -m "fix: preflight checks all web gateway providers"
```

### Task 3: Expose New Env Keys In Tauri Settings

**Files:**
- Modify: `tauri-app/src/pages/Settings.tsx`

**Step 1: Add env field defs**

Add 4 entries to the Web Gateway env fields list.

**Step 2: Verify**

Run:

- `pnpm -C tauri-app run typecheck`
- `pnpm -C tauri-app test`

Expected: PASS.

**Step 3: Commit**

```bash
git add tauri-app/src/pages/Settings.tsx
git commit -m "feat(tauri): add generate/review provider env fields"
```

### Task 4: Update Env Example Docs

**Files:**
- Modify: `config/env.example`

**Step 1: Add env keys**

Add:

- `OPENCLAW_WEB_LLM_PRIMARY/FALLBACK`
- `OPENCLAW_WEB_LLM_GENERATE_PRIMARY/FALLBACK`
- `OPENCLAW_WEB_LLM_REVIEW_PRIMARY/FALLBACK`

**Step 2: Commit**

```bash
git add config/env.example
git commit -m "docs: document per-phase web llm provider env"
```

### Task 5: Final Verification

Run:

- `python -m unittest discover -s tests -p 'test_*.py'`
- `pnpm -C tauri-app run typecheck`
- `pnpm -C tauri-app test`

