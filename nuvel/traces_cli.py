"""nuvel traces — inspect agent trace logs across all local agents.

Discovers JSONL trace files in:
  - ./traces (current working dir)
  - generated-agents/*/traces  (per-agent dirs at the project root)
  - $TRACE_DIR (if set)
  - any path passed via --source

Handles two schemas in the same file/line stream:

  ADK rich format — multiple lines per run, joined by `trace_id`:
      {event: run_start, trace_id, session_id, agent, user_input, timestamp}
      {event: llm_request, ...}
      {event: llm_response, usage: {prompt_tokens, completion_tokens, ...}}
      {event: run_end, duration_ms, llm_calls, tool_calls, total_*_tokens}

  Claude Agent SDK flat format — one line per run:
      {ts, session_id, total_cost_usd, duration_ms, num_turns, subtype}

The CLI normalizes both into a `Run` summary so list/show/stats work uniformly.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable, Iterator


# ── Discovery ────────────────────────────────────────────────────────


def _discover_trace_dirs(extra: list[str] | None = None) -> list[Path]:
    roots: list[Path] = []
    cwd = Path.cwd()

    local = cwd / "traces"
    if local.is_dir():
        roots.append(local)

    gen = cwd / "generated-agents"
    if gen.is_dir():
        for agent_dir in sorted(gen.iterdir()):
            t = agent_dir / "traces"
            if t.is_dir():
                roots.append(t)

    env = os.getenv("TRACE_DIR")
    if env:
        p = Path(env)
        if p.is_dir() and p not in roots:
            roots.append(p)

    for src in extra or []:
        p = Path(src)
        if p.is_dir() and p not in roots:
            roots.append(p)

    return roots


def _iter_trace_files(dirs: Iterable[Path]) -> Iterator[Path]:
    for d in dirs:
        for f in sorted(d.glob("*.jsonl")):
            yield f


def _agent_label_for(file: Path) -> str:
    """Derive an agent label from the trace file's location."""
    parts = file.resolve().parts
    if "generated-agents" in parts:
        i = parts.index("generated-agents")
        if i + 1 < len(parts):
            return parts[i + 1]
    # Top-level ./traces — likely the meta-agent
    return "(local)"


# ── Run model ────────────────────────────────────────────────────────


@dataclass
class Run:
    """Normalized summary of one agent run, sourced from either schema."""

    agent: str
    file: Path
    session_id: str
    trace_id: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    duration_ms: int | None = None
    llm_calls: int = 0
    tool_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float | None = None
    user_input: str | None = None
    subtype: str | None = None
    schema: str = "adk"  # "adk" | "casdk"
    events: list[dict] = field(default_factory=list)

    @property
    def key(self) -> str:
        return self.trace_id or self.session_id


def _read_jsonl(path: Path) -> Iterator[dict]:
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    # Best-effort: skip partial/corrupt lines silently.
                    continue
    except OSError:
        return


