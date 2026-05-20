"""Tests for nuvel.dashboard.app."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from nuvel.dashboard.app import build_app
from nuvel.dashboard.loader import TraceLoader


def _write(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


@pytest.fixture
def loader(tmp_path: Path) -> TraceLoader:
    _write(tmp_path / "traces" / "2026-05-15_abc.jsonl", [
        {"trace_id": "abc123", "session_id": "s1", "event": "run_start",
         "timestamp": "2026-05-15T10:00:00+00:00", "agent": "meta_agent",
         "user_input": "hello"},
        {"trace_id": "abc123", "session_id": "s1", "event": "run_end",
         "timestamp": "2026-05-15T10:00:05+00:00", "duration_ms": 5000,
         "llm_calls": 1, "tool_calls": 0, "total_tokens": 1000},
    ])
    return TraceLoader(sources=[tmp_path / "traces"])


def test_home_returns_200_with_run_id(loader: TraceLoader) -> None:
    client = TestClient(build_app(loader))
    r = client.get("/")
    assert r.status_code == 200
    assert "abc123" in r.text


def test_run_detail_returns_200_for_known_id(loader: TraceLoader) -> None:
    client = TestClient(build_app(loader))
    r = client.get("/run/abc123")
    assert r.status_code == 200
    assert "abc123" in r.text


def test_run_detail_returns_404_for_unknown_id(loader: TraceLoader) -> None:
    client = TestClient(build_app(loader))
    r = client.get("/run/nope")
    assert r.status_code == 404


def test_feed_partial_returns_html_fragment(loader: TraceLoader) -> None:
    client = TestClient(build_app(loader))
    r = client.get("/api/runs/feed")
    assert r.status_code == 200
    assert "abc123" in r.text
    # Partial should NOT include a full HTML document.
    assert "<html" not in r.text.lower()


def test_sse_endpoint_is_event_stream_when_watcher_attached(tmp_path) -> None:
    # Sync TestClient and httpx.ASGITransport both buffer the response body
    # to completion, which hangs for an indefinite SSE stream. Drive the ASGI
    # app directly and send `http.disconnect` once the response starts so we
    # can assert on headers without consuming the stream.
    import asyncio

    from nuvel.dashboard.watcher import RunWatcher

    watcher = RunWatcher(sources=[tmp_path], poll_seconds=0.2)
    loader = TraceLoader(sources=[tmp_path])
    app = build_app(loader, watcher)

    async def _drive() -> tuple[int, dict[str, str]]:
        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/sse",
            "raw_path": b"/sse",
            "query_string": b"",
            "root_path": "",
            "headers": [],
            "server": ("testserver", 80),
            "client": ("testclient", 12345),
        }
        captured: dict = {}
        response_started = asyncio.Event()
        first_body_seen = asyncio.Event()

        async def receive():
            # Block until the response has started, then signal disconnect so
            # the SSE generator's `finally` clause runs and the ASGI call returns.
            await first_body_seen.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            if message["type"] == "http.response.start":
                captured["status"] = message["status"]
                captured["headers"] = {
                    k.decode(): v.decode() for k, v in message.get("headers", [])
                }
                response_started.set()
            elif message["type"] == "http.response.body":
                first_body_seen.set()

        await app(scope, receive, send)
        return captured["status"], captured["headers"]

    status, headers = asyncio.run(_drive())
    assert status == 200
    assert headers["content-type"].startswith("text/event-stream")


def test_sse_endpoint_404_when_no_watcher(loader) -> None:
    client = TestClient(build_app(loader, watcher=None))
    r = client.get("/sse")
    assert r.status_code == 404
