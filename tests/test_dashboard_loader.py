"""Tests for nuvel.dashboard.loader."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nuvel.dashboard.loader import TraceLoader


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


@pytest.fixture
def traces_dir(tmp_path: Path) -> Path:
    d = tmp_path / "traces"
    _write_jsonl(d / "2026-05-15_abc123def456.jsonl", [
        {"trace_id": "abc123", "session_id": "s1", "event": "run_start",
         "timestamp": "2026-05-15T10:00:00+00:00", "agent": "meta_agent",
         "user_input": "hey"},
        {"trace_id": "abc123", "session_id": "s1", "event": "run_end",
         "timestamp": "2026-05-15T10:00:08+00:00", "duration_ms": 8000,
         "llm_calls": 1, "tool_calls": 0, "total_tokens": 1234,
         "total_cost_usd": 0.001},
    ])
    return d


def test_loader_returns_runs_in_newest_first_order(traces_dir: Path) -> None:
    loader = TraceLoader(sources=[traces_dir])
    runs = loader.runs()
    assert len(runs) == 1
    assert runs[0].trace_id == "abc123"
    assert runs[0].total_tokens == 1234


def test_loader_finds_run_by_full_trace_id(traces_dir: Path) -> None:
    loader = TraceLoader(sources=[traces_dir])
    run = loader.find_by_id("abc123")
    assert run is not None
    assert run.trace_id == "abc123"
    # find_by_id loads events for the detail page.
    assert len(run.events) >= 2


def test_loader_finds_run_by_id_prefix(traces_dir: Path) -> None:
    loader = TraceLoader(sources=[traces_dir])
    run = loader.find_by_id("abc")
    assert run is not None
    assert run.trace_id == "abc123"


def test_loader_returns_none_for_unknown_id(traces_dir: Path) -> None:
    loader = TraceLoader(sources=[traces_dir])
    assert loader.find_by_id("does-not-exist") is None


def test_loader_returns_empty_list_for_empty_dir(tmp_path: Path) -> None:
    loader = TraceLoader(sources=[tmp_path / "empty"])
    assert loader.runs() == []
