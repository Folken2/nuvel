"""CLI subcommand behavior.

Drives `nuvel eval ...` via the top-level parser to verify the full
argparse path. Judge is mocked so no real LLM calls happen.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from nuvel.cli import build_parser
from nuvel.eval.schema import JudgeResult
from nuvel.eval.writer import load_scored_index


@pytest.fixture(autouse=True)
def _mock_judge(monkeypatch: pytest.MonkeyPatch):
    """Replace the default judge with a deterministic fake everywhere."""
    async def fake_judge(run, rubric):
        # Mid-range default to keep results predictable.
        return JudgeResult(model="fake-model", success=0.8, quality=0.7, cost_usd=0.0001)

    monkeypatch.setattr("nuvel.eval.judge.judge_run", fake_judge, raising=True)
    # Also patch the import in scorer.py since it imported by name at module load.
    monkeypatch.setattr("nuvel.eval.scorer._default_judge_run", fake_judge, raising=True)


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


def _setup_demo(tmp_path: Path) -> Path:
    agent_dir = tmp_path / "generated-agents" / "demo" / "traces"
    _write_trace(agent_dir / "day.jsonl", "trace-1")
    _write_trace(agent_dir / "day.jsonl", "trace-1")
    return agent_dir


def test_cli_score_writes_scored_jsonl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    agent_dir = _setup_demo(tmp_path)
    monkeypatch.chdir(tmp_path)
    parser = build_parser()
    args = parser.parse_args(["eval", "score"])
    rc = args.func(args)
    assert rc == 0
    captured = capsys.readouterr().out
    assert "Scored: 1" in captured
    assert (agent_dir / "scored.jsonl").is_file()


def test_cli_score_dry_run_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    agent_dir = _setup_demo(tmp_path)
    monkeypatch.chdir(tmp_path)
    parser = build_parser()
    args = parser.parse_args(["eval", "score", "--dry-run"])
    rc = args.func(args)
    assert rc == 0
    assert not (agent_dir / "scored.jsonl").exists()


def test_cli_score_second_run_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    _setup_demo(tmp_path)
    monkeypatch.chdir(tmp_path)
    parser = build_parser()
    parser.parse_args(["eval", "score"]).func(parser.parse_args(["eval", "score"]))
    capsys.readouterr()  # flush
    args = parser.parse_args(["eval", "score"])
    rc = args.func(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "Scored: 0" in out
    assert "skipped (already scored" in out


def test_cli_report_renders_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    _setup_demo(tmp_path)
    monkeypatch.chdir(tmp_path)
    parser = build_parser()
    parser.parse_args(["eval", "score"]).func(parser.parse_args(["eval", "score"]))
    capsys.readouterr()  # flush score output
    rc = parser.parse_args(["eval", "report"]).func(parser.parse_args(["eval", "report"]))
    assert rc == 0
    out = capsys.readouterr().out
    assert "AGENT" in out
    assert "demo" in out


def test_cli_worst_lists_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    _setup_demo(tmp_path)
    monkeypatch.chdir(tmp_path)
    parser = build_parser()
    parser.parse_args(["eval", "score"]).func(parser.parse_args(["eval", "score"]))
    capsys.readouterr()
    rc = parser.parse_args(["eval", "worst", "-n", "5"]).func(
        parser.parse_args(["eval", "worst", "-n", "5"])
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "trace-1" in out
    assert "SCORE" in out


def test_cli_drift_with_no_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.chdir(tmp_path)
    parser = build_parser()
    args = parser.parse_args(["eval", "drift"])
    rc = args.func(args)
    assert rc == 0  # no drift to report → exit 0
    out = capsys.readouterr().out
    assert "No drift data" in out or "AGENT" in out
