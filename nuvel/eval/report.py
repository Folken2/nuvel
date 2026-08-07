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


def render_report(scored: list[ScoredRun]) -> str:
    """Per-agent summary: count, mean overall + per-component, top flags."""
    if not scored:
        return "No scored runs."

    by_agent: dict[str, list[ScoredRun]] = {}
    for s in scored:
        by_agent.setdefault(_agent_key(s), []).append(s)

    lines: list[str] = []
    header = ("AGENT", "RUNS", "OVERALL", "SUCC", "QUAL", "EFFI", "RELI", "TOP FLAG")
    rows: list[tuple[str, ...]] = []
    for agent, rows_ in sorted(by_agent.items()):
        n = len(rows_)
        mean_overall = sum(r.overall for r in rows_) / n
        comp_means = {}
        for key in ("success", "quality", "efficiency", "reliability"):
            vals = [r.components.get(key) for r in rows_ if key in r.components]
            comp_means[key] = sum(vals) / len(vals) if vals else None
        flag_counts = Counter(f for r in rows_ for f in r.flags)
        top = flag_counts.most_common(1)
        top_flag = f"{top[0][0]} ({top[0][1]})" if top else "-"
        rows.append((
            agent,
            str(n),
            _fmt_pct(mean_overall),
            _fmt_pct(comp_means["success"]),
            _fmt_pct(comp_means["quality"]),
            _fmt_pct(comp_means["efficiency"]),
            _fmt_pct(comp_means["reliability"]),
            top_flag,
        ))
    lines.append(_format_table(header, rows))
    return "\n".join(lines)


def render_worst(scored: list[ScoredRun], *, n: int = 10) -> str:
    """N worst runs by overall score, with judge notes inline."""
    if not scored:
        return "No scored runs."
    worst = sorted(scored, key=lambda s: s.overall)[:n]
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


def render_drift(reports) -> str:
    """Format a list of DriftReport into a table."""
    if not reports:
        return "No drift data."
    header = ("AGENT", "CURRENT", "BASELINE", "DELTA", "CUR_N", "BASE_N", "STATUS")
    rows = []
    for r in reports:
        if r.delta is None:
            status = "—" if r.current_n == 0 and r.baseline_n == 0 else "insufficient"
        else:
            status = "⚠ DRIFT" if r.drifted else "ok"
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
