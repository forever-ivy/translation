#!/usr/bin/env python3
"""Bridge helpers for ClawRAG sync/search with graceful fallback support."""

from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_CLAWRAG_BASE_URL = "http://127.0.0.1:8080"


def _request_json(
    *,
    method: str,
    url: str,
    payload: dict[str, Any] | None = None,
    timeout: int = 20,
) -> tuple[bool, int, dict[str, Any] | None, str]:
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url=url, method=method.upper(), data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=max(2, int(timeout))) as resp:
            body = resp.read().decode("utf-8", errors="ignore")
            parsed = json.loads(body) if body.strip().startswith("{") or body.strip().startswith("[") else None
            return True, int(resp.status), parsed, ""
    except urllib.error.HTTPError as exc:
        raw = ""
        try:
            raw = exc.read().decode("utf-8", errors="ignore")
        except Exception:
            pass
        parsed = None
        if raw.strip().startswith("{") or raw.strip().startswith("["):
            try:
                parsed = json.loads(raw)
            except Exception:
                parsed = None
        return False, int(exc.code), parsed, raw[:2000]
    except Exception as exc:  # pragma: no cover - network-specific
        return False, 0, None, str(exc)


def clawrag_health(*, base_url: str = DEFAULT_CLAWRAG_BASE_URL, timeout: int = 8) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}/health"
    ok, status, payload, detail = _request_json(method="GET", url=url, payload=None, timeout=timeout)
    return {
        "ok": bool(ok and status < 400),
        "status_code": status,
        "payload": payload,
        "detail": detail,
        "base_url": base_url,
    }


def clawrag_sync(
    *,
    changed_paths: list[str],
    base_url: str = DEFAULT_CLAWRAG_BASE_URL,
    collection: str = "translation-kb",
    timeout: int = 30,
) -> dict[str, Any]:
    if not changed_paths:
        return {
            "ok": True,
            "backend": "clawrag",
            "collection": collection,
            "uploaded_count": 0,
            "mode": "noop",
            "detail": "no_changed_paths",
        }

    documents = [{"path": str(Path(p).expanduser().resolve())} for p in changed_paths]
    endpoint_attempts = [
        (
            "POST",
            f"{base_url.rstrip('/')}/api/v1/rag/collections/{urllib.parse.quote(collection)}/documents/bulk",
            {"documents": documents, "upsert": True},
        ),
        (
            "POST",
            f"{base_url.rstrip('/')}/api/v1/rag/ingest",
            {"collection": collection, "documents": documents},
        ),
        (
            "POST",
            f"{base_url.rstrip('/')}/api/v1/rag/documents",
            {"collection": collection, "documents": documents},
        ),
    ]

    errors: list[dict[str, Any]] = []
    for method, url, payload in endpoint_attempts:
        ok, status, body, detail = _request_json(method=method, url=url, payload=payload, timeout=timeout)
        if ok and status < 400:
            return {
                "ok": True,
                "backend": "clawrag",
                "collection": collection,
                "uploaded_count": len(documents),
                "mode": "api",
                "endpoint": url,
                "response": body,
            }
        errors.append({"endpoint": url, "status_code": status, "detail": detail})

    return {
        "ok": False,
        "backend": "clawrag",
        "collection": collection,
        "uploaded_count": 0,
        "mode": "api_failed",
        "errors": errors,
    }


