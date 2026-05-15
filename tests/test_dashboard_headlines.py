"""Tests for nuvel.dashboard.headlines."""

from __future__ import annotations

from dataclasses import field
from pathlib import Path

from nuvel.dashboard.headlines import describe_run
from nuvel.traces_cli import Run


def _run(**kwargs) -> Run:
    base = dict(
        agent="meta_agent",
        file=Path("/dev/null"),
        session_id="s1",
        trace_id="abc123def456",
        llm_calls=0,
        tool_calls=0,
        events=[],
    )
    base.update(kwargs)
    return Run(**base)


def test_describe_falls_back_to_id_for_minimal_runs() -> None:
    r = _run(llm_calls=1, tool_calls=0)
    assert describe_run(r) == "Run abc123de"


def test_describe_recognizes_tool_use() -> None:
    r = _run(llm_calls=3, tool_calls=4)
    assert describe_run(r) == "meta_agent thought through 4 tool calls in 3 turns."


def test_describe_recognizes_sub_agent_transfer() -> None:
    events = [
        {"event": "agent_transfer", "from_agent": "meta_agent", "to_agent": "outlook_specialist"},
    ]
    r = _run(llm_calls=4, tool_calls=2, events=events)
    headline = describe_run(r)
    assert "meta_agent" in headline
    assert "handed off" in headline
    assert "outlook_specialist" in headline


def test_describe_recognizes_errors() -> None:
    events = [
        {"event": "tool_exception", "tool": "send_email", "error_type": "SMTPAuthenticationError"},
    ]
    r = _run(llm_calls=2, tool_calls=1, events=events)
    assert describe_run(r) == "meta_agent hit an error mid-run."