def _parse_file_runs(path: Path, keep_events: bool = False) -> list[Run]:
    """Group records in one JSONL file into Run summaries."""
    agent_label = _agent_label_for(path)
    runs: dict[str, Run] = {}

    for rec in _read_jsonl(path):
        # CASDK flat schema: no `event` key, has top-level cost/duration/num_turns.
        if "event" not in rec and ("num_turns" in rec or "total_cost_usd" in rec):
            sid = str(rec.get("session_id") or "")
            ts = rec.get("ts")
            run = Run(
                agent=agent_label,
                file=path,
                session_id=sid,
                trace_id=None,
                started_at=ts,
                ended_at=ts,
                duration_ms=rec.get("duration_ms"),
                llm_calls=rec.get("num_turns") or 0,
                cost_usd=rec.get("total_cost_usd"),
                subtype=rec.get("subtype"),
                schema="casdk",
            )
            if keep_events:
                run.events.append(rec)
            # CASDK writes one record per run, but use ts to disambiguate.
            runs[f"{sid}:{ts}"] = run
            continue

        # ADK rich schema.
        ev = rec.get("event")
        tid = rec.get("trace_id") or rec.get("session_id") or ""
        if not tid:
            continue
        run = runs.get(tid)
        if run is None:
            run = Run(
                agent=agent_label,
                file=path,
                session_id=str(rec.get("session_id") or ""),
                trace_id=str(rec.get("trace_id") or ""),
                schema="adk",
            )
            runs[tid] = run
        if keep_events:
            run.events.append(rec)

        if ev == "run_start":
            run.started_at = rec.get("timestamp")
            run.user_input = rec.get("user_input")
            if rec.get("agent"):
                # Prefer the in-trace agent name over the dir name.
                run.agent = f"{agent_label}/{rec['agent']}"
        elif ev == "run_end":
            run.ended_at = rec.get("timestamp")
            run.duration_ms = rec.get("duration_ms")
            run.llm_calls = rec.get("llm_calls") or run.llm_calls
            run.tool_calls = rec.get("tool_calls") or run.tool_calls
            run.prompt_tokens = rec.get("total_prompt_tokens") or run.prompt_tokens
            run.completion_tokens = rec.get("total_completion_tokens") or run.completion_tokens
            run.total_tokens = rec.get("total_tokens") or run.total_tokens
            cost = rec.get("total_cost_usd")
            if cost is not None:
                run.cost_usd = float(cost)
        elif ev == "llm_response":
            usage = rec.get("usage") or {}
            run.prompt_tokens += int(usage.get("prompt_tokens") or 0)
            run.completion_tokens += int(usage.get("completion_tokens") or 0)
            run.total_tokens += int(usage.get("total_tokens") or 0)
            # Aggregate per-call cost as a fallback when run_end omits it.
            cost = rec.get("cost_usd")
            if cost is not None:
                run.cost_usd = (run.cost_usd or 0.0) + float(cost)

    return list(runs.values())


def _collect_runs(sources: list[str] | None, keep_events: bool = False) -> list[Run]:
    dirs = _discover_trace_dirs(sources)
    runs: list[Run] = []
    for f in _iter_trace_files(dirs):
        runs.extend(_parse_file_runs(f, keep_events=keep_events))
    return runs


# ── Filters & formatting ─────────────────────────────────────────────


def _parse_since(value: str) -> datetime | None:
    """Accept YYYY-MM-DD or full ISO timestamp."""
    if not value:
        return None
    try:
        if "T" in value:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        return datetime.fromisoformat(value)
    except ValueError:
        print(f"warning: could not parse --since {value!r}", file=sys.stderr)
        return None


def _run_ts(run: Run) -> datetime | None:
    raw = run.started_at or run.ended_at
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _apply_filters(runs: list[Run], args: argparse.Namespace) -> list[Run]:
    agent = getattr(args, "agent", None)
    since = _parse_since(getattr(args, "since", "") or "")
    if agent:
        runs = [r for r in runs if agent.lower() in r.agent.lower()]
    if since:
        runs = [r for r in runs if (_run_ts(r) is None or _run_ts(r) >= since)]
    return runs


def _sort_runs(runs: list[Run]) -> list[Run]:
    return sorted(runs, key=lambda r: r.started_at or r.ended_at or "", reverse=True)


def _fmt_duration(ms: int | None) -> str:
    if ms is None:
        return "-"
    if ms < 1000:
        return f"{ms}ms"
    return f"{ms / 1000:.1f}s"


def _fmt_cost(cost: float | None) -> str:
    return "-" if cost is None else f"${cost:.4f}"


def _fmt_tokens(n: int) -> str:
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


def _short(value: str | None, n: int) -> str:
    if not value:
        return "-"
    value = " ".join(value.split())
    return value if len(value) <= n else value[: n - 1] + "…"


# ── Commands ─────────────────────────────────────────────────────────


