#!/usr/bin/env python3

import asyncio
import json
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scripts.openclaw_web_gateway import MultiWebGateway, build_app


@dataclass
class FakeState:
    provider: str
    model: str = "fake"
    base_url: str = "http://127.0.0.1:8765"
    profile_dir: str = "/tmp/fake"
    home_url: str = "https://example.com/"
    running: bool = True
    healthy: bool = True
    logged_in: bool = False
    last_error: str = ""
    updated_at: str = "2026-02-25T00:00:00Z"
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


class FakeProvider:
    def __init__(self, provider_id: str) -> None:
        self.state = FakeState(provider=provider_id)

    async def check_logged_in(self, *, interactive: bool = False, timeout_seconds: int = 15) -> dict[str, Any]:
        self.state.logged_in = True
        self.state.healthy = True
        self.state.session_checked_at = "2026-02-25T00:00:01Z"
        self.state.last_url = self.state.home_url
        return {"ok": True, "logged_in": True, "selector": "fake", "url": self.state.home_url}

    async def diagnose_session(self) -> dict[str, Any]:
        return {"ok": True, "logged_in": bool(self.state.logged_in), "url": self.state.home_url, "selector_probe": {}, "last_error": ""}

    async def completion(self, payload: dict[str, Any], *, health_file: Path) -> dict[str, Any]:
        _ = payload, health_file
        return {
            "ok": True,
            "response": {
                "id": "chatcmpl_test",
                "object": "chat.completion",
                "created": 0,
                "model": "fake",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "hello"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                "meta": {"gateway": {"provider": self.state.provider}},
            },
        }

    async def reset_browser(self) -> None:
        return None


class OpenClawWebGatewayApiTest(unittest.TestCase):
    @staticmethod
    def _get_endpoint(app: Any, *, path: str, method: str) -> Any:
        for route in getattr(app, "routes", []) or []:
            if getattr(route, "path", None) == path and method.upper() in (getattr(route, "methods", set()) or set()):
                return getattr(route, "endpoint", None)
        raise AssertionError(f"Route not found: {method} {path}")

    def test_session_login_and_chat_completion(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            health_file = Path(td) / "web_gateway_health.json"
            providers = {"chatgpt_web": FakeProvider("chatgpt_web")}
            gateway = MultiWebGateway(providers=providers, health_file=health_file, version="0.0-test")  # type: ignore[arg-type]
            app = build_app(gateway)

            login = self._get_endpoint(app, path="/session/login", method="POST")
            out = asyncio.run(login(payload={"provider": "chatgpt_web", "interactive": True, "timeout_seconds": 1}))
            self.assertTrue(out.get("ok"))
            self.assertEqual(out.get("provider"), "chatgpt_web")
            self.assertTrue(bool(out.get("state", {}).get("logged_in")))

            completions = self._get_endpoint(app, path="/v1/chat/completions", method="POST")
            resp = asyncio.run(
                completions(payload={"provider": "chatgpt_web", "messages": [{"role": "user", "content": "hi"}]})
            )
            self.assertEqual(getattr(resp, "status_code", None), 200)
            body2 = json.loads(bytes(getattr(resp, "body", b"")).decode("utf-8"))
            self.assertEqual(body2["choices"][0]["message"]["content"], "hello")

    def test_unknown_provider_returns_400(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            health_file = Path(td) / "web_gateway_health.json"
            providers = {"chatgpt_web": FakeProvider("chatgpt_web")}
            gateway = MultiWebGateway(providers=providers, health_file=health_file, version="0.0-test")  # type: ignore[arg-type]
            app = build_app(gateway)

            completions = self._get_endpoint(app, path="/v1/chat/completions", method="POST")
            resp = asyncio.run(completions(payload={"provider": "nope", "messages": [{"role": "user", "content": "hi"}]}))
            self.assertEqual(getattr(resp, "status_code", None), 400)
            body = json.loads(bytes(getattr(resp, "body", b"")).decode("utf-8"))
            err = body.get("error") or {}
            self.assertEqual(err.get("type"), "gateway_bad_payload")