def clawrag_delete(
    *,
    removed_paths: list[str],
    base_url: str = DEFAULT_CLAWRAG_BASE_URL,
    collection: str = "translation-kb",
    timeout: int = 30,
) -> dict[str, Any]:
    if not removed_paths:
        return {
            "ok": True,
            "backend": "clawrag",
            "collection": collection,
            "deleted_count": 0,
            "mode": "noop",
            "detail": "no_removed_paths",
        }

    documents = [{"path": str(Path(p).expanduser().resolve())} for p in removed_paths]
    endpoint_attempts = [
        (
            "DELETE",
            f"{base_url.rstrip('/')}/api/v1/rag/collections/{urllib.parse.quote(collection)}/documents/bulk",
            {"documents": documents},
        ),
        (
            "POST",
            f"{base_url.rstrip('/')}/api/v1/rag/collections/{urllib.parse.quote(collection)}/documents/bulk",
            {"documents": documents, "delete": True},
        ),
        (
            "POST",
            f"{base_url.rstrip('/')}/api/v1/rag/documents/delete",
            {"collection": collection, "documents": documents},
        ),
        (
            "POST",
            f"{base_url.rstrip('/')}/api/v1/rag/collections/{urllib.parse.quote(collection)}/documents/delete",
            {"documents": documents},
        ),
    ]

    errors: list[dict[str, Any]] = []
    for method, url, payload in endpoint_attempts:
        ok, status, body, detail = _request_json(method=method, url=url, payload=payload, timeout=timeout)
        if ok and status < 400:
            return {
                "ok": True,
                "backend": "clawrag",
                "collection": collection,
                "deleted_count": len(documents),
                "mode": "api",
                "endpoint": url,
                "response": body,
            }
        errors.append({"endpoint": url, "status_code": status, "detail": detail})

    return {
        "ok": False,
        "backend": "clawrag",
        "collection": collection,
        "deleted_count": 0,
        "mode": "api_failed",
        "errors": errors,
    }


