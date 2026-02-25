# Long-term Memory

- Translation automation project moved to OpenClaw-first architecture.
- Current direction: Playwright-driven Web LLM Gateway (Gemini web primary; ChatGPT web fallback) with strict observability and preflight before `running`.
- User prefers human-in-the-loop delivery: system writes only to `_VERIFY`, manual move to final folder.
- User wants contextual WhatsApp commands instead of job-id-heavy commands.
- Priority is translation quality and format fidelity with real model cross-checking.
- Failover order preference: Kimi (`moonshot/kimi-k2.5`) before GLM (`zai/glm-*`) when Codex/Gemini are unavailable.
- Vision QA preference: use Kimi (Moonshot) for multimodal format checks when Gemini Vision keys are unavailable/restricted.
- 2026-02-25: Tauri UI slimmed to core loop (Runtime/Jobs/Verify/Logs/KB Health/Glossary/Settings); Verify now covers needs_attention with actionable buttons; Logs supports gateway logs; Settings/KB Health simplified.
