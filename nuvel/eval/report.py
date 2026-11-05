"""Rendering for `nuvel eval report` and `nuvel eval worst`.

Pure functions that take loaded scored data + filter args and return a
human-readable string. The CLI module owns I/O.
"""
from __future__ import annotations

from collections import Counter

from nuvel.eval.schema import ScoredRun


def _agent_key(s: ScoredRun) -> str:
    return s.agent.split("/")[0]


def _fmt_pct(x: float | None) -> str:
    return "-" if x is None else f"{x:.2f}"


_COMPONENTS = ("success", "quality", "efficiency", "reliability")


def summarize_report(scored: list[ScoredRun]) -> list[dict]:
    """Per-agent summary rows: count, mean overall + per-component, top flag."""
    by_agent: dict[str, list[ScoredRun]] = {}
    for s in scored:
        by_agent.setdefault(_agent_key(s), []).append(s)

    summary: list[dict] = []
    for agent, rows_ in sorted(by_agent.items()):
        n = len(rows_)
        comp_means = {}
        for key in _COMPONENTS:
            vals = [r.components.get(key) for r in rows_ if key in r.components]
            comp_means[key] = sum(vals) / len(vals) if vals else None
        top = Counter(f for r in rows_ for f in r.flags).most_common(1)
        summary.append({
            "agent": agent,
            "runs": n,
            "overall": sum(r.overall for r in rows_) / n,
            "components": comp_means,
            "top_flag": {"flag": top[0][0], "count": top[0][1]} if top else None,
        })
    return summary


def render_report(scored: list[ScoredRun]) -> str:
    """Per-agent summary: count, mean overall + per-component, top flags."""
    if not scored:
        return "No scored runs."

    header = ("AGENT", "RUNS", "OVERALL", "SUCC", "QUAL", "EFFI", "RELI", "TOP FLAG")
    rows: list[tuple[str, ...]] = []
    for row in summarize_report(scored):
        top = row["top_flag"]
        rows.append((
            row["agent"],
            str(row["runs"]),
            _fmt_pct(row["overall"]),
            *(_fmt_pct(row["components"][key]) for key in _COMPONENTS),
            f"{top['flag']} ({top['count']})" if top else "-",
        ))
    return _format_table(header, rows)


def worst_runs(scored: list[ScoredRun], *, n: int = 10) -> list[ScoredRun]:
    """The N worst runs by overall score, worst first."""
    return sorted(scored, key=lambda s: s.overall)[:n]


def render_worst(scored: list[ScoredRun], *, n: int = 10) -> str:
    """N worst runs by overall score, with judge notes inline."""
    if not scored:
        return "No scored runs."
    worst = worst_runs(scored, n=n)
    lines = [f"{'SCORE':<6} {'AGENT':<24} {'TRACE_ID':<14} FLAGS / NOTE"]
    lines.append("-" * len(lines[0]))
    for s in worst:
        flags = ",".join(s.flags) if s.flags else "-"
        note = (s.judge or {}).get("notes") or ""
        detail = flags if not note else f"{flags}  — {note}"
        lines.append(f"{s.overall:<6.2f} {_agent_key(s):<24} {s.trace_id[:14]:<14} {detail}")
    return "\n".join(lines)


def _format_table(header: tuple[str, ...], rows: list[tuple[str, ...]]) -> str:
    if not rows:
        return "  ".join(header)
    widths = [max(len(h), *(len(r[i]) for r in rows)) for i, h in enumerate(header)]
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    out = [fmt.format(*header), fmt.format(*("-" * w for w in widths))]
    out.extend(fmt.format(*r) for r in rows)
    return "\n".join(out)


def drift_status(r) -> str:
    """`no_data`, `insufficient`, `drift` or `ok` for one DriftReport."""
    if r.delta is None:
        return "no_data" if r.current_n == 0 and r.baseline_n == 0 else "insufficient"
    return "drift" if r.drifted else "ok"


_DRIFT_LABELS = {"no_data": "—", "drift": "⚠ DRIFT"}


def render_drift(reports) -> str:
    """Format a list of DriftReport into a table."""
    if not reports:
        return "No drift data."
    header = ("AGENT", "CURRENT", "BASELINE", "DELTA", "CUR_N", "BASE_N", "STATUS")
    rows = []
    for r in reports:
        status = drift_status(r)
        status = _DRIFT_LABELS.get(status, status)
        rows.append((
            r.agent,
            _fmt_pct(r.current_mean),
            _fmt_pct(r.baseline_mean),
            ("-" if r.delta is None else f"{r.delta:+.3f}"),
            str(r.current_n),
            str(r.baseline_n),
            status,
        ))
    return _format_table(header, rows)