def _cmd_list(args: argparse.Namespace) -> int:
    runs = _apply_filters(_collect_runs(args.source), args)
    runs = _sort_runs(runs)
    if args.limit:
        runs = runs[: args.limit]

    if not runs:
        print("No runs found.")
        return 0

    header = ("WHEN", "AGENT", "ID", "DUR", "LLM", "TOOLS", "TOK", "COST", "INPUT")
    rows = []
    for r in runs:
        when = (r.started_at or r.ended_at or "-")[:19].replace("T", " ")
        ident = (r.trace_id or r.session_id or "-")[:12]
        rows.append((
            when,
            r.agent[:24],
            ident,
            _fmt_duration(r.duration_ms),
            str(r.llm_calls or "-"),
            str(r.tool_calls or "-"),
            _fmt_tokens(r.total_tokens) if r.total_tokens else "-",
            _fmt_cost(r.cost_usd),
            _short(r.user_input or r.subtype, 40),
        ))

    widths = [max(len(h), *(len(row[i]) for row in rows)) for i, h in enumerate(header)]
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    print(fmt.format(*header))
    print(fmt.format(*("-" * w for w in widths)))
    for row in rows:
        print(fmt.format(*row))
    print(f"\n{len(rows)} run(s).")
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    runs = _collect_runs(args.source, keep_events=True)
    target = args.id
    matches = [r for r in runs if r.trace_id == target or r.session_id == target
               or (r.trace_id and r.trace_id.startswith(target))
               or (r.session_id and r.session_id.startswith(target))]
    if not matches:
        print(f"No run found for id {target!r}.", file=sys.stderr)
        return 1
    if len(matches) > 1 and not args.all:
        print(f"{len(matches)} runs match — pass --all to show every one, "
              "or use a longer id prefix.", file=sys.stderr)
        for r in matches[:10]:
            print(f"  {r.trace_id or r.session_id}  ({r.agent})", file=sys.stderr)
        return 1

    for r in matches:
        _print_run(r)
    return 0


def _print_run(r: Run) -> None:
    print(f"── {r.trace_id or r.session_id} ─ {r.agent}")
    print(f"   file:     {r.file}")
    print(f"   session:  {r.session_id}")
    print(f"   started:  {r.started_at or '-'}")
    print(f"   ended:    {r.ended_at or '-'}")
    print(f"   duration: {_fmt_duration(r.duration_ms)}")
    print(f"   llm/tool: {r.llm_calls}/{r.tool_calls}  tokens: {r.total_tokens}  cost: {_fmt_cost(r.cost_usd)}")
    if r.user_input:
        print(f"   input:    {_short(r.user_input, 200)}")
    print()
    for ev in r.events:
        name = ev.get("event") or "(record)"
        ts = (ev.get("timestamp") or ev.get("ts") or "")[11:23]
        depth = int(ev.get("agent_depth") or 0)
        indent = "  " * max(depth - 1, 0)
        detail = _event_detail(ev)
        print(f"   {ts}  {indent}{name:<14} {detail}")
    print()


def _event_detail(ev: dict) -> str:
    name = ev.get("event")
    if name == "llm_request":
        return f"model={ev.get('model')}  msgs={ev.get('message_count')}  tools={len(ev.get('tools_available') or [])}"
    if name == "llm_response":
        u = ev.get("usage") or {}
        return (f"latency={ev.get('latency_ms')}ms  "
                f"tok={u.get('total_tokens')}  "
                f"calls={len(ev.get('function_calls') or [])}")
    if name == "tool_start":
        args = ev.get("args") or {}
        arg_preview = _short(", ".join(f"{k}={v!r}" for k, v in args.items()), 60)
        return f"tool={ev.get('tool')}  args={arg_preview}"
    if name == "tool_end":
        return (f"tool={ev.get('tool')}  status={ev.get('status')}  "
                f"dur={ev.get('duration_ms')}ms")
    if name == "run_start":
        return _short(ev.get("user_input"), 80)
    if name == "run_end":
        cost = ev.get("total_cost_usd")
        cost_part = f"  cost=${cost:.4f}" if cost else ""
        return (f"dur={ev.get('duration_ms')}ms  "
                f"llm={ev.get('llm_calls')}  tool={ev.get('tool_calls')}  "
                f"tok={ev.get('total_tokens')}{cost_part}")
    if name == "agent_start":
        return f"agent={ev.get('agent')}"
    if name == "agent_end":
        return f"agent={ev.get('agent')}"
    if name == "agent_transfer":
        return f"{ev.get('from_agent')} → {ev.get('to_agent')}"
    if name == "agent_escalate":
        return f"agent={ev.get('agent')}  (escalating up)"
    if name == "agent_end_of_turn":
        return f"agent={ev.get('agent')}"
    if name == "auth_requested":
        ids = ev.get("function_call_ids") or []
        return f"agent={ev.get('agent')}  fn_call_ids={len(ids)}"
    if name == "event":
        actions = ev.get("actions") or {}
        bits = []
        if actions.get("transfer_to_agent"):
            bits.append(f"→{actions['transfer_to_agent']}")
        if actions.get("escalate"):
            bits.append("escalate")
        if actions.get("state_delta_keys"):
            bits.append(f"state[{','.join(actions['state_delta_keys'])}]")
        if actions.get("artifact_delta"):
            bits.append(f"artifacts={list(actions['artifact_delta'].keys())}")
        return "  ".join(bits)
    if not name:
        return _short(ev.get("subtype"), 80)
    return ""


