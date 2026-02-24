#!/usr/bin/env python3
"""OpenClaw web gateway service (ChatGPT route, OpenAI-compatible surface)."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse
import uvicorn

CHATGPT_HOME_URL = "https://chatgpt.com/"


def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


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


def _extract_openclaw_text(stdout: str) -> str:
    text = str(stdout or "").strip()
    if not text:
        return ""
    decoder = json.JSONDecoder()
    parsed_values: list[Any] = []
    for idx, ch in enumerate(text):
        if ch not in "{[":
            continue
        try:
            value, _end = decoder.raw_decode(text[idx:])
        except json.JSONDecodeError:
            continue
        parsed_values.append(value)
        if len(parsed_values) >= 6:
            break
    for value in parsed_values:
        if isinstance(value, dict):
            for container in (value.get("result"), value):
                if not isinstance(container, dict):
                    continue
                payloads = container.get("payloads")
                if isinstance(payloads, list):
                    for payload in payloads:
                        if isinstance(payload, dict) and isinstance(payload.get("text"), str):
                            content = str(payload["text"]).strip()
                            if content:
                                return content
                direct = container.get("text")
                if isinstance(direct, str) and direct.strip():
                    return direct.strip()
    return text


@dataclass
class GatewayState:
    model: str
    base_url: str
    health_file: Path
    profile_dir: Path
    running: bool = True
    healthy: bool = False
    logged_in: bool = False
    last_error: str = ""
    updated_at: str = field(default_factory=_utc_now)
    session_checked_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "running": bool(self.running),
            "healthy": bool(self.healthy),
            "logged_in": bool(self.logged_in),
            "base_url": self.base_url,
            "model": self.model,
            "last_error": self.last_error,
            "updated_at": self.updated_at,
            "session_checked_at": self.session_checked_at,
            "profile_dir": str(self.profile_dir),
        }

    def persist(self) -> None:
        self.updated_at = _utc_now()
        self.health_file.parent.mkdir(parents=True, exist_ok=True)
        self.health_file.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")


class ChatGPTWebGateway:
    def __init__(
        self,
        *,
        model: str,
        base_url: str,
        health_file: Path,
        profile_dir: Path,
        headless: bool,
    ) -> None:
        self.state = GatewayState(
            model=model,
            base_url=base_url,
            health_file=health_file,
            profile_dir=profile_dir,
        )
        self.headless = headless
        self._playwright = None
        self._context = None
        self._page = None
        self.state.persist()

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
            self._page = self._context.pages[0] if self._context.pages else self._context.new_page()
            return True, ""
        except Exception as exc:
            return False, f"playwright_init_failed:{exc}"

    def _check_logged_in(self, *, interactive: bool = False) -> dict[str, Any]:
        ok, err = self._ensure_browser()
        if not ok:
            self.state.logged_in = False
            self.state.healthy = False
            self.state.last_error = err
            self.state.session_checked_at = _utc_now()
            self.state.persist()
            return {"ok": False, "error": err}

        try:
            assert self._page is not None
            self._page.goto(CHATGPT_HOME_URL, wait_until="domcontentloaded", timeout=45000)
            self._page.wait_for_timeout(1500)

            if interactive and not self.headless:
                # Give operator a short interactive window to complete login manually.
                self._page.wait_for_timeout(15000)

            selector_candidates = [
                "textarea#prompt-textarea",
                "textarea[data-id='prompt-textarea']",
                "div[contenteditable='true'][data-id='prompt-textarea']",
            ]
            logged_in = False
            for sel in selector_candidates:
                loc = self._page.locator(sel).first
                if loc.count() > 0:
                    logged_in = True
                    break
            if "/auth" in str(self._page.url):
                logged_in = False

            self.state.logged_in = bool(logged_in)
            self.state.healthy = bool(logged_in) or bool(os.getenv("OPENCLAW_WEB_GATEWAY_REQUIRE_LOGIN", "0") != "1")
            self.state.last_error = "" if logged_in else "gateway_login_required"
            self.state.session_checked_at = _utc_now()
            self.state.persist()
            return {"ok": logged_in, "logged_in": logged_in, "url": str(self._page.url)}
        except Exception as exc:
            self.state.logged_in = False
            self.state.healthy = False
            self.state.last_error = f"gateway_login_check_failed:{exc}"
            self.state.session_checked_at = _utc_now()
            self.state.persist()
            return {"ok": False, "error": self.state.last_error}

    def _complete_via_openclaw(self, prompt: str) -> dict[str, Any]:
        openclaw_bin = str(os.getenv("OPENCLAW_BIN") or "openclaw").strip()
        agent = str(os.getenv("OPENCLAW_WEB_GATEWAY_AGENT", "translator-core")).strip()
        cmd = [
            openclaw_bin,
            "run",
            "--agent",
            agent,
            "--prompt",
            prompt,
            "--json",
        ]
        proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
        if proc.returncode != 0:
            stderr = str(proc.stderr or "").strip()
            stdout = str(proc.stdout or "").strip()
            return {
                "ok": False,
                "error": "gateway_unavailable",
                "detail": stderr or stdout or f"openclaw_exit_{proc.returncode}",
            }
        text = _extract_openclaw_text(proc.stdout)
        if not text:
            return {"ok": False, "error": "gateway_bad_payload", "detail": "empty_completion_text"}
        return {"ok": True, "text": text}

    def completion(self, payload: dict[str, Any]) -> dict[str, Any]:
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
        result = self._complete_via_openclaw(prompt)
        if not result.get("ok"):
            self.state.healthy = False
            self.state.last_error = str(result.get("error") or "gateway_unavailable")
            self.state.persist()
            return result

        self.state.healthy = True
        self.state.last_error = ""
        self.state.persist()
        model = str(payload.get("model") or self.state.model or "chatgpt-web")
        now_ts = int(time.time())
        return {
            "ok": True,
            "response": {
                "id": f"chatcmpl_{uuid.uuid4().hex[:12]}",
                "object": "chat.completion",
                "created": now_ts,
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": str(result.get("text") or "")},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            },
        }


def build_app(gateway: ChatGPTWebGateway) -> FastAPI:
    app = FastAPI(title="OpenClaw Web Gateway", version="0.1.0")

    @app.get("/health")
    def health() -> dict[str, Any]:
        gateway.state.persist()
        return gateway.state.to_dict()

    @app.get("/session")
    def session() -> dict[str, Any]:
        return {
            "ok": True,
            "logged_in": gateway.state.logged_in,
            "session_checked_at": gateway.state.session_checked_at,
            "profile_dir": str(gateway.state.profile_dir),
        }

    @app.post("/session/login")
    def session_login(payload: dict[str, Any] | None = None) -> dict[str, Any]:
        req = payload or {}
        interactive = bool(req.get("interactive", False))
        result = gateway._check_logged_in(interactive=interactive)
        return {"ok": bool(result.get("ok")), "result": result, "state": gateway.state.to_dict()}

    @app.post("/v1/chat/completions")
    def chat_completions(payload: dict[str, Any]) -> JSONResponse:
        result = gateway.completion(payload)
        if not result.get("ok"):
            error = str(result.get("error") or "gateway_unavailable")
            status_code = 503 if error in {"gateway_unavailable", "gateway_timeout"} else 400
            return JSONResponse(
                status_code=status_code,
                content={
                    "error": {
                        "type": error,
                        "message": str(result.get("detail") or error),
                    }
                },
            )
        return JSONResponse(status_code=200, content=result["response"])

    return app


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="OpenClaw ChatGPT web gateway")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--base-url", default=os.getenv("OPENCLAW_WEB_GATEWAY_BASE_URL", "http://127.0.0.1:8765"))
    parser.add_argument("--model", default=os.getenv("OPENCLAW_WEB_GATEWAY_MODEL", "chatgpt-web"))
    parser.add_argument(
        "--profile-dir",
        default=str(Path("~/.openclaw/runtime/translation/web-gateway-profile").expanduser()),
    )
    parser.add_argument(
        "--health-file",
        default=str(Path("~/.openclaw/runtime/translation/web_gateway_health.json").expanduser()),
    )
    parser.add_argument("--headless", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    gateway = ChatGPTWebGateway(
        model=str(args.model).strip() or "chatgpt-web",
        base_url=str(args.base_url).strip(),
        health_file=Path(args.health_file).expanduser().resolve(),
        profile_dir=Path(args.profile_dir).expanduser().resolve(),
        headless=bool(args.headless),
    )
    app = build_app(gateway)
    uvicorn.run(app, host=str(args.host), port=int(args.port), log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
