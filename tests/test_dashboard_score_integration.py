"""Dashboard surfaces scores from scored.jsonl siblings.

Covers the loader join + the home view's score column rendering.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from nuvel.dashboard.app import _view, build_app
from nuvel.dashboard.loader import TraceLoader
from nuvel.eval.schema import SCORER_VERSION, Flag, ScoredRun
from nuvel.eval.writer import append_scored
from nuvel.traces_cli import _parse_file_runs


def _write_trace(path: Path, trace_id: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    records = [
        {"event": "run_start", "trace_id": trace_id, "session_id": "s",
         "agent": "demo", "user_input": "hi",
         "timestamp": "2026-05-20T10:00:00+00:00"},
        {"event": "llm_response", "trace_id": trace_id, "session_id": "s",
         "response_text": "ok",
         "usage": {"completion_tokens": 10, "total_tokens": 10}},
        {"event": "run_end", "trace_id": trace_id, "session_id": "s",
         "timestamp": "2026-05-20T10:00:01+00:00",
         "duration_ms": 500, "llm_calls": 1, "tool_calls": 0,
         "total_tokens": 10},
    ]
    with path.open("w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def _scored(trace_id: str, overall: float, flags: list[str] | None = None) -> ScoredRun:
    return ScoredRun(
        trace_id=trace_id,
        agent="demo",
        scored_at="2026-05-20T11:00:00+00:00",
        scorer_version=SCORER_VERSION,
        rubric_version="r",
        overall=overall,
        components={"success": 1.0, "quality": overall},
        flags=flags or [],
    )


def test_loader_scored_index_aggregates(tmp_path: Path) -> None:
    agent_dir = tmp_path / "traces"
    _write_trace(agent_dir / "t.jsonl", "tid-1")
    append_scored(agent_dir / "scored.jsonl", _scored("tid-1", 0.8))
    loader = TraceLoader([agent_dir])
    idx = loader.scored_index()
    assert "tid-1" in idx
    assert idx["tid-1"].overall == 0.8


def test_loader_scored_index_missing_file_is_empty(tmp_path: Path) -> None:
    loader = TraceLoader([tmp_path / "traces"])
    assert loader.scored_index() == {}


def test_view_without_scored_shows_dash(tmp_path: Path) -> None:
    agent_dir = tmp_path / "traces"
    _write_trace(agent_dir / "t.jsonl", "tid-1")
    runs = _parse_file_runs(agent_dir / "t.jsonl", keep_events=True)
    view = _view(runs[0], scored=None)
    assert view.score_label == "—"
    assert view.score_class == "none"
    assert view.flags == []


def test_view_with_scored_renders_pill(tmp_path: Path) -> None:
    agent_dir = tmp_path / "traces"
    _write_trace(agent_dir / "t.jsonl", "tid-1")
    runs = _parse_file_runs(agent_dir / "t.jsonl", keep_events=True)
    view = _view(runs[0], scored=_scored("tid-1", 0.78, flags=[Flag.LATENCY_OUTLIER]))
    assert view.score_label == "0.78"
    assert view.score_class == "warn"
    assert Flag.LATENCY_OUTLIER in view.flags


def test_score_class_thresholds(tmp_path: Path) -> None:
    agent_dir = tmp_path / "traces"
    _write_trace(agent_dir / "t.jsonl", "tid-1")
    runs = _parse_file_runs(agent_dir / "t.jsonl", keep_events=True)
    assert _view(runs[0], _scored("tid-1", 0.9)).score_class == "good"
    assert _view(runs[0], _scored("tid-1", 0.6)).score_class == "warn"
    assert _view(runs[0], _scored("tid-1", 0.2)).score_class == "bad"


def test_home_endpoint_renders_score(tmp_path: Path) -> None:
    """End-to-end via FastAPI test client."""
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    agent_dir = tmp_path / "traces"
    _write_trace(agent_dir / "t.jsonl", "tid-1")
    append_scored(agent_dir / "scored.jsonl", _scored("tid-1", 0.83))

    loader = TraceLoader([agent_dir])
    app = build_app(loader)
    with TestClient(app) as client:
        resp = client.get("/")
        assert resp.status_code == 200
        body = resp.text
        # Score pill renders the formatted value.
        assert "0.83" in body
        assert "score-good" in body
