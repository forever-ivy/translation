#!/usr/bin/env python3
"""OpenClaw Web LLM Gateway (Playwright-driven, OpenAI-compatible surface).

This service drives real web UIs (Gemini/ChatGPT) and exposes a small HTTP API:
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

import argparse
import json
import os
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

PROVIDER_GEMINI_WEB = "gemini_web"
PROVIDER_CHATGPT_WEB = "chatgpt_web"

GEMINI_HOME_URL = "https://gemini.google.com/app"
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

GEMINI_PROMPT_SELECTORS = [
    "div[contenteditable='true'][aria-label*='Enter a prompt']",
    "div[contenteditable='true'][aria-label*='enter a prompt']",
    "div[contenteditable='true'][aria-label*='Message']",
    "div[contenteditable='true'][aria-label*='message']",
    "textarea[aria-label*='prompt']",
    "textarea",
    "div[contenteditable='true']",
]
GEMINI_SEND_SELECTORS = [
    "button[aria-label*='Send']",
    "button[aria-label*='send']",
    "button[type='submit']",
]
GEMINI_LOADING_SELECTORS = [
    "button[aria-label*='Stop']",
    "button[aria-label*='stop']",
    "button[aria-label*='Cancel']",
    "button[aria-label*='cancel']",
    "mat-spinner",
    "div[class*='typing']",
]
GEMINI_ASSISTANT_SELECTORS = [
    "[data-test-id*='response']",
    "[data-testid*='response']",
    "[class*='model-response']",
    "[class*='response-content']",
    "main [class*='response']",
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
        self.prompt_selectors = list(prompt_selectors)
        self.send_selectors = list(send_selectors)
        self.loading_selectors = list(loading_selectors)
        self.assistant_selectors = list(assistant_selectors)
        self.login_url_tokens = [str(t) for t in (login_url_tokens or []) if str(t).strip()]
        self._playwright = None
        self._context = None
        self._primary_page = None
        self._lock = threading.Lock()

    def _ensure_browser(self) -> tuple[bool, str]:
        if self._context is not None:
            return True, ""
        try:
            from playwright.sync_api import sync_playwright  # lazy import

            self._playwright = sync_playwright().start()
            self.state.profile_dir.mkdir(parents=True, exist_ok=True)
            self._context = self._playwright.chromium.launch_persistent_context(
                user_data_dir=str(self.state.profile_dir),
                headless=self.headless,
            )
            self._primary_page = self._context.pages[0] if self._context.pages else self._context.new_page()
            return True, ""
        except Exception as exc:
            return False, f"playwright_init_failed:{exc}"

    def _is_login_page(self, url: str) -> bool:
        u = str(url or "").lower()
        return any(token.lower() in u for token in self.login_url_tokens)

    @staticmethod
    def _pick_first_visible_on_page(page: Any, selectors: list[str]):
        for sel in selectors:
            try:
                loc = page.locator(sel).first
                if loc.count() > 0 and loc.is_visible():
                    return sel, loc
            except Exception:
                continue
        return None, None

    def _check_logged_in(self, *, interactive: bool = False, timeout_seconds: int = 15) -> dict[str, Any]:
        ok, err = self._ensure_browser()
        if not ok:
            self.state.logged_in = False
            self.state.healthy = False
            self.state.last_error = err
            self.state.session_checked_at = _utc_now()
            return {"ok": False, "error": err}

        try:
            assert self._primary_page is not None
            self._primary_page.goto(self.state.home_url, wait_until="domcontentloaded", timeout=45000)
            self._primary_page.wait_for_timeout(1500)

            if interactive and not self.headless:
                self._primary_page.wait_for_timeout(max(1000, int(timeout_seconds * 1000)))

            sel, loc = self._pick_first_visible_on_page(self._primary_page, self.prompt_selectors)
            logged_in = bool(sel and loc and loc.count() > 0 and not self._is_login_page(str(self._primary_page.url)))

            self.state.logged_in = bool(logged_in)
            self.state.healthy = bool(logged_in) or bool(os.getenv("OPENCLAW_WEB_GATEWAY_REQUIRE_LOGIN", "0") != "1")
            self.state.last_error = "" if logged_in else "gateway_login_required"
            self.state.session_checked_at = _utc_now()
            self.state.last_url = str(self._primary_page.url)
            return {"ok": logged_in, "logged_in": logged_in, "selector": sel or "", "url": str(self._primary_page.url)}
        except Exception as exc:
            self.state.logged_in = False
            self.state.healthy = False
            self.state.last_error = f"gateway_login_check_failed:{exc}"
            self.state.session_checked_at = _utc_now()
            return {"ok": False, "error": self.state.last_error}

    def _diagnose_session(self) -> dict[str, Any]:
        ok, err = self._ensure_browser()
        if not ok:
            return {"ok": False, "error": err, "logged_in": False, "selector_probe": {}}

        assert self._primary_page is not None
        try:
            self._primary_page.goto(self.state.home_url, wait_until="domcontentloaded", timeout=45000)
            self._primary_page.wait_for_timeout(1200)
            selector_probe: dict[str, bool] = {}
            for sel in self.prompt_selectors + self.send_selectors + self.loading_selectors:
                try:
                    selector_probe[sel] = bool(self._primary_page.locator(sel).count() > 0)
                except Exception:
                    selector_probe[sel] = False
            login = self._check_logged_in(interactive=False)
            return {
                "ok": bool(login.get("ok", False)),
                "logged_in": bool(login.get("logged_in", False)),
                "url": str(self._primary_page.url),
                "selector_probe": selector_probe,
                "last_error": "" if login.get("ok") else str(login.get("error") or "gateway_login_required"),
            }
        except Exception as exc:
            return {"ok": False, "logged_in": False, "error": f"gateway_diagnose_failed:{exc}", "selector_probe": {}}

    def _prompt_send(self, page: Any, prompt: str) -> tuple[bool, str]:
        sel, box = self._pick_first_visible_on_page(page, self.prompt_selectors)
        if not sel or box is None:
            return False, "prompt_selector_missing"
        try:
            box.click(timeout=5000)
            box.evaluate(
                """
                (el, value) => {
                    const tag = (el.tagName || '').toLowerCase();
                    if (tag === 'textarea' || tag === 'input') {
                        el.value = value;
                    } else {
                        el.textContent = value;
                    }
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                }
                """,
                prompt,
            )
            time.sleep(0.15)

            _, send_btn = self._pick_first_visible_on_page(page, self.send_selectors)
            if send_btn is not None:
                send_btn.click(timeout=5000)
            else:
                box.press("Enter", timeout=5000)
            return True, ""
        except Exception as exc:
            return False, f"prompt_send_failed:{exc}"

    def _response_probe(self, page: Any, *, prompt_text: str) -> dict[str, Any]:
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
            return {
                loading: hasLoading,
                code_blocks: codeUnique,
                assistant_texts: assistantUnique,
                generic_last: genericLast,
                prompt_echo: promptText,
            };
        }
        """
        return page.evaluate(
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

        def _is_prompt_echo(candidate: str) -> bool:
            c = str(candidate or "").strip()
            if not c:
                return True
            if not prompt_norm:
                return False
            if c == prompt_norm:
                return True
            return c.startswith(prompt_norm[: min(160, len(prompt_norm))])

        for txt in reversed(list(probe.get("code_blocks") or [])):
            s = str(txt or "").strip()
            if s and not _is_prompt_echo(s):
                return s, "code_block"
        for txt in reversed(list(probe.get("assistant_texts") or [])):
            s = str(txt or "").strip()
            if s and not _is_prompt_echo(s):
                return s, "assistant"
        generic = str(probe.get("generic_last") or "").strip()
        if generic and not _is_prompt_echo(generic):
            return generic, "generic"
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

    def _complete_via_browser(
        self,
        prompt: str,
        *,
        timeout_seconds: int,
        new_chat: bool,
        trace_dir: Path | None,
        operation_id: str,
    ) -> dict[str, Any]:
        ok, err = self._ensure_browser()
        if not ok:
            return {"ok": False, "error": "gateway_unavailable", "detail": err}

        assert self._context is not None
        page = self._context.new_page() if new_chat else self._primary_page
        if page is None:
            page = self._context.new_page()
        try:
            page.goto(self.state.home_url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(600)
        except Exception as exc:
            try:
                if new_chat:
                    page.close()
            except Exception:
                pass
            return {"ok": False, "error": "gateway_unavailable", "detail": f"gateway_navigate_failed:{exc}"}

        sent, send_err = self._prompt_send(page, prompt)
        if not sent:
            try:
                if new_chat:
                    page.close()
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
                probe = self._response_probe(page, prompt_text=prompt)
            except Exception:
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
            time.sleep(0.7)

        screenshot_path = ""
        if trace_dir:
            try:
                trace_dir.mkdir(parents=True, exist_ok=True)
                screenshot = trace_dir / f"{int(time.time())}_{operation_id}.png"
                page.screenshot(path=str(screenshot))
                screenshot_path = str(screenshot)
            except Exception:
                screenshot_path = ""

        url = str(getattr(page, "url", "") or "")
        self.state.last_url = url
        try:
            if new_chat:
                page.close()
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

    def completion(self, payload: dict[str, Any], *, health_file: Path) -> dict[str, Any]:
        messages = payload.get("messages")
        if not isinstance(messages, list) or not messages:
            return {"ok": False, "error": "gateway_bad_payload", "detail": "messages required"}
        if payload.get("stream") not in (None, False):
            return {"ok": False, "error": "gateway_bad_payload", "detail": "stream=true not supported"}

        require_login = str(os.getenv("OPENCLAW_WEB_GATEWAY_REQUIRE_LOGIN", "0")).strip() == "1"
        if require_login and not self.state.logged_in:
            checked = self._check_logged_in(interactive=False)
            if not checked.get("ok"):
                return {"ok": False, "error": "gateway_login_required", "detail": checked}

        prompt = _join_prompt_from_messages(messages)
        if not prompt:
            return {"ok": False, "error": "gateway_bad_payload", "detail": "empty prompt"}

        format_contract = payload.get("format_contract") if isinstance(payload.get("format_contract"), dict) else None
        strict_mode = bool(payload.get("strict_mode", False))
        operation_id = str(payload.get("operation_id") or "").strip() or f"op_{uuid.uuid4().hex[:12]}"
        job_id = str(payload.get("job_id") or "").strip()
        round_id = payload.get("round")
        batch_id = str(payload.get("batch_id") or "").strip()

        new_chat = bool(payload.get("new_chat", False))
        if not new_chat:
            session_mode = str(os.getenv("OPENCLAW_WEB_SESSION_MODE", "per_request")).strip().lower() or "per_request"
            new_chat = session_mode in {"per_request", "per-request", "new_chat", "new-chat"}

        started_at = time.time()
        trace_dir = self._infer_trace_dir(health_file=health_file, job_id=job_id)

        with self._lock:
            result = self._complete_via_browser(
                prompt,
                timeout_seconds=max(30, int(os.getenv("OPENCLAW_WEB_GATEWAY_TIMEOUT_SECONDS", "180"))),
                new_chat=new_chat,
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
                    repaired = self._complete_via_browser(
                        repair_prompt,
                        timeout_seconds=max(30, int(os.getenv("OPENCLAW_WEB_GATEWAY_TIMEOUT_SECONDS", "180"))),
                        new_chat=new_chat,
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
            "site": ("gemini" if self.state.provider == PROVIDER_GEMINI_WEB else "chatgpt"),
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
    if "gemini" in model:
        return PROVIDER_GEMINI_WEB
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
        return str(os.getenv("OPENCLAW_WEB_LLM_PRIMARY", PROVIDER_GEMINI_WEB)).strip() or PROVIDER_GEMINI_WEB

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

    @app.get("/health")
    def health() -> dict[str, Any]:
        gateway.persist()
        return gateway.health()

    @app.get("/session")
    def session() -> dict[str, Any]:
        gateway.persist()
        return {"ok": True, "providers": {k: v.state.to_dict() for k, v in gateway.providers.items()}}

    @app.get("/session/diagnose")
    def session_diagnose(provider: str = "") -> dict[str, Any]:
        provider_id = (provider or "").strip()
        if not provider_id:
            out: dict[str, Any] = {pid: p._diagnose_session() for pid, p in gateway.providers.items()}
            return {"ok": True, "providers": out}
        p = gateway.get_provider(provider_id)
        if not p:
            return {"ok": False, "error": "unknown_provider", "provider": provider_id}
        result = p._diagnose_session()
        return {"ok": bool(result.get("ok")), "provider": provider_id, "result": result, "state": p.state.to_dict()}

    @app.post("/session/login")
    def session_login(payload: dict[str, Any] | None = None) -> dict[str, Any]:
        req = payload or {}
        provider_id = str(req.get("provider") or "").strip() or gateway._primary_provider()
        interactive = bool(req.get("interactive", False))
        timeout_seconds = int(req.get("timeout_seconds") or 15)
        p = gateway.get_provider(provider_id)
        if not p:
            return {"ok": False, "error": "unknown_provider", "provider": provider_id}
        result = p._check_logged_in(interactive=interactive, timeout_seconds=timeout_seconds)
        gateway.persist()
        return {"ok": bool(result.get("ok")), "provider": provider_id, "result": result, "state": p.state.to_dict()}

    @app.post("/v1/chat/completions")
    def chat_completions(payload: dict[str, Any]) -> JSONResponse:
        provider_id = _infer_provider(payload, default_provider=gateway._primary_provider())
        p = gateway.get_provider(provider_id)
        if not p:
            return JSONResponse(
                status_code=400,
                content={"error": {"type": "gateway_bad_payload", "message": f"unknown provider: {provider_id}"}},
            )

        result = p.completion(payload, health_file=gateway.health_file)
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
    parser = argparse.ArgumentParser(description="OpenClaw Web LLM Gateway (Gemini/ChatGPT)")
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


def main() -> int:
    args = parse_args()
    health_file = Path(args.health_file).expanduser().resolve()
    base_url = str(args.base_url).strip()
    model = str(args.model).strip() or "web-llm"
    headless = bool(args.headless)

    base_profiles = str(args.profiles_dir or "").strip()
    if not base_profiles and str(args.profile_dir or "").strip():
        base_profiles = str(args.profile_dir).strip()
    profiles_dir = Path(base_profiles or "~/.openclaw/runtime/translation/web-profiles").expanduser().resolve()
    profiles_dir.mkdir(parents=True, exist_ok=True)

    providers: dict[str, PlaywrightWebProvider] = {
        PROVIDER_GEMINI_WEB: PlaywrightWebProvider(
            provider=PROVIDER_GEMINI_WEB,
            model=model,
            base_url=base_url,
            home_url=GEMINI_HOME_URL,
            profile_dir=profiles_dir / PROVIDER_GEMINI_WEB,
            headless=headless,
            prompt_selectors=GEMINI_PROMPT_SELECTORS,
            send_selectors=GEMINI_SEND_SELECTORS,
            loading_selectors=GEMINI_LOADING_SELECTORS,
            assistant_selectors=GEMINI_ASSISTANT_SELECTORS,
            login_url_tokens=["accounts.google.com", "servicelogin", "/signin"],
        ),
        PROVIDER_CHATGPT_WEB: PlaywrightWebProvider(
            provider=PROVIDER_CHATGPT_WEB,
            model=model,
            base_url=base_url,
            home_url=CHATGPT_HOME_URL,
            profile_dir=profiles_dir / PROVIDER_CHATGPT_WEB,
            headless=headless,
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