def _cmd_stats(args: argparse.Namespace) -> int:
    runs = _apply_filters(_collect_runs(args.source), args)
    if not runs:
        print("No runs found.")
        return 0

    by_agent: dict[str, dict] = {}
    for r in runs:
        agent = r.agent.split("/")[0]
        bucket = by_agent.setdefault(agent, {
            "runs": 0, "llm": 0, "tools": 0, "tokens": 0,
            "duration_ms": 0, "cost": 0.0, "any_cost": False,
        })
        bucket["runs"] += 1
        bucket["llm"] += r.llm_calls or 0
        bucket["tools"] += r.tool_calls or 0
        bucket["tokens"] += r.total_tokens or 0
        bucket["duration_ms"] += r.duration_ms or 0
        if r.cost_usd is not None:
            bucket["cost"] += r.cost_usd
            bucket["any_cost"] = True

    header = ("AGENT", "RUNS", "LLM", "TOOLS", "TOKENS", "DUR", "COST")
    rows = []
    for agent, b in sorted(by_agent.items()):
        rows.append((
            agent,
            str(b["runs"]),
            str(b["llm"]),
            str(b["tools"]),
            _fmt_tokens(b["tokens"]),
            _fmt_duration(b["duration_ms"]),
            (f"${b['cost']:.4f}" if b["any_cost"] else "-"),
        ))

    widths = [max(len(h), *(len(row[i]) for row in rows)) for i, h in enumerate(header)]
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    print(fmt.format(*header))
    print(fmt.format(*("-" * w for w in widths)))
    for row in rows:
        print(fmt.format(*row))

    total_runs = sum(b["runs"] for b in by_agent.values())
    total_tokens = sum(b["tokens"] for b in by_agent.values())
    total_cost = sum(b["cost"] for b in by_agent.values() if b["any_cost"])
    print(f"\nTotal: {total_runs} run(s), {_fmt_tokens(total_tokens)} tokens"
          + (f", ${total_cost:.4f}" if total_cost else ""))
    return 0


# ── Parser wiring ────────────────────────────────────────────────────


def _add_source_flag(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--source", "-s", action="append", default=None,
        help="Extra trace directory to scan (repeatable). Defaults discover "
             "./traces, generated-agents/*/traces, and $TRACE_DIR.",
    )


def _add_filters(p: argparse.ArgumentParser) -> None:
    p.add_argument("--agent", "-a", default=None,
                   help="Filter by agent name (substring match).")
    p.add_argument("--since", default=None,
                   help="Only runs at/after this date (YYYY-MM-DD or ISO).")


def register(subparsers: argparse._SubParsersAction) -> None:
    """Register the `traces` subcommand tree on an existing argparse subparsers."""
    p = subparsers.add_parser("traces", help="Inspect agent trace logs.")
    sub = p.add_subparsers(dest="traces_command", required=True)

    p_list = sub.add_parser("list", help="List runs across all agents.")
    _add_source_flag(p_list)
    _add_filters(p_list)
    p_list.add_argument("--limit", "-n", type=int, default=50,
                        help="Max rows to show (default 50, 0 for all).")
    p_list.set_defaults(func=_cmd_list)

    p_show = sub.add_parser("show", help="Show full event timeline for one run.")
    _add_source_flag(p_show)
    p_show.add_argument("id", help="trace_id or session_id (prefix is OK).")
    p_show.add_argument("--all", action="store_true",
                        help="If the id prefix matches multiple runs, show all.")
    p_show.set_defaults(func=_cmd_show)

    p_stats = sub.add_parser("stats", help="Aggregate stats per agent.")
    _add_source_flag(p_stats)
    _add_filters(p_stats)
    p_stats.set_defaults(func=_cmd_stats)
