#!/usr/bin/env python3
"""OpenClaw Web LLM Gateway (Playwright-driven, OpenAI-compatible surface).

This service drives real web UIs (DeepSeek/ChatGPT) and exposes a small HTTP API:
- GET /health
- GET /session
- GET /session/diagnose?provider=...
- POST /session/login
- POST /v1/chat/completions

Observability: when trace is enabled, each call is persisted under:
  <work_root>/Translated -EN/_VERIFY/<job_id>/.system/web_calls/<provider>/
as JSON + a screenshot, so "running stuck" has artifacts to debug.
"""

from __future__ import annotations

import asyncio
import argparse
import json
import os
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse
import uvicorn

from scripts.gateway_format_contract import apply_format_contract, build_format_repair_prompt

PROVIDER_DEEPSEEK_WEB = "deepseek_web"
PROVIDER_CHATGPT_WEB = "chatgpt_web"

DEEPSEEK_HOME_URL = "https://chat.deepseek.com/"
CHATGPT_HOME_URL = "https://chatgpt.com/"

CHATGPT_PROMPT_SELECTORS = [
    "textarea#prompt-textarea",
    "textarea[data-id='prompt-textarea']",
    "div[contenteditable='true'][data-id='prompt-textarea']",
    "div[contenteditable='true']",
]
CHATGPT_SEND_SELECTORS = [
    "button[data-testid='send-button']",
    "button[aria-label*='Send']",
    "button[data-testid='fruitjuice-send-button']",
]
CHATGPT_LOADING_SELECTORS = [
    "button[data-testid='stop-button']",
    "button[aria-label*='Stop']",
    "div[class*='result-streaming']",
    "div[class*='typing']",
]
CHATGPT_ASSISTANT_SELECTORS = [
    "[data-message-author-role='assistant']",
    "article[data-testid*='conversation-turn']",
    "main article",
]

DEEPSEEK_PROMPT_SELECTORS = [
    # Try specific placeholders first, then fall back to generic textarea/contenteditable.
    "textarea[placeholder*='Message']",
    "textarea[placeholder*='message']",
    "textarea[placeholder*='Send']",
    "textarea[placeholder*='send']",
    "textarea[placeholder*='Ask']",
    "textarea[placeholder*='ask']",
    "textarea[placeholder*='输入']",
    "textarea[placeholder*='发送']",
    "textarea",
    "[contenteditable='true'][role='textbox']",
    "div[contenteditable='true']",
]
DEEPSEEK_SEND_SELECTORS = [
    "button[type='submit']",
    "button[aria-label*='Send']",
    "button[aria-label*='send']",
    "button:has-text('Send')",
    "button:has-text('发送')",
]
DEEPSEEK_LOADING_SELECTORS = [
    "button[aria-label*='Stop']",
    "button[aria-label*='stop']",
    "button:has-text('Stop')",
    "button:has-text('停止')",
    "div[class*='typing']",
]
DEEPSEEK_ASSISTANT_SELECTORS = [
    "[data-message-author-role='assistant']",
    "[class*='assistant']",
    "main [class*='message']",
    "main article",
]


def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _truthy_env(name: str, default: str = "0") -> bool:
    return str(os.getenv(name, default)).strip().lower() not in {"0", "false", "off", "no", ""}


def _join_prompt_from_messages(messages: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for msg in messages:
        role = str(msg.get("role") or "user").strip()
        content = msg.get("content")
        if isinstance(content, list):
            text_items: list[str] = []
            for item in content:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    text_items.append(str(item.get("text")))
            content_text = "\n".join([x for x in text_items if x.strip()])
        else:
            content_text = str(content or "")
        if not content_text.strip():
            continue
        parts.append(f"[{role}] {content_text}")
    return "\n\n".join(parts).strip()


@dataclass
class ProviderState:
    provider: str
    model: str
    base_url: str
    profile_dir: Path
    home_url: str
    running: bool = True
    healthy: bool = False
    logged_in: bool = False
    last_error: str = ""
    updated_at: str = field(default_factory=_utc_now)
    session_checked_at: str = ""
    last_url: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "running": bool(self.running),
            "healthy": bool(self.healthy),
            "logged_in": bool(self.logged_in),
            "base_url": self.base_url,
            "model": self.model,
            "home_url": self.home_url,
            "last_error": self.last_error,
            "updated_at": self.updated_at,
            "session_checked_at": self.session_checked_at,
            "profile_dir": str(self.profile_dir),
            "last_url": self.last_url,
        }