def _extract_hits(payload: Any) -> list[dict[str, Any]]:
    if payload is None:
        return []

    candidates: list[Any] = []
    if isinstance(payload, list):
        candidates = payload
    elif isinstance(payload, dict):
        for key in ("hits", "results", "data", "documents", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                candidates = value
                break
            if isinstance(value, dict):
                nested = value.get("hits") or value.get("results")
                if isinstance(nested, list):
                    candidates = nested
                    break
        if not candidates and isinstance(payload.get("result"), list):
            candidates = payload["result"]

    out: list[dict[str, Any]] = []
    for item in candidates:
        if not isinstance(item, dict):
            continue
        snippet = (
            item.get("snippet")
            or item.get("text")
            or item.get("content")
            or item.get("chunk")
            or ""
        )
        path = item.get("path") or item.get("source") or item.get("source_path") or ""
        score = item.get("score") or item.get("similarity") or item.get("rank_score") or 0.0
        source_group = item.get("source_group") or item.get("group") or "general"
        out.append(
            {
                "path": str(path),
                "source_group": str(source_group),
                "chunk_index": int(item.get("chunk_index") or item.get("index") or 0),
                "snippet": str(snippet)[:700],
                "score": float(score or 0.0),
            }
        )
    out.sort(key=lambda x: x.get("score", 0.0), reverse=True)
    return out


def clawrag_search(
    *,
    query: str,
    top_k: int = 12,
    base_url: str = DEFAULT_CLAWRAG_BASE_URL,
    collection: str = "translation-kb",
    timeout: int = 15,
) -> dict[str, Any]:
    q = (query or "").strip()
    if not q:
        return {"ok": True, "backend": "clawrag", "hits": [], "collection": collection, "detail": "empty_query"}

    endpoint_attempts = [
        (
            "POST",
            f"{base_url.rstrip('/')}/api/v1/rag/search",
            {"query": q, "top_k": max(1, int(top_k)), "collection": collection},
        ),
        (
            "POST",
            f"{base_url.rstrip('/')}/api/v1/rag/query",
            {"query": q, "k": max(1, int(top_k)), "collection": collection},
        ),
        (
            "GET",
            f"{base_url.rstrip('/')}/api/v1/rag/search?query={urllib.parse.quote(q)}&top_k={max(1, int(top_k))}&collection={urllib.parse.quote(collection)}",
            None,
        ),
    ]

    errors: list[dict[str, Any]] = []
    for method, url, payload in endpoint_attempts:
        ok, status, body, detail = _request_json(method=method, url=url, payload=payload, timeout=timeout)
        if ok and status < 400:
            hits = _extract_hits(body)
            return {
                "ok": True,
                "backend": "clawrag",
                "collection": collection,
                "hits": hits[: max(1, int(top_k))],
                "endpoint": url,
            }
        errors.append({"endpoint": url, "status_code": status, "detail": detail})

    return {
        "ok": False,
        "backend": "clawrag",
        "collection": collection,
        "hits": [],
        "errors": errors,
    }


def clawrag_delete(
    *,
    removed_paths: list[str],
    base_url: str = DEFAULT_CLAWRAG_BASE_URL,
    collection: str = "translation-kb",
    timeout: int = 30,
) -> dict[str, Any]:
    if not removed_paths:
        return {
            "ok": True,
            "backend": "clawrag",
            "collection": collection,
            "deleted_count": 0,
            "mode": "noop",
            "detail": "no_removed_paths",
        }

    documents = [{"path": str(Path(p).expanduser().resolve())} for p in removed_paths]
    endpoint_attempts = [
        (
            "DELETE",
            f"{base_url.rstrip('/')}/api/v1/rag/collections/{urllib.parse.quote(collection)}/documents",
            {"documents": documents},
        ),
        (
            "POST",
            f"{base_url.rstrip('/')}/api/v1/rag/collections/{urllib.parse.quote(collection)}/documents/delete",
            {"documents": documents},
        ),
        (
            "POST",
            f"{base_url.rstrip('/')}/api/v1/rag/delete",
            {"collection": collection, "documents": documents},
        ),
    ]

    errors: list[dict[str, Any]] = []
    for method, url, payload in endpoint_attempts:
        ok, status, body, detail = _request_json(method=method, url=url, payload=payload, timeout=timeout)
        if ok and status < 400:
            return {
                "ok": True,
                "backend": "clawrag",
                "collection": collection,
                "deleted_count": len(documents),
                "mode": "api",
                "endpoint": url,
                "response": body,
            }
        errors.append({"endpoint": url, "status_code": status, "detail": detail})

    return {
        "ok": False,
        "backend": "clawrag",
        "collection": collection,
        "deleted_count": 0,
        "mode": "api_failed",
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=DEFAULT_CLAWRAG_BASE_URL)
    parser.add_argument("--collection", default="translation-kb")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_health = sub.add_parser("health")
    p_health.add_argument("--timeout", type=int, default=8)

    p_sync = sub.add_parser("sync")
    p_sync.add_argument("--changed-file", action="append", default=[])
    p_sync.add_argument("--changed-json-file", default="")
    p_sync.add_argument("--timeout", type=int, default=30)

    p_search = sub.add_parser("search")
    p_search.add_argument("--query", required=True)
    p_search.add_argument("--top-k", type=int, default=12)
    p_search.add_argument("--timeout", type=int, default=15)

    args = parser.parse_args()

    if args.cmd == "health":
        out = clawrag_health(base_url=args.base_url, timeout=args.timeout)
        print(json.dumps(out, ensure_ascii=False))
        return 0 if out.get("ok") else 1

    if args.cmd == "sync":
        changed: list[str] = list(args.changed_file or [])
        if args.changed_json_file:
            payload = json.loads(Path(args.changed_json_file).read_text(encoding="utf-8"))
            if isinstance(payload, list):
                changed.extend([str(x) for x in payload if str(x).strip()])
        out = clawrag_sync(
            changed_paths=changed,
            base_url=args.base_url,
            collection=args.collection,
            timeout=args.timeout,
        )
        print(json.dumps(out, ensure_ascii=False))
        return 0 if out.get("ok") else 1

    if args.cmd == "search":
        out = clawrag_search(
            query=args.query,
            top_k=args.top_k,
            base_url=args.base_url,
            collection=args.collection,
            timeout=args.timeout,
        )
        print(json.dumps(out, ensure_ascii=False))
        return 0 if out.get("ok") else 1

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
