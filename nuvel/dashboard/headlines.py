"""Deterministic headline generator for the run detail page.

Picks a sentence from a fixed set keyed on the shape of the run
(`num_tool_calls`, `num_agent_transfers`, `has_errors`). Falls back to
`Run {trace_id_short}` when no template matches.

Not LLM-driven. No user-input inference. Predictable on purpose.
"""

from __future__ import annotations

from nuvel.traces_cli import Run

_ERROR_EVENTS = {"llm_error", "tool_exception"}


def _count_transfers(run: Run) -> tuple[int, str | None]:
    count = 0
    last_target: str | None = None
    for ev in run.events:
        if ev.get("event") == "agent_transfer":
            count += 1
            last_target = ev.get("to_agent") or last_target
    return count, last_target


def _has_errors(run: Run) -> bool:
    for ev in run.events:
        kind = ev.get("event")
        if kind in _ERROR_EVENTS:
            return True
        if kind == "tool_end" and ev.get("status") == "error":
            return True
    return False


def describe_run(run: Run) -> str:
    """Generate a one-sentence headline for a run.

    Deliberately small rule set. The fallback is the load-bearing case —
    most runs from new users won't match a template, and that's fine.
    """
    agent = run.agent.split("/")[-1]  # strip "(local)/" or similar prefix
    transfers, last_target = _count_transfers(run)

    if _has_errors(run):
        return f"{agent} hit an error mid-run."

    if transfers >= 1 and last_target:
        return f"{agent} handed off to {last_target} after {run.llm_calls} LLM call{'s' if run.llm_calls != 1 else ''}."

    if run.tool_calls >= 3:
        return f"{agent} thought through {run.tool_calls} tool calls in {run.llm_calls} turn{'s' if run.llm_calls != 1 else ''}."

    if run.tool_calls >= 1:
        tool_word = "tool call" if run.tool_calls == 1 else "tool calls"
        return f"{agent} worked through {run.tool_calls} {tool_word}."

    short = (run.trace_id or run.session_id or "")[:8]
    return f"Run {short}"