class PlaywrightWebProvider:
    def __init__(
        self,
        *,
        provider: str,
        model: str,
        base_url: str,
        home_url: str,
        profile_dir: Path,
        headless: bool,
        browser_channel: str = "",
        prompt_selectors: list[str],
        send_selectors: list[str],
        loading_selectors: list[str],
        assistant_selectors: list[str],
        login_url_tokens: list[str] | None = None,
    ) -> None:
        self.state = ProviderState(
            provider=str(provider),
            model=str(model),
            base_url=str(base_url),
            profile_dir=profile_dir,
            home_url=str(home_url),
        )
        self.headless = bool(headless)
        self.browser_channel = str(browser_channel or "").strip()
        self.prompt_selectors = list(prompt_selectors)
        self.send_selectors = list(send_selectors)
        self.loading_selectors = list(loading_selectors)
        self.assistant_selectors = list(assistant_selectors)
        self.login_url_tokens = [str(t) for t in (login_url_tokens or []) if str(t).strip()]
        self._playwright = None
        self._context = None
        self._primary_page = None
        self._lock: asyncio.Lock | None = None
        # per-job session reuse (OPENCLAW_WEB_SESSION_MODE=per_job)
        self._job_pages: dict[str, Any] = {}
        self._job_chat_url: dict[str, str] = {}
        self._job_last_active: dict[str, float] = {}

    def _get_lock(self) -> asyncio.Lock:
        # Lazily initialize: asyncio primitives can be loop-bound depending on Python version.
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    @staticmethod
    def _is_target_closed_error(exc: BaseException) -> bool:
        name = exc.__class__.__name__
        msg = str(exc)
        return (
            name == "TargetClosedError"
            or "TargetClosedError" in msg
            or "Target page, context or browser has been closed" in msg
            or "context or browser has been closed" in msg
        )

    async def _reset_browser(self) -> None:
        context, self._context = self._context, None
        playwright, self._playwright = self._playwright, None
        self._primary_page = None
        self._job_pages = {}
        self._job_chat_url = {}
        self._job_last_active = {}
        try:
            if context is not None:
                await context.close()
        except Exception:
            pass
        try:
            if playwright is not None:
                await playwright.stop()
        except Exception:
            pass

    async def _ensure_browser(self) -> tuple[bool, str]:
        if self._context is not None:
            try:
                if self._primary_page is None:
                    self._primary_page = self._context.pages[0] if self._context.pages else await self._context.new_page()
                return True, ""
            except Exception:
                await self._reset_browser()

        try:
            from playwright.async_api import async_playwright  # lazy import

            self._playwright = await async_playwright().start()
            self.state.profile_dir.mkdir(parents=True, exist_ok=True)
            launch_kwargs: dict[str, Any] = {
                "user_data_dir": str(self.state.profile_dir),
                "headless": self.headless,
            }
            if self.browser_channel:
                launch_kwargs["channel"] = self.browser_channel
            try:
                self._context = await self._playwright.chromium.launch_persistent_context(**launch_kwargs)
            except Exception:
                # Fallback to Playwright-managed Chromium if the requested channel is unavailable.
                launch_kwargs.pop("channel", None)
                self._context = await self._playwright.chromium.launch_persistent_context(**launch_kwargs)
            self._primary_page = self._context.pages[0] if self._context.pages else await self._context.new_page()
            return True, ""
        except Exception as exc:
            await self._reset_browser()
            return False, f"playwright_init_failed:{exc}"

    def _is_login_page(self, url: str) -> bool:
        u = str(url or "").lower()
        return any(token.lower() in u for token in self.login_url_tokens)

    async def _pick_first_visible_on_page(self, page: Any, selectors: list[str]):
        for sel in selectors:
            try:
                loc = page.locator(sel).first
                if await loc.count() > 0 and await loc.is_visible():
                    return sel, loc
            except Exception:
                continue
        return None, None

    async def check_logged_in(self, *, interactive: bool = False, timeout_seconds: int = 15) -> dict[str, Any]:
        async with self._get_lock():
            return await self._check_logged_in(interactive=interactive, timeout_seconds=timeout_seconds)

    async def diagnose_session(self) -> dict[str, Any]:
        async with self._get_lock():
            return await self._diagnose_session()

    async def reset_browser(self) -> None:
        async with self._get_lock():
            await self._reset_browser()

    async def _check_logged_in(self, *, interactive: bool = False, timeout_seconds: int = 15) -> dict[str, Any]:
        for attempt in range(2):
            ok, err = await self._ensure_browser()
            if not ok:
                self.state.logged_in = False
                self.state.healthy = False
                self.state.last_error = err
                self.state.session_checked_at = _utc_now()
                return {"ok": False, "error": err}

            try:
                assert self._primary_page is not None
                await self._primary_page.goto(self.state.home_url, wait_until="domcontentloaded", timeout=45000)
                await self._primary_page.wait_for_timeout(1500)

                if interactive and not self.headless:
                    await self._primary_page.wait_for_timeout(max(1000, int(timeout_seconds * 1000)))

                sel, loc = await self._pick_first_visible_on_page(self._primary_page, self.prompt_selectors)
                logged_in = bool(sel and loc and not self._is_login_page(str(self._primary_page.url)))

                self.state.logged_in = bool(logged_in)
                self.state.healthy = bool(logged_in) or bool(os.getenv("OPENCLAW_WEB_GATEWAY_REQUIRE_LOGIN", "0") != "1")
                self.state.last_error = "" if logged_in else "gateway_login_required"
                self.state.session_checked_at = _utc_now()
                self.state.last_url = str(self._primary_page.url)
                return {"ok": logged_in, "logged_in": logged_in, "selector": sel or "", "url": str(self._primary_page.url)}
            except Exception as exc:
                if attempt == 0 and self._is_target_closed_error(exc):
                    await self._reset_browser()
                    continue
                self.state.logged_in = False
                self.state.healthy = False
                self.state.last_error = f"gateway_login_check_failed:{exc}"
                self.state.session_checked_at = _utc_now()
                return {"ok": False, "error": self.state.last_error}

        self.state.logged_in = False
        self.state.healthy = False
        self.state.last_error = "gateway_login_check_failed:unknown"
        self.state.session_checked_at = _utc_now()
        return {"ok": False, "error": self.state.last_error}

    async def _diagnose_session(self) -> dict[str, Any]:
        for attempt in range(2):
            ok, err = await self._ensure_browser()
            if not ok:
                return {"ok": False, "error": err, "logged_in": False, "selector_probe": {}}

            assert self._primary_page is not None
            try:
                await self._primary_page.goto(self.state.home_url, wait_until="domcontentloaded", timeout=45000)
                await self._primary_page.wait_for_timeout(1200)
                selector_probe: dict[str, bool] = {}
                for sel in self.prompt_selectors + self.send_selectors + self.loading_selectors:
                    try:
                        selector_probe[sel] = bool(await self._primary_page.locator(sel).count() > 0)
                    except Exception:
                        selector_probe[sel] = False
                login = await self._check_logged_in(interactive=False)
                return {
                    "ok": bool(login.get("ok", False)),
                    "logged_in": bool(login.get("logged_in", False)),
                    "url": str(self._primary_page.url),
                    "selector_probe": selector_probe,
                    "last_error": "" if login.get("ok") else str(login.get("error") or "gateway_login_required"),
                }
            except Exception as exc:
                if attempt == 0 and self._is_target_closed_error(exc):
                    await self._reset_browser()
                    continue
                return {"ok": False, "logged_in": False, "error": f"gateway_diagnose_failed:{exc}", "selector_probe": {}}

        return {"ok": False, "logged_in": False, "error": "gateway_diagnose_failed:unknown", "selector_probe": {}}

    async def _prompt_send(self, page: Any, prompt: str) -> tuple[bool, str]:
        wait_seconds = max(1.0, float(os.getenv("OPENCLAW_WEB_GATEWAY_PROMPT_WAIT_SECONDS", "12") or 12))
        deadline = time.time() + wait_seconds
        sel, box = None, None
        while time.time() < deadline:
            sel, box = await self._pick_first_visible_on_page(page, self.prompt_selectors)
            if sel and box is not None:
                break
            try:
                await page.wait_for_timeout(250)
            except Exception:
                break
        if not sel or box is None:
            return False, "prompt_selector_missing"
        try:
            await box.click(timeout=5000)
            await box.evaluate(
                """
                (el, value) => {
                    const tag = (el.tagName || '').toLowerCase();
                    const v = String(value || '');
                    if (tag === 'textarea') {
                        const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value')?.set;
                        if (setter) {
                            setter.call(el, v);
                        } else {
                            el.value = v;
                        }
                        el.dispatchEvent(new Event('input', { bubbles: true }));
                        el.dispatchEvent(new Event('change', { bubbles: true }));
                        return;
                    }
                    if (tag === 'input') {
                        const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
                        if (setter) {
                            setter.call(el, v);
                        } else {
                            el.value = v;
                        }
                        el.dispatchEvent(new Event('input', { bubbles: true }));
                        el.dispatchEvent(new Event('change', { bubbles: true }));
                        return;
                    }
                    // contenteditable / other
                    el.textContent = v;
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                }
                """,
                prompt,
            )
            await asyncio.sleep(0.15)

            def _normalize_value(value: str) -> str:
                return " ".join(str(value or "").split()).strip()

            before_send = _normalize_value(
                await box.evaluate(
                    """
                    (el) => {
                        const tag = (el.tagName || '').toLowerCase();
                        if (tag === 'textarea' || tag === 'input') return String(el.value || '');
                        return String(el.textContent || '');
                    }
                    """
                )
            )

            # Try explicit send selectors first; fall back to keyboard submission.
            _, send_btn = await self._pick_first_visible_on_page(page, self.send_selectors)
            if send_btn is not None:
                await send_btn.click(timeout=5000)
            else:
                await box.press("Enter", timeout=5000)

            async def _box_value() -> str:
                return _normalize_value(
                    await box.evaluate(
                        """
                        (el) => {
                            const tag = (el.tagName || '').toLowerCase();
                            if (tag === 'textarea' || tag === 'input') return String(el.value || '');
                            return String(el.textContent || '');
                        }
                        """
                    )
                )

            # Heuristic: most chat UIs clear the input on successful submit.
            await asyncio.sleep(0.25)
            after_send = await _box_value()
            if after_send and before_send and after_send.startswith(before_send[: min(60, len(before_send))]):
                # Fallback: try platform-specific "send" combos and a DOM-click heuristic.
                try:
                    await box.press("Meta+Enter", timeout=2500)
                except Exception:
                    pass
                try:
                    await box.press("Control+Enter", timeout=2500)
                except Exception:
                    pass
                await asyncio.sleep(0.25)
                after_send2 = await _box_value()
                if after_send2 and before_send and after_send2.startswith(before_send[: min(60, len(before_send))]):
                    clicked = await box.evaluate(
                        """
                        (el) => {
                            const root = el.closest('form') || el.closest('main') || el.parentElement;
                            if (!root) return false;
                            const buttons = Array.from(root.querySelectorAll('button')).filter((b) => {
                                try {
                                    if (b.disabled) return false;
                                    if (b.offsetParent === null) return false;
                                } catch (e) {
                                    return false;
                                }
                                const t = (b.innerText || '').trim();
                                if (t === 'DeepThink' || t === 'Search') return false;
                                return true;
                            });
                            const candidate = buttons.length ? buttons[buttons.length - 1] : null;
                            if (!candidate) return false;
                            candidate.click();
                            return true;
                        }
                        """
                    )
                    if clicked:
                        await asyncio.sleep(0.25)
                        after_send3 = await _box_value()
                        if after_send3 and before_send and after_send3.startswith(before_send[: min(60, len(before_send))]):
                            return False, "prompt_send_no_effect"
                    else:
                        return False, "prompt_send_selector_missing"
            return True, ""
        except Exception as exc:
            return False, f"prompt_send_failed:{exc}"

    async def _response_probe(self, page: Any, *, prompt_text: str) -> dict[str, Any]:
        js = """
        (args) => {
            const promptText = args && args.promptText ? args.promptText : '';
            const loadingSelectors = (args && Array.isArray(args.loadingSelectors)) ? args.loadingSelectors : [];
            const assistantSelectors = (args && Array.isArray(args.assistantSelectors)) ? args.assistantSelectors : [];

            const hasLoading = loadingSelectors.some((sel) => {
                try {
                    return !!document.querySelector(sel);
                } catch (e) {
                    return false;
                }
            });

            const codeNodes = Array.from(document.querySelectorAll('pre code, pre, code')).slice(-8);
            const codeTexts = codeNodes
                .map((n) => (n && n.innerText ? n.innerText.trim() : ''))
                .filter(Boolean);

            const assistantNodes = [];
            for (const sel of assistantSelectors) {
                try {
                    assistantNodes.push(...Array.from(document.querySelectorAll(sel)));
                } catch (e) {}
            }
            const assistantTexts = assistantNodes
                .map((n) => (n && n.innerText ? n.innerText.trim() : ''))
                .filter(Boolean)
                .slice(-8);

            const dedupe = (arr) => {
                const out = [];
                const seen = new Set();
                for (const x of arr) {
                    if (!x || seen.has(x)) continue;
                    seen.add(x);
                    out.push(x);
                }
                return out;
            };

            const codeUnique = dedupe(codeTexts);
            const assistantUnique = dedupe(assistantTexts);
            const genericLast = (assistantUnique.length ? assistantUnique[assistantUnique.length - 1] : '');
            let mainText = '';
            try {
                const main = document.querySelector('main') || document.body;
                mainText = main && main.innerText ? String(main.innerText || '').trim() : '';
            } catch (e) {
                mainText = '';
            }
            const maxTail = 20000;
            const mainTail = (mainText && mainText.length > maxTail) ? mainText.slice(-maxTail) : mainText;
            return {
                loading: hasLoading,
                code_blocks: codeUnique,
                assistant_texts: assistantUnique,
                generic_last: genericLast,
                main_tail: mainTail,
                prompt_echo: promptText,
            };
        }
        """
        return await page.evaluate(
            js,
            {
                "promptText": prompt_text,
                "loadingSelectors": self.loading_selectors,
                "assistantSelectors": self.assistant_selectors,
            },
        )

    @staticmethod
    def _pick_response_text(probe: dict[str, Any], *, prompt_text: str) -> tuple[str, str]:
        prompt_norm = str(prompt_text or "").strip()

        def _strip_prompt_echo_prefix(candidate: str) -> str:
            c = str(candidate or "").strip()
            if not c:
                return ""
            if not prompt_norm:
                return c
            if c == prompt_norm:
                return ""
            if c.startswith(prompt_norm):
                rest = c[len(prompt_norm):].strip()
                return rest
            if prompt_norm in c:
                idx = c.rfind(prompt_norm)
                rest = c[idx + len(prompt_norm):].strip()
                if rest:
                    return rest
            return c

        for txt in reversed(list(probe.get("code_blocks") or [])):
            s = _strip_prompt_echo_prefix(txt)
            if s:
                return s, "code_block"
        for txt in reversed(list(probe.get("assistant_texts") or [])):
            s = _strip_prompt_echo_prefix(txt)
            if s:
                return s, "assistant"
        generic = str(probe.get("generic_last") or "").strip()
        generic = _strip_prompt_echo_prefix(generic)
        if generic:
            return generic, "generic"
        tail = str(probe.get("main_tail") or "").strip()
        tail = _strip_prompt_echo_prefix(tail)
        if tail:
            return tail, "main_tail"
        return "", ""

    def _infer_trace_dir(self, *, health_file: Path, job_id: str) -> Path | None:
        if not _truthy_env("OPENCLAW_WEB_GATEWAY_TRACE", "1"):
            return None
        job_norm = (job_id or "").strip()
        if not job_norm:
            return None
        try:
            # Dispatcher passes health_file inside <work_root>/.system/...
            work_root = health_file.expanduser().resolve().parent.parent
            return (
                work_root
                / "Translated -EN"
                / "_VERIFY"
                / job_norm
                / ".system"
                / "web_calls"
                / self.state.provider
            )
        except Exception:
            return None

    async def _gc_job_pages(self) -> None:
        """Best-effort cleanup for per-job persistent pages."""
        if not self._job_pages:
            return
        now = time.time()
        ttl_seconds = max(60, int(os.getenv("OPENCLAW_WEB_SESSION_TTL_SECONDS", "7200") or 7200))
        max_jobs = max(1, int(os.getenv("OPENCLAW_WEB_SESSION_MAX_JOBS", "6") or 6))

        # Drop closed/expired pages first.
        for job_id, page in list(self._job_pages.items()):
            last_active = float(self._job_last_active.get(job_id, 0.0) or 0.0)
            expired = bool(last_active and (now - last_active) > float(ttl_seconds))
            closed = False
            try:
                closed = bool(getattr(page, "is_closed")() if callable(getattr(page, "is_closed", None)) else False)
            except Exception:
                closed = True
            if expired or closed:
                try:
                    await page.close()
                except Exception:
                    pass
                self._job_pages.pop(job_id, None)
                self._job_chat_url.pop(job_id, None)
                self._job_last_active.pop(job_id, None)

        # If still too many, evict least recently used.
        if len(self._job_pages) > max_jobs:
            ordered = sorted(self._job_last_active.items(), key=lambda kv: float(kv[1] or 0.0))
            overflow = len(self._job_pages) - max_jobs
            for job_id, _ in ordered[:overflow]:
                page = self._job_pages.pop(job_id, None)
                try:
                    if page is not None:
                        await page.close()
                except Exception:
                    pass
                self._job_chat_url.pop(job_id, None)
                self._job_last_active.pop(job_id, None)

    async def _complete_via_browser(
        self,
        prompt: str,
        *,
        timeout_seconds: int,
        new_chat: bool,
        job_id: str,
        session_mode: str,
        trace_dir: Path | None,
        operation_id: str,
    ) -> dict[str, Any]:
        for attempt in range(2):
            ok, err = await self._ensure_browser()
            if not ok:
                return {"ok": False, "error": "gateway_unavailable", "detail": err}

            assert self._context is not None
            session_mode_norm = str(session_mode or "").strip().lower()
            job_norm = str(job_id or "").strip()
            per_job = (not new_chat) and bool(job_norm) and session_mode_norm in {"per_job", "per-job"}

            page = None
            if per_job:
                await self._gc_job_pages()
                page = self._job_pages.get(job_norm)
                try:
                    if page is not None and callable(getattr(page, "is_closed", None)) and page.is_closed():
                        page = None
                except Exception:
                    page = None
                if page is None:
                    page = await self._context.new_page()
                    self._job_pages[job_norm] = page
                self._job_last_active[job_norm] = time.time()
            else:
                page = await self._context.new_page() if new_chat else self._primary_page
                if page is None:
                    page = await self._context.new_page()

            try:
                current_url = ""
                try:
                    current_url = str(getattr(page, "url", "") or "")
                except Exception:
                    current_url = ""

                # Session navigation strategy:
                # - per_request: always navigate to home_url (fresh chat context).
                # - per_job: reuse the existing conversation page for this job; only navigate when
                #   the page is new/blank, on a login page, or when we have a saved chat URL.
                if not per_job:
                    await page.goto(self.state.home_url, wait_until="domcontentloaded", timeout=45000)
                    await page.wait_for_timeout(600)
                else:
                    chat_url = str(self._job_chat_url.get(job_norm, "") or "").strip()
                    if chat_url and current_url != chat_url:
                        await page.goto(chat_url, wait_until="domcontentloaded", timeout=45000)
                        await page.wait_for_timeout(600)
                    elif (not current_url) or current_url.startswith("about:") or self._is_login_page(current_url):
                        await page.goto(self.state.home_url, wait_until="domcontentloaded", timeout=45000)
                        await page.wait_for_timeout(600)
                    else:
                        # Already on a stable page for this job; avoid reload to keep the
                        # web UI conversation "in one thread".
                        await page.wait_for_timeout(200)

                sent, send_err = await self._prompt_send(page, prompt)
                if not sent:
                    # Self-heal for persistent pages: fall back to the provider home page once.
                    if per_job:
                        try:
                            await page.goto(self.state.home_url, wait_until="domcontentloaded", timeout=45000)
                            await page.wait_for_timeout(600)
                            sent2, send_err2 = await self._prompt_send(page, prompt)
                            if sent2:
                                sent = True
                            else:
                                send_err = send_err2 or send_err
                        except Exception:
                            pass
                    try:
                        if new_chat:
                            await page.close()
                    except Exception:
                        pass
                    return {"ok": False, "error": "gateway_bad_payload", "detail": send_err}

                deadline = time.time() + max(15, int(timeout_seconds))
                stable_hits = 0
                last_text = ""
                last_method = ""
                last_probe: dict[str, Any] = {}

                while time.time() < deadline:
                    try:
                        probe = await self._response_probe(page, prompt_text=prompt)
                    except Exception as exc:
                        if self._is_target_closed_error(exc):
                            raise
                        probe = {"loading": True, "code_blocks": [], "assistant_texts": [], "generic_last": ""}
                    last_probe = probe
                    loading = bool(probe.get("loading", False))
                    candidate, method = self._pick_response_text(probe, prompt_text=prompt)
                    if candidate and candidate == last_text:
                        stable_hits += 1
                    elif candidate:
                        stable_hits = 0
                        last_text = candidate
                        last_method = method

                    if candidate and (not loading) and stable_hits >= 2:
                        break
                    await asyncio.sleep(0.7)

                screenshot_path = ""
                if trace_dir:
                    try:
                        trace_dir.mkdir(parents=True, exist_ok=True)
                        screenshot = trace_dir / f"{int(time.time())}_{operation_id}.png"
                        await page.screenshot(path=str(screenshot))
                        screenshot_path = str(screenshot)
                    except Exception:
                        screenshot_path = ""

                url = str(getattr(page, "url", "") or "")
                self.state.last_url = url
                if per_job and job_norm and url and not self._is_login_page(url):
                    self._job_last_active[job_norm] = time.time()
                    # Prefer remembering a concrete conversation URL when available.
                    if url != str(self.state.home_url):
                        self._job_chat_url[job_norm] = url
                try:
                    if new_chat:
                        await page.close()
                except Exception:
                    pass

                if last_text:
                    return {
                        "ok": True,
                        "text": last_text,
                        "extract_method": last_method or "timeout_last",
                        "probe": {"loading": False, "stable_hits": stable_hits, "raw": last_probe},
                        "url": url,
                        "screenshot_path": screenshot_path,
                    }
                return {"ok": False, "error": "gateway_timeout", "detail": "response_timeout_no_extractable_text"}
            except Exception as exc:
                try:
                    if new_chat:
                        await page.close()
                except Exception:
                    pass
                if attempt == 0 and self._is_target_closed_error(exc):
                    await self._reset_browser()
                    continue
                return {"ok": False, "error": "gateway_unavailable", "detail": f"gateway_runtime_failed:{exc}"}

        return {"ok": False, "error": "gateway_unavailable", "detail": "gateway_runtime_failed:unknown"}

    async def completion(self, payload: dict[str, Any], *, health_file: Path) -> dict[str, Any]:
        messages = payload.get("messages")
        if not isinstance(messages, list) or not messages:
            return {"ok": False, "error": "gateway_bad_payload", "detail": "messages required"}
        if payload.get("stream") not in (None, False):
            return {"ok": False, "error": "gateway_bad_payload", "detail": "stream=true not supported"}

        prompt = _join_prompt_from_messages(messages)
        if not prompt:
            return {"ok": False, "error": "gateway_bad_payload", "detail": "empty prompt"}

        format_contract = payload.get("format_contract") if isinstance(payload.get("format_contract"), dict) else None
        strict_mode = bool(payload.get("strict_mode", False))
        operation_id = str(payload.get("operation_id") or "").strip() or f"op_{uuid.uuid4().hex[:12]}"
        job_id = str(payload.get("job_id") or "").strip()
        round_id = payload.get("round")
        batch_id = str(payload.get("batch_id") or "").strip()

        session_mode = str(os.getenv("OPENCLAW_WEB_SESSION_MODE", "per_job")).strip().lower() or "per_job"
        new_chat = bool(payload.get("new_chat", False))
        if not new_chat:
            new_chat = session_mode in {"per_request", "per-request", "new_chat", "new-chat"}

        started_at = time.time()
        trace_dir = self._infer_trace_dir(health_file=health_file, job_id=job_id)
        requested_timeout = payload.get("timeout_seconds")
        try:
            requested_timeout_int = int(requested_timeout) if requested_timeout is not None else 0
        except Exception:
            requested_timeout_int = 0
        if requested_timeout_int > 0:
            # Keep guardrails: too small causes flakiness, too large stalls workflows.
            timeout_seconds = max(15, min(600, requested_timeout_int))
        else:
            timeout_seconds = max(30, int(os.getenv("OPENCLAW_WEB_GATEWAY_TIMEOUT_SECONDS", "180")))

        async with self._get_lock():
            require_login = str(os.getenv("OPENCLAW_WEB_GATEWAY_REQUIRE_LOGIN", "0")).strip() == "1"
            if require_login and not self.state.logged_in:
                checked = await self._check_logged_in(interactive=False)
                if not checked.get("ok"):
                    return {"ok": False, "error": "gateway_login_required", "detail": checked}

            result = await self._complete_via_browser(
                prompt,
                timeout_seconds=timeout_seconds,
                new_chat=new_chat,
                job_id=job_id,
                session_mode=session_mode,
                trace_dir=trace_dir,
                operation_id=operation_id,
            )
            if not result.get("ok"):
                self.state.healthy = False
                self.state.last_error = str(result.get("error") or "gateway_unavailable")
                return result

            raw_text = str(result.get("text") or "").strip()
            extract_method = str(result.get("extract_method") or "unknown")
            marker_validation: dict[str, Any] = {"applied": bool(format_contract), "attempts": 1, "ok": True}

            final_text = raw_text
            if format_contract:
                fmt = apply_format_contract(raw_text, format_contract)
                if fmt.get("ok"):
                    final_text = str(fmt.get("text") or "")
                    marker_validation = {"applied": True, "attempts": 1, "ok": True, "meta": fmt.get("meta") or {}}
                else:
                    repair_prompt = build_format_repair_prompt(
                        raw_text,
                        format_contract,
                        reason=str(fmt.get("detail") or "format_contract_failed"),
                    )
                    repaired = await self._complete_via_browser(
                        repair_prompt,
                        timeout_seconds=max(30, int(os.getenv("OPENCLAW_WEB_GATEWAY_TIMEOUT_SECONDS", "180"))),
                        new_chat=new_chat,
                        job_id=job_id,
                        session_mode=session_mode,
                        trace_dir=trace_dir,
                        operation_id=f"{operation_id}_repair",
                    )
                    if not repaired.get("ok"):
                        self.state.healthy = False
                        self.state.last_error = str(repaired.get("error") or "gateway_bad_payload")
                        return {
                            "ok": False,
                            "error": "gateway_bad_payload",
                            "detail": f"format_contract_failed:repair_request_failed:{repaired.get('detail')}",
                        }
                    fmt2 = apply_format_contract(str(repaired.get("text") or ""), format_contract)
                    if not fmt2.get("ok"):
                        self.state.healthy = False
                        self.state.last_error = "gateway_bad_payload"
                        return {"ok": False, "error": "gateway_bad_payload", "detail": f"format_contract_failed:{fmt2.get('detail')}"}
                    final_text = str(fmt2.get("text") or "")
                    marker_validation = {"applied": True, "attempts": 2, "ok": True, "meta": fmt2.get("meta") or {}}
                    extract_method = str(repaired.get("extract_method") or extract_method)

        if not final_text.strip():
            return {"ok": False, "error": "gateway_bad_payload", "detail": "empty_completion_text"}

        self.state.healthy = True
        self.state.last_error = ""
        self.state.updated_at = _utc_now()
        model = str(payload.get("model") or self.state.model or self.state.provider)
        now_ts = int(time.time())
        elapsed_ms = int((time.time() - started_at) * 1000)
        gateway_meta = {
            "provider": self.state.provider,
            "site": ("deepseek" if self.state.provider == PROVIDER_DEEPSEEK_WEB else "chatgpt"),
            "session_state": {"logged_in": bool(self.state.logged_in), "healthy": bool(self.state.healthy)},
            "extract_method": extract_method,
            "marker_validation": marker_validation,
            "elapsed_ms": elapsed_ms,
            "operation_id": operation_id,
            "job_id": job_id,
            "round": round_id,
            "batch_id": batch_id,
            "strict_mode": strict_mode,
            "page_url": str(result.get("url") or self.state.last_url or ""),
            "screenshot_path": str(result.get("screenshot_path") or ""),
        }

        if trace_dir:
            try:
                trace_dir.mkdir(parents=True, exist_ok=True)
                call_path = trace_dir / f"{int(time.time())}_{operation_id}.json"
                call_path.write_text(
                    json.dumps(
                        {
                            "ts": _utc_now(),
                            "provider": self.state.provider,
                            "job_id": job_id,
                            "operation_id": operation_id,
                            "round": round_id,
                            "batch_id": batch_id,
                            "model": model,
                            "new_chat": bool(new_chat),
                            "strict_mode": bool(strict_mode),
                            "prompt": prompt,
                            "raw_text": raw_text,
                            "final_text": final_text,
                            "meta": gateway_meta,
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
            except Exception:
                pass

        return {
            "ok": True,
            "response": {
                "id": f"chatcmpl_{uuid.uuid4().hex[:12]}",
                "object": "chat.completion",
                "created": now_ts,
                "model": model,
                "choices": [
                    {"index": 0, "message": {"role": "assistant", "content": final_text}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                "meta": {"gateway": gateway_meta},
            },
        }


def _infer_provider(request_payload: dict[str, Any], *, default_provider: str) -> str:
    raw_provider = str(request_payload.get("provider") or "").strip()
    if raw_provider:
        return raw_provider
    model = str(request_payload.get("model") or "").strip().lower()
    if "deepseek" in model:
        return PROVIDER_DEEPSEEK_WEB
    if "chatgpt" in model or "gpt" in model:
        return PROVIDER_CHATGPT_WEB
    return default_provider


class MultiWebGateway:
    def __init__(self, *, providers: dict[str, PlaywrightWebProvider], health_file: Path, version: str) -> None:
        self.providers = providers
        self.health_file = health_file
        self.version = version
        self.started_at = _utc_now()
        self._lock = threading.Lock()
        self.persist()

    def _primary_provider(self) -> str:
        return str(os.getenv("OPENCLAW_WEB_LLM_PRIMARY", PROVIDER_DEEPSEEK_WEB)).strip() or PROVIDER_DEEPSEEK_WEB

    def health(self) -> dict[str, Any]:
        providers = {k: v.state.to_dict() for k, v in self.providers.items()}
        primary = self._primary_provider()
        primary_state = providers.get(primary) if isinstance(providers.get(primary), dict) else {}
        # Keep back-compat flat fields for older callers.
        logged_in = bool(primary_state.get("logged_in", False)) if primary_state else any(
            bool(v.get("logged_in", False)) for v in providers.values()
        )
        healthy = bool(primary_state.get("healthy", False)) if primary_state else any(
            bool(v.get("healthy", False)) for v in providers.values()
        )
        return {
            "ok": True,
            "running": True,
            "healthy": healthy,
            "logged_in": logged_in,
            "primary_provider": primary,
            "started_at": self.started_at,
            "version": self.version,
            "providers": providers,
            "updated_at": _utc_now(),
        }

    def persist(self) -> None:
        with self._lock:
            self.health_file.parent.mkdir(parents=True, exist_ok=True)
            self.health_file.write_text(json.dumps(self.health(), ensure_ascii=False, indent=2), encoding="utf-8")

    def get_provider(self, provider_id: str) -> PlaywrightWebProvider | None:
        return self.providers.get(provider_id)


def build_app(gateway: MultiWebGateway) -> FastAPI:
    app = FastAPI(title="OpenClaw Web Gateway", version=gateway.version)

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        for p in gateway.providers.values():
            try:
                await p.reset_browser()
            except Exception:
                pass

    @app.get("/health")
    async def health() -> dict[str, Any]:
        gateway.persist()
        return gateway.health()

    @app.get("/session")
    async def session() -> dict[str, Any]:
        gateway.persist()
        return {"ok": True, "providers": {k: v.state.to_dict() for k, v in gateway.providers.items()}}

    @app.get("/session/diagnose")
    async def session_diagnose(provider: str = "") -> dict[str, Any]:
        provider_id = (provider or "").strip()
        if not provider_id:
            pids = list(gateway.providers.keys())
            results = await asyncio.gather(
                *[gateway.providers[pid].diagnose_session() for pid in pids],
                return_exceptions=True,
            )
            out: dict[str, Any] = {}
            for pid, res in zip(pids, results):
                if isinstance(res, BaseException):
                    out[pid] = {"ok": False, "logged_in": False, "error": f"gateway_diagnose_failed:{res}", "selector_probe": {}}
                else:
                    out[pid] = res
            return {"ok": True, "providers": out}
        p = gateway.get_provider(provider_id)
        if not p:
            return {"ok": False, "error": "unknown_provider", "provider": provider_id}
        result = await p.diagnose_session()
        return {"ok": bool(result.get("ok")), "provider": provider_id, "result": result, "state": p.state.to_dict()}

    @app.post("/session/login")
    async def session_login(payload: dict[str, Any] | None = None) -> dict[str, Any]:
        req = payload or {}
        provider_id = str(req.get("provider") or "").strip() or gateway._primary_provider()
        interactive = bool(req.get("interactive", False))
        timeout_seconds = int(req.get("timeout_seconds") or 15)
        p = gateway.get_provider(provider_id)
        if not p:
            return {"ok": False, "error": "unknown_provider", "provider": provider_id}
        result = await p.check_logged_in(interactive=interactive, timeout_seconds=timeout_seconds)
        gateway.persist()
        return {"ok": bool(result.get("ok")), "provider": provider_id, "result": result, "state": p.state.to_dict()}

    @app.post("/v1/chat/completions")
    async def chat_completions(payload: dict[str, Any]) -> JSONResponse:
        provider_id = _infer_provider(payload, default_provider=gateway._primary_provider())
        p = gateway.get_provider(provider_id)
        if not p:
            return JSONResponse(
                status_code=400,
                content={"error": {"type": "gateway_bad_payload", "message": f"unknown provider: {provider_id}"}},
            )

        result = await p.completion(payload, health_file=gateway.health_file)
        gateway.persist()
        if not result.get("ok"):
            error = str(result.get("error") or "gateway_unavailable")
            status_code = 503 if error in {"gateway_unavailable", "gateway_timeout"} else 400
            return JSONResponse(
                status_code=status_code,
                content={"error": {"type": error, "message": str(result.get("detail") or error)}},
            )
        return JSONResponse(status_code=200, content=result["response"])

    return app


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="OpenClaw Web LLM Gateway (DeepSeek/ChatGPT)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--base-url", default=os.getenv("OPENCLAW_WEB_GATEWAY_BASE_URL", "http://127.0.0.1:8765"))
    parser.add_argument("--model", default=os.getenv("OPENCLAW_WEB_GATEWAY_MODEL", "web-llm"))
    parser.add_argument(
        "--profiles-dir",
        default=str(
            Path(os.getenv("OPENCLAW_WEB_GATEWAY_PROFILES_DIR", "~/.openclaw/runtime/translation/web-profiles")).expanduser()
        ),
        help="Base dir for provider browser profiles (cookies/sessions).",
    )
    # Backward compatible alias (older dispatcher used --profile-dir).
    parser.add_argument("--profile-dir", default="")
    parser.add_argument(
        "--health-file",
        default=str(Path("~/.openclaw/runtime/translation/web_gateway_health.json").expanduser()),
    )
    parser.add_argument("--headless", action="store_true")
    return parser.parse_args()

def _default_browser_channel() -> str:
    override = str(os.getenv("OPENCLAW_WEB_GATEWAY_BROWSER_CHANNEL", "")).strip()
    if override:
        return override
    # Some sites are stricter in Playwright-managed Chromium; on macOS we can
    # often improve compatibility by using the system Chrome channel.
    try:
        if sys.platform == "darwin" and Path("/Applications/Google Chrome.app").exists():
            return "chrome"
    except Exception:
        pass
    return ""


def main() -> int:
    args = parse_args()
    health_file = Path(args.health_file).expanduser().resolve()
    base_url = str(args.base_url).strip()
    model = str(args.model).strip() or "web-llm"
    headless = bool(args.headless)
    browser_channel = _default_browser_channel()

    base_profiles = str(args.profiles_dir or "").strip()
    if not base_profiles and str(args.profile_dir or "").strip():
        base_profiles = str(args.profile_dir).strip()
    profiles_dir = Path(base_profiles or "~/.openclaw/runtime/translation/web-profiles").expanduser().resolve()
    profiles_dir.mkdir(parents=True, exist_ok=True)

    providers: dict[str, PlaywrightWebProvider] = {
        PROVIDER_DEEPSEEK_WEB: PlaywrightWebProvider(
            provider=PROVIDER_DEEPSEEK_WEB,
            model=model,
            base_url=base_url,
            home_url=DEEPSEEK_HOME_URL,
            profile_dir=profiles_dir / PROVIDER_DEEPSEEK_WEB,
            headless=headless,
            browser_channel=browser_channel,
            prompt_selectors=DEEPSEEK_PROMPT_SELECTORS,
            send_selectors=DEEPSEEK_SEND_SELECTORS,
            loading_selectors=DEEPSEEK_LOADING_SELECTORS,
            assistant_selectors=DEEPSEEK_ASSISTANT_SELECTORS,
            login_url_tokens=["/login", "login", "/signin", "signin", "auth", "oauth"],
        ),
        PROVIDER_CHATGPT_WEB: PlaywrightWebProvider(
            provider=PROVIDER_CHATGPT_WEB,
            model=model,
            base_url=base_url,
            home_url=CHATGPT_HOME_URL,
            profile_dir=profiles_dir / PROVIDER_CHATGPT_WEB,
            headless=headless,
            browser_channel=browser_channel,
            prompt_selectors=CHATGPT_PROMPT_SELECTORS,
            send_selectors=CHATGPT_SEND_SELECTORS,
            loading_selectors=CHATGPT_LOADING_SELECTORS,
            assistant_selectors=CHATGPT_ASSISTANT_SELECTORS,
            login_url_tokens=["/auth", "login", "signin"],
        ),
    }

    gateway = MultiWebGateway(providers=providers, health_file=health_file, version="0.2.0")
    app = build_app(gateway)
    uvicorn.run(app, host=str(args.host), port=int(args.port), log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
