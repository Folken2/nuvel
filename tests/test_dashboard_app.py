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
