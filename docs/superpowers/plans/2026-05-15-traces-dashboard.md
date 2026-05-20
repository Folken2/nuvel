# `nuvel dashboard` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `nuvel dashboard` — a polished, locally-served, Editorial-styled web view over the existing JSONL trace stream. Demo/portfolio surface, two pages (home + run detail), live updates via SSE, bundled fixtures via `--demo`.

**Architecture:** New `nuvel/dashboard/` package. FastAPI + Jinja + HTMX + Tailwind CDN. Reuses `_collect_runs` from `nuvel/traces_cli.py` — no parallel JSONL parser. Watcher polls trace dirs at 1s via `watchfiles`, pushes new runs onto an `asyncio.Queue` that the `/sse` endpoint drains.

**Tech Stack:** Python 3.11+, FastAPI, Jinja2, watchfiles, uvicorn (already a dep), HTMX (CDN), Tailwind (CDN). New runtime deps: `jinja2`, `watchfiles`. Test dep: `httpx` (FastAPI's TestClient transitively pulls it; verify before adding).

The reference mockups live in `.superpowers/brainstorm/15339-1778857358/content/visual-style.html` (Editorial direction) and `page-mockups.html` (home + run detail). Tasks 9–11 reference them for visual styling.

---

## Task 1: Add runtime dependencies

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Edit `requirements.txt`** — add three new lines under the existing block, alphabetically near `fastapi`:

```
jinja2>=3.1,<4.0
pytest-asyncio>=0.23,<2.0
watchfiles>=0.21,<2.0
```

- [ ] **Step 2: Reinstall in the worktree's venv**

Run: `pip install -e .`
Expected: jinja2, watchfiles, and pytest-asyncio installed successfully.

- [ ] **Step 2b: Enable asyncio test mode**

Find `[tool.pytest.ini_options]` in `pyproject.toml` (create the section if missing) and add:

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
```

If the section already exists, just add `asyncio_mode = "auto"` to it.

- [ ] **Step 3: Confirm test client transitively gets httpx**

Run: `python -c "from fastapi.testclient import TestClient; import httpx; print(httpx.__version__)"`
Expected: prints a version string. If `ModuleNotFoundError`, add `httpx` to `requirements.txt` too.

- [ ] **Step 4: Commit**

```bash
git add requirements.txt pyproject.toml
git commit -m "deps: jinja2 + watchfiles + pytest-asyncio for nuvel dashboard"
```

---

## Task 2: Create the dashboard package skeleton

**Files:**
- Create: `nuvel/dashboard/__init__.py`
- Create: `nuvel/dashboard/cli.py`
- Create: `nuvel/dashboard/app.py`
- Create: `nuvel/dashboard/loader.py`
- Create: `nuvel/dashboard/watcher.py`
- Create: `nuvel/dashboard/templates/.gitkeep`
- Create: `nuvel/dashboard/static/.gitkeep`
- Create: `nuvel/dashboard/fixtures/.gitkeep`

- [ ] **Step 1: Create `nuvel/dashboard/__init__.py`**

```python
"""nuvel dashboard — local web command center over the JSONL trace stream.

Embedded as the `nuvel dashboard` subcommand. See
`docs/superpowers/specs/2026-05-15-traces-dashboard-design.md`.
"""

from nuvel.dashboard.cli import register

__all__ = ["register"]
```

- [ ] **Step 2: Create placeholder `nuvel/dashboard/cli.py`**

```python
"""nuvel dashboard CLI subcommand."""

from __future__ import annotations

import argparse


def register(subparsers: argparse._SubParsersAction) -> None:
    """Register the `dashboard` subcommand. Wired in Task 6."""
    raise NotImplementedError("Filled in by Task 6.")
```

- [ ] **Step 3: Create placeholder `nuvel/dashboard/app.py`**

```python
"""FastAPI app factory for the dashboard. Filled in by Task 5."""

from __future__ import annotations
```

- [ ] **Step 4: Create placeholder `nuvel/dashboard/loader.py`**

```python
"""Trace loader for the dashboard. Filled in by Task 3."""

from __future__ import annotations
```

- [ ] **Step 5: Create placeholder `nuvel/dashboard/watcher.py`**

```python
"""JSONL watcher → SSE channel. Filled in by Task 12."""

from __future__ import annotations
```

- [ ] **Step 6: Create `.gitkeep` files for empty dirs**

```bash
mkdir -p nuvel/dashboard/templates nuvel/dashboard/static nuvel/dashboard/fixtures
touch nuvel/dashboard/templates/.gitkeep nuvel/dashboard/static/.gitkeep nuvel/dashboard/fixtures/.gitkeep
```

- [ ] **Step 7: Confirm the package imports cleanly**

Run: `python -c "import nuvel.dashboard; print(nuvel.dashboard.register)"`
Expected: prints `<function register at 0x…>`.

- [ ] **Step 8: Commit**

```bash
git add nuvel/dashboard/
git commit -m "scaffold: nuvel/dashboard/ package skeleton"
```

---

## Task 3: Implement the loader (TDD)

The loader wraps `_collect_runs` from `nuvel.traces_cli` and adds a `find_by_id` helper. It treats sources as a fixed list (resolved once at startup) so the watcher and HTTP handlers share the same view.

**Files:**
- Modify: `nuvel/dashboard/loader.py`
- Create: `tests/test_dashboard_loader.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_dashboard_loader.py
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_dashboard_loader.py -v`
Expected: All five tests fail with `ImportError: cannot import name 'TraceLoader'`.

- [ ] **Step 3: Implement `nuvel/dashboard/loader.py`**

```python
"""Trace loader for the dashboard.

Thin wrapper around `nuvel.traces_cli._collect_runs` so the dashboard
and the CLI share one parser and one `Run` schema. Sources are resolved
once at construction so the watcher and HTTP handlers see the same view.
"""

from __future__ import annotations

from pathlib import Path

from nuvel.traces_cli import (
    Run,
    _iter_trace_files,
    _parse_file_runs,
    _sort_runs,
)


class TraceLoader:
    """Loads `Run` records from a fixed list of source directories."""

    def __init__(self, sources: list[Path]) -> None:
        self._sources = sources

    def sources(self) -> list[Path]:
        return list(self._sources)

    def runs(self) -> list[Run]:
        """Return all runs across sources, newest first. No events kept."""
        out: list[Run] = []
        for f in _iter_trace_files(self._sources):
            out.extend(_parse_file_runs(f, keep_events=False))
        return _sort_runs(out)

    def find_by_id(self, id_or_prefix: str) -> Run | None:
        """Find a single run by trace_id or session_id (prefix match)."""
        for f in _iter_trace_files(self._sources):
            for run in _parse_file_runs(f, keep_events=True):
                if (
                    run.trace_id == id_or_prefix
                    or run.session_id == id_or_prefix
                    or (run.trace_id and run.trace_id.startswith(id_or_prefix))
                    or (run.session_id and run.session_id.startswith(id_or_prefix))
                ):
                    return run
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_dashboard_loader.py -v`
Expected: All five tests pass.

- [ ] **Step 5: Commit**

```bash
git add nuvel/dashboard/loader.py tests/test_dashboard_loader.py
git commit -m "feat(dashboard): TraceLoader wrapping traces_cli._collect_runs"
```

---

## Task 4: Implement the headline generator (TDD)

Rule-based, deterministic. Picks a sentence from a small set keyed on `(num_tool_calls, num_agent_transfers, has_errors)`.

**Files:**
- Create: `nuvel/dashboard/headlines.py`
- Create: `tests/test_dashboard_headlines.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_dashboard_headlines.py
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
    headline = describe_run(r)
    assert "meta_agent" in headline
    assert "thought through" in headline or "worked through" in headline
    assert "4 tool calls" in headline or "four tool calls" in headline


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
    headline = describe_run(r)
    assert "ran into trouble" in headline or "hit an error" in headline
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_dashboard_headlines.py -v`
Expected: All four tests fail with `ImportError`.

- [ ] **Step 3: Implement `nuvel/dashboard/headlines.py`**

```python
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
        return f"{agent} ran into trouble during a {run.tool_calls or 'one'}-tool call run."

    if transfers >= 1 and last_target:
        return f"{agent} handed off to {last_target} after {run.llm_calls} LLM call{'s' if run.llm_calls != 1 else ''}."

    if run.tool_calls >= 3:
        return f"{agent} thought through a {run.tool_calls} tool calls in {run.llm_calls} turn{'s' if run.llm_calls != 1 else ''}."

    if run.tool_calls >= 1:
        return f"{agent} worked through {run.tool_calls} tool calls."

    short = (run.trace_id or run.session_id or "")[:8]
    return f"Run {short}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_dashboard_headlines.py -v`
Expected: All four tests pass.

- [ ] **Step 5: Commit**

```bash
git add nuvel/dashboard/headlines.py tests/test_dashboard_headlines.py
git commit -m "feat(dashboard): rule-based headline generator for run detail"
```

---

## Task 5: FastAPI app factory and three routes (TDD)

Routes return raw text in this task — templates land in Task 9–11. This task verifies plumbing.

**Files:**
- Modify: `nuvel/dashboard/app.py`
- Create: `tests/test_dashboard_app.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_dashboard_app.py
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_dashboard_app.py -v`
Expected: All four tests fail with `ImportError: cannot import name 'build_app'`.

- [ ] **Step 3: Implement `nuvel/dashboard/app.py`** (templates still placeholder — Task 9 swaps these for real Jinja templates)

```python
"""FastAPI app factory for the dashboard.

Templates are wired in Task 9. This task ships a functional skeleton:
three routes returning minimal HTML so end-to-end plumbing is testable
before the visual layer arrives.
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse

from nuvel.dashboard.loader import TraceLoader


def build_app(loader: TraceLoader, watcher: object | None = None) -> FastAPI:
    """Build a FastAPI app over the given loader.

    `watcher` is optional and wired in Task 13 (SSE). Passing None disables
    live updates — the rest of the app works unchanged.
    """
    app = FastAPI(title="nuvel dashboard", docs_url=None, redoc_url=None)
    app.state.loader = loader
    app.state.watcher = watcher

    @app.get("/", response_class=HTMLResponse)
    def home() -> HTMLResponse:
        runs = loader.runs()
        body = "\n".join(
            f"<div>{r.trace_id or r.session_id} — {r.agent}</div>" for r in runs[:20]
        )
        return HTMLResponse(f"<h1>nuvel dashboard</h1>{body}")

    @app.get("/run/{trace_id}", response_class=HTMLResponse)
    def run_detail(trace_id: str) -> HTMLResponse:
        run = loader.find_by_id(trace_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        return HTMLResponse(
            f"<h1>{run.trace_id or run.session_id}</h1>"
            f"<div>agent={run.agent}</div>"
        )

    @app.get("/api/runs/feed", response_class=HTMLResponse)
    def runs_feed() -> HTMLResponse:
        runs = loader.runs()
        body = "\n".join(
            f"<div>{r.trace_id or r.session_id} — {r.agent}</div>" for r in runs[:20]
        )
        return HTMLResponse(body)

    return app
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_dashboard_app.py -v`
Expected: All four tests pass.

- [ ] **Step 5: Commit**

```bash
git add nuvel/dashboard/app.py tests/test_dashboard_app.py
git commit -m "feat(dashboard): FastAPI app factory + three placeholder routes"
```

---

## Task 6: CLI subcommand `nuvel dashboard`

Wires the subparser into the main CLI, resolves sources, opens the browser, launches uvicorn.

**Files:**
- Modify: `nuvel/dashboard/cli.py`
- Modify: `nuvel/cli.py` (register the subparser)
- Create: `tests/test_dashboard_cli.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_dashboard_cli.py
"""Tests for nuvel.dashboard.cli."""

from __future__ import annotations

from unittest.mock import patch

from nuvel.cli import build_parser


def test_dashboard_subcommand_is_registered() -> None:
    parser = build_parser()
    args = parser.parse_args(["dashboard", "--port", "9001"])
    assert args.command == "dashboard"
    assert args.port == 9001
    assert args.host == "127.0.0.1"
    assert args.demo is False


def test_dashboard_subcommand_accepts_demo_flag() -> None:
    parser = build_parser()
    args = parser.parse_args(["dashboard", "--demo"])
    assert args.demo is True


def test_dashboard_subcommand_collects_sources() -> None:
    parser = build_parser()
    args = parser.parse_args(["dashboard", "-s", "/a", "-s", "/b"])
    assert args.source == ["/a", "/b"]


def test_dashboard_launch_invokes_uvicorn_and_browser() -> None:
    from nuvel.dashboard.cli import _cmd_dashboard
    from argparse import Namespace
    args = Namespace(host="127.0.0.1", port=8765, source=None, demo=True, open_browser=True)

    with patch("nuvel.dashboard.cli.uvicorn.run") as run, \
         patch("nuvel.dashboard.cli.webbrowser.open") as browser:
        _cmd_dashboard(args)

    assert run.called
    assert browser.called
    assert browser.call_args.args[0] == "http://127.0.0.1:8765"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_dashboard_cli.py -v`
Expected: All four tests fail (subcommand not registered + `_cmd_dashboard` not implemented).

- [ ] **Step 3: Implement `nuvel/dashboard/cli.py`**

```python
"""nuvel dashboard CLI subcommand."""

from __future__ import annotations

import argparse
import sys
import webbrowser
from pathlib import Path

import uvicorn

from nuvel.dashboard.app import build_app
from nuvel.dashboard.loader import TraceLoader
from nuvel.traces_cli import _discover_trace_dirs


def _resolve_sources(args: argparse.Namespace) -> list[Path]:
    if args.demo:
        fixtures = Path(__file__).resolve().parent / "fixtures"
        return [fixtures]
    return _discover_trace_dirs(args.source)


def _port_in_use(host: str, port: int) -> bool:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.2)
        try:
            s.bind((host, port))
        except OSError:
            return True
    return False


def _cmd_dashboard(args: argparse.Namespace) -> int:
    sources = _resolve_sources(args)
    loader = TraceLoader(sources=sources)
    app = build_app(loader)

    if _port_in_use(args.host, args.port):
        print(
            f"Port {args.port} is in use. Try `--port <other>`.",
            file=sys.stderr,
        )
        return 1

    url = f"http://{args.host}:{args.port}"
    print(f"nuvel dashboard → {url}")
    if args.demo:
        print("  (demo mode: bundled fixtures)")

    if args.open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass  # Falls through to manual open from the printed URL.

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


def register(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "dashboard",
        help="Open the local web dashboard over your trace logs.",
    )
    p.add_argument("--host", default="127.0.0.1",
                   help="Bind address (default 127.0.0.1).")
    p.add_argument("--port", type=int, default=8765,
                   help="Bind port (default 8765).")
    p.add_argument("--source", "-s", action="append", default=None,
                   help="Extra trace directory to scan (repeatable). "
                        "Same semantics as `nuvel traces --source`.")
    p.add_argument("--demo", action="store_true",
                   help="Load bundled demo fixtures instead of real traces.")
    p.add_argument("--no-open", dest="open_browser", action="store_false",
                   help="Don't open the browser automatically.")
    p.set_defaults(open_browser=True, func=_cmd_dashboard)
```

- [ ] **Step 4: Modify `nuvel/cli.py`** — register the dashboard subparser right after `traces_cli.register(sub)` and `pricing.register(sub)` (search for `pricing.register(sub)` and add directly below):

```python
    from nuvel import pricing
    pricing.register(sub)

    from nuvel import dashboard
    dashboard.register(sub)

    return parser
```

Also extend the top-of-file docstring's subcommand list:

```python
    nuvel dashboard
        Open the local web dashboard over your trace logs.
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_dashboard_cli.py -v`
Expected: All four tests pass.

- [ ] **Step 6: Smoke-check the CLI exposes the subcommand**

Run: `python -c "from nuvel.cli import main; main(['dashboard', '--help'])"`
Expected: prints usage with `--host`, `--port`, `--source`, `--demo`, `--no-open`.

- [ ] **Step 7: Commit**

```bash
git add nuvel/dashboard/cli.py nuvel/cli.py tests/test_dashboard_cli.py
git commit -m "feat(dashboard): nuvel dashboard subcommand"
```

---

## Task 7: Create demo fixtures

Three curated JSONL files used by `--demo`. Hand-authored, not generated — the content needs to feel real and show off the design.

**Files:**
- Create: `nuvel/dashboard/fixtures/multi_agent.jsonl`
- Create: `nuvel/dashboard/fixtures/with_errors.jsonl`
- Create: `nuvel/dashboard/fixtures/cost_breakdown.jsonl`

- [ ] **Step 1: Create `multi_agent.jsonl`** — a 6-event run showing a sub-agent transfer. Each line is a single JSON object. Trace id `b1aef763f4e48660`, session `demo-session-1`, agent `meta_agent`:

```jsonl
{"trace_id": "b1aef763f4e48660", "session_id": "demo-session-1", "event": "run_start", "timestamp": "2026-05-15T10:00:00+00:00", "agent_depth": 0, "parent_agent": null, "agent": "meta_agent", "user_input": "I want a background agent for technical support that can read Outlook emails, sort them, prioritize, write drafts and escalate to human review."}
{"trace_id": "b1aef763f4e48660", "session_id": "demo-session-1", "event": "agent_start", "timestamp": "2026-05-15T10:00:00.010+00:00", "agent_depth": 1, "parent_agent": null, "agent": "meta_agent"}
{"trace_id": "b1aef763f4e48660", "session_id": "demo-session-1", "event": "llm_request", "timestamp": "2026-05-15T10:00:00.020+00:00", "agent_depth": 1, "parent_agent": null, "call_index": 1, "model": "openrouter/moonshotai/kimi-k2.5", "message_count": 3, "tools_available": ["scaffold_agent", "load_skill", "list_composio_toolkits"]}
{"trace_id": "b1aef763f4e48660", "session_id": "demo-session-1", "event": "llm_response", "timestamp": "2026-05-15T10:00:08.430+00:00", "agent_depth": 1, "parent_agent": null, "call_index": 1, "model_version": "moonshotai/kimi-k2.5-0127", "latency_ms": 8410, "usage": {"prompt_tokens": 2967, "completion_tokens": 313, "total_tokens": 3280}, "cost_usd": 0.0000084, "response_text": "I should search Composio for Outlook tooling first, then load the relevant ADK skills before scaffolding."}
{"trace_id": "b1aef763f4e48660", "session_id": "demo-session-1", "event": "tool_start", "timestamp": "2026-05-15T10:00:08.435+00:00", "agent_depth": 1, "parent_agent": null, "tool": "list_composio_toolkits", "args": {"query": "outlook email"}}
{"trace_id": "b1aef763f4e48660", "session_id": "demo-session-1", "event": "tool_end", "timestamp": "2026-05-15T10:00:09.345+00:00", "agent_depth": 1, "parent_agent": null, "tool": "list_composio_toolkits", "status": "success", "duration_ms": 910, "result": {"status": "ok", "toolkits": ["outlook"], "count": 1}}
{"trace_id": "b1aef763f4e48660", "session_id": "demo-session-1", "event": "agent_transfer", "timestamp": "2026-05-15T10:00:09.350+00:00", "agent_depth": 1, "parent_agent": null, "from_agent": "meta_agent", "to_agent": "outlook_specialist", "event_id": "evt-001"}
{"trace_id": "b1aef763f4e48660", "session_id": "demo-session-1", "event": "agent_start", "timestamp": "2026-05-15T10:00:09.360+00:00", "agent_depth": 2, "parent_agent": "meta_agent", "agent": "outlook_specialist"}
{"trace_id": "b1aef763f4e48660", "session_id": "demo-session-1", "event": "tool_start", "timestamp": "2026-05-15T10:00:09.400+00:00", "agent_depth": 2, "parent_agent": "meta_agent", "tool": "scaffold_agent", "args": {"name": "support-king", "with_composio": true, "with_telegram": false}}
{"trace_id": "b1aef763f4e48660", "session_id": "demo-session-1", "event": "tool_end", "timestamp": "2026-05-15T10:00:10.643+00:00", "agent_depth": 2, "parent_agent": "meta_agent", "tool": "scaffold_agent", "status": "success", "duration_ms": 1243, "result": {"status": "ok", "path": "./generated-agents/support-king", "files_created": 49}}
{"trace_id": "b1aef763f4e48660", "session_id": "demo-session-1", "event": "agent_end", "timestamp": "2026-05-15T10:00:10.700+00:00", "agent_depth": 2, "parent_agent": "meta_agent", "agent": "outlook_specialist"}
{"trace_id": "b1aef763f4e48660", "session_id": "demo-session-1", "event": "agent_end", "timestamp": "2026-05-15T10:00:10.710+00:00", "agent_depth": 1, "parent_agent": null, "agent": "meta_agent"}
{"trace_id": "b1aef763f4e48660", "session_id": "demo-session-1", "event": "run_end", "timestamp": "2026-05-15T10:00:10.720+00:00", "agent_depth": 0, "parent_agent": null, "duration_ms": 10720, "llm_calls": 1, "tool_calls": 4, "total_prompt_tokens": 2967, "total_completion_tokens": 313, "total_tokens": 3280, "total_cost_usd": 0.0000084}
```

- [ ] **Step 2: Create `with_errors.jsonl`** — short run that surfaces `tool_exception` and `llm_error`. Trace id `7ae5f4645a1e47ca`:

```jsonl
{"trace_id": "7ae5f4645a1e47ca", "session_id": "demo-session-2", "event": "run_start", "timestamp": "2026-05-15T10:31:14+00:00", "agent_depth": 0, "parent_agent": null, "agent": "outlook_king", "user_input": "sort the last 50 messages by urgency"}
{"trace_id": "7ae5f4645a1e47ca", "session_id": "demo-session-2", "event": "agent_start", "timestamp": "2026-05-15T10:31:14.010+00:00", "agent_depth": 1, "parent_agent": null, "agent": "outlook_king"}
{"trace_id": "7ae5f4645a1e47ca", "session_id": "demo-session-2", "event": "llm_request", "timestamp": "2026-05-15T10:31:14.020+00:00", "agent_depth": 1, "parent_agent": null, "call_index": 1, "model": "openrouter/moonshotai/kimi-k2.5", "message_count": 2, "tools_available": ["read_inbox", "send_email", "set_label"]}
{"trace_id": "7ae5f4645a1e47ca", "session_id": "demo-session-2", "event": "llm_response", "timestamp": "2026-05-15T10:31:16.840+00:00", "agent_depth": 1, "parent_agent": null, "call_index": 1, "model_version": "moonshotai/kimi-k2.5-0127", "latency_ms": 2820, "usage": {"prompt_tokens": 1820, "completion_tokens": 145, "total_tokens": 1965}, "cost_usd": 0.0000054, "response_text": "I'll start by sending the urgency report to the team channel."}
{"trace_id": "7ae5f4645a1e47ca", "session_id": "demo-session-2", "event": "tool_exception", "timestamp": "2026-05-15T10:31:17.001+00:00", "agent_depth": 1, "parent_agent": null, "tool": "send_email", "error_type": "SMTPAuthenticationError", "error_message": "535 Authentication failed for service@example.com", "args": {"to": "team@example.com", "subject": "Urgency report"}}
{"trace_id": "7ae5f4645a1e47ca", "session_id": "demo-session-2", "event": "llm_error", "timestamp": "2026-05-15T10:31:19.500+00:00", "agent_depth": 1, "parent_agent": null, "call_index": 2, "model": "openrouter/moonshotai/kimi-k2.5", "error_type": "RateLimitError", "error_message": "OpenRouter returned 429 after 3 retries"}
{"trace_id": "7ae5f4645a1e47ca", "session_id": "demo-session-2", "event": "agent_end", "timestamp": "2026-05-15T10:31:19.520+00:00", "agent_depth": 1, "parent_agent": null, "agent": "outlook_king"}
{"trace_id": "7ae5f4645a1e47ca", "session_id": "demo-session-2", "event": "run_end", "timestamp": "2026-05-15T10:31:19.530+00:00", "agent_depth": 0, "parent_agent": null, "duration_ms": 5530, "llm_calls": 1, "tool_calls": 0, "total_prompt_tokens": 1820, "total_completion_tokens": 145, "total_tokens": 1965, "total_cost_usd": 0.0000054}
```

- [ ] **Step 3: Create `cost_breakdown.jsonl`** — 4-turn run with cost on every LLM response. Trace id `c3d4e5f6a7b8c9d0`:

```jsonl
{"trace_id": "c3d4e5f6a7b8c9d0", "session_id": "demo-session-3", "event": "run_start", "timestamp": "2026-05-15T09:58:00+00:00", "agent_depth": 0, "parent_agent": null, "agent": "ppt_king", "user_input": "slide deck about Q3 results, board-friendly tone"}
{"trace_id": "c3d4e5f6a7b8c9d0", "session_id": "demo-session-3", "event": "agent_start", "timestamp": "2026-05-15T09:58:00.010+00:00", "agent_depth": 1, "parent_agent": null, "agent": "ppt_king"}
{"trace_id": "c3d4e5f6a7b8c9d0", "session_id": "demo-session-3", "event": "llm_request", "timestamp": "2026-05-15T09:58:00.020+00:00", "agent_depth": 1, "parent_agent": null, "call_index": 1, "model": "openrouter/google/gemini-3.1-pro-preview", "message_count": 2, "tools_available": ["build_slide", "add_chart", "publish_deck"]}
{"trace_id": "c3d4e5f6a7b8c9d0", "session_id": "demo-session-3", "event": "llm_response", "timestamp": "2026-05-15T09:58:12.000+00:00", "agent_depth": 1, "parent_agent": null, "call_index": 1, "model_version": "google/gemini-3.1-pro-preview", "latency_ms": 11980, "usage": {"prompt_tokens": 4200, "completion_tokens": 1820, "total_tokens": 6020}, "cost_usd": 0.030240, "response_text": "I'll structure the deck around four themes: revenue, margin expansion, segment performance, and outlook."}
{"trace_id": "c3d4e5f6a7b8c9d0", "session_id": "demo-session-3", "event": "tool_start", "timestamp": "2026-05-15T09:58:12.010+00:00", "agent_depth": 1, "parent_agent": null, "tool": "build_slide", "args": {"title": "Q3 in one line", "subtitle": "Revenue +18% YoY, margin +210 bps"}}
{"trace_id": "c3d4e5f6a7b8c9d0", "session_id": "demo-session-3", "event": "tool_end", "timestamp": "2026-05-15T09:58:13.450+00:00", "agent_depth": 1, "parent_agent": null, "tool": "build_slide", "status": "success", "duration_ms": 1440, "result": {"status": "ok", "slide_id": "s-001"}}
{"trace_id": "c3d4e5f6a7b8c9d0", "session_id": "demo-session-3", "event": "llm_request", "timestamp": "2026-05-15T09:58:13.460+00:00", "agent_depth": 1, "parent_agent": null, "call_index": 2, "model": "openrouter/google/gemini-3.1-pro-preview", "message_count": 5, "tools_available": ["build_slide", "add_chart", "publish_deck"]}
{"trace_id": "c3d4e5f6a7b8c9d0", "session_id": "demo-session-3", "event": "llm_response", "timestamp": "2026-05-15T09:58:22.100+00:00", "agent_depth": 1, "parent_agent": null, "call_index": 2, "model_version": "google/gemini-3.1-pro-preview", "latency_ms": 8640, "usage": {"prompt_tokens": 5100, "completion_tokens": 1230, "total_tokens": 6330}, "cost_usd": 0.024960, "response_text": "Adding a waterfall chart for the segment breakdown."}
{"trace_id": "c3d4e5f6a7b8c9d0", "session_id": "demo-session-3", "event": "tool_start", "timestamp": "2026-05-15T09:58:22.110+00:00", "agent_depth": 1, "parent_agent": null, "tool": "add_chart", "args": {"type": "waterfall", "data": "segments"}}
{"trace_id": "c3d4e5f6a7b8c9d0", "session_id": "demo-session-3", "event": "tool_end", "timestamp": "2026-05-15T09:58:23.500+00:00", "agent_depth": 1, "parent_agent": null, "tool": "add_chart", "status": "success", "duration_ms": 1390, "result": {"status": "ok", "chart_id": "c-001"}}
{"trace_id": "c3d4e5f6a7b8c9d0", "session_id": "demo-session-3", "event": "agent_end", "timestamp": "2026-05-15T09:58:23.520+00:00", "agent_depth": 1, "parent_agent": null, "agent": "ppt_king"}
{"trace_id": "c3d4e5f6a7b8c9d0", "session_id": "demo-session-3", "event": "run_end", "timestamp": "2026-05-15T09:58:23.530+00:00", "agent_depth": 0, "parent_agent": null, "duration_ms": 23530, "llm_calls": 2, "tool_calls": 2, "total_prompt_tokens": 9300, "total_completion_tokens": 3050, "total_tokens": 12350, "total_cost_usd": 0.0552}
```

- [ ] **Step 4: Smoke-test demo flag end-to-end**

Run: `python -c "from nuvel.dashboard.loader import TraceLoader; from pathlib import Path; l = TraceLoader([Path('nuvel/dashboard/fixtures')]); print(len(l.runs()), [r.trace_id for r in l.runs()])"`
Expected: prints `3 ['c3d4e5f6a7b8c9d0', '7ae5f4645a1e47ca', 'b1aef763f4e48660']` (newest first).

- [ ] **Step 5: Update `pyproject.toml` to ship fixtures in the wheel**

Find the `[tool.setuptools.package-data]` section (or create it under `[tool.setuptools]`). Add:

```toml
[tool.setuptools.package-data]
"nuvel.dashboard" = ["fixtures/*.jsonl", "templates/*.html", "static/*"]
```

If the section already exists, merge entries; don't duplicate keys.

- [ ] **Step 6: Commit**

```bash
git add nuvel/dashboard/fixtures/*.jsonl pyproject.toml
git commit -m "feat(dashboard): three curated demo fixtures + package-data wiring"
```

---

## Task 8: Verify `--demo` actually loads fixtures via CLI

End-to-end smoke check that the wiring from `--demo` through `_resolve_sources` through `TraceLoader` actually works.

**Files:**
- Modify: `tests/test_dashboard_cli.py`

- [ ] **Step 1: Append a new test**

```python
def test_demo_flag_loads_bundled_fixtures(tmp_path) -> None:
    from argparse import Namespace
    from nuvel.dashboard.cli import _resolve_sources

    args = Namespace(demo=True, source=None)
    sources = _resolve_sources(args)
    assert len(sources) == 1
    assert sources[0].name == "fixtures"
    assert (sources[0] / "multi_agent.jsonl").is_file()
```

- [ ] **Step 2: Run the test**

Run: `pytest tests/test_dashboard_cli.py::test_demo_flag_loads_bundled_fixtures -v`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_dashboard_cli.py
git commit -m "test(dashboard): verify --demo wiring resolves to fixtures dir"
```

---

## Task 9: Templates — base.html, home.html, _run_card.html

Editorial styling. Visual reference: `.superpowers/brainstorm/15339-1778857358/content/page-mockups.html` — the home-page mockup is the source of truth for layout and class hooks.

**Files:**
- Create: `nuvel/dashboard/templates/base.html`
- Create: `nuvel/dashboard/templates/home.html`
- Create: `nuvel/dashboard/templates/_run_card.html`
- Modify: `nuvel/dashboard/app.py` (use Jinja2Templates)

- [ ] **Step 1: Create `nuvel/dashboard/templates/base.html`** — the editorial frame, loaded once:

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{% block title %}nuvel{% endblock %}</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <script src="https://unpkg.com/htmx.org@2.0.4"></script>
  <script src="https://unpkg.com/htmx.org@2.0.4/dist/ext/sse.js"></script>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Source+Serif+4:ital,opsz,wght@0,8..60,400;0,8..60,500;1,8..60,400&display=swap" rel="stylesheet">
  <link rel="stylesheet" href="/static/style.css">
</head>
<body class="bg-[#f8f6f2] text-[#1f1d1a] font-sans antialiased">
  <div class="max-w-5xl mx-auto px-10 py-10">
    <header class="flex items-center justify-between pb-5 border-b border-black/10 mb-8 font-mono text-[11px] text-[#6b6b6b]">
      <a href="/" class="brand">nuvel</a>
      <div>{% block topbar_right %}{{ runs|length if runs is defined else "" }}{% endblock %}</div>
    </header>
    {% block body %}{% endblock %}
  </div>
</body>
</html>
```

- [ ] **Step 2: Create `nuvel/dashboard/templates/home.html`**:

```html
{% extends "base.html" %}
{% block title %}nuvel · home{% endblock %}
{% block topbar_right %}
  <span class="live">live</span> · {{ runs|length }} run{{ '' if runs|length == 1 else 's' }}
{% endblock %}
{% block body %}

<h1 class="ed-h1">
  {% if total_runs == 0 %}
    No runs yet.
  {% elif total_runs == 1 %}
    One run. <span class="accent">{{ total_tokens_short }}</span> tokens.
  {% else %}
    {{ total_runs }} runs. <span class="accent">{{ total_tokens_short }}</span> tokens.
  {% endif %}
</h1>
<div class="ed-sub">Last {{ window_label }} · across {{ agents|length }} agent{{ '' if agents|length == 1 else 's' }}{% if agents %} · {{ agents|join(", ") }}{% endif %}</div>

<div class="ed-stats">
  <div class="ed-stat"><div class="ed-stat-num">{{ total_runs }}</div><div class="ed-stat-lbl">Runs</div></div>
  <div class="ed-stat"><div class="ed-stat-num">{{ total_tokens_short }}</div><div class="ed-stat-lbl">Tokens</div></div>
  <div class="ed-stat"><div class="ed-stat-num">{{ total_cost_label }}</div><div class="ed-stat-lbl">Spend</div></div>
  <div class="ed-stat"><div class="ed-stat-num {% if error_count %}err{% endif %}">{{ error_count }}</div><div class="ed-stat-lbl">Errors</div></div>
</div>

{% if recent_error %}
<div class="ed-error-callout">
  <div class="title">A run failed recently.</div>
  <div><strong>{{ recent_error.agent }}</strong> · <code>{{ recent_error.trace_id_short }}</code> · {{ recent_error.summary }}</div>
</div>
{% endif %}

<div class="ed-section-lbl">Recent activity</div>
<div id="feed" hx-ext="sse" sse-connect="/sse" sse-swap="run" hx-swap="afterbegin">
  {% for run in runs %}
    {% include "_run_card.html" %}
  {% else %}
    <div class="ed-empty">
      <p>No runs yet. Try <code>nuvel dashboard --demo</code> to see what this looks like with sample data.</p>
    </div>
  {% endfor %}
</div>

{% endblock %}
```

- [ ] **Step 3: Create `nuvel/dashboard/templates/_run_card.html`**:

```html
<a href="/run/{{ run.trace_id or run.session_id }}" class="ed-run">
  <div class="time">{{ run.started_at_short }}</div>
  <div>
    <div class="agent">{{ run.agent_display }}</div>
    <div class="input">{{ run.user_input_short or run.headline or "" }}</div>
  </div>
  <div class="stat">{{ run.llm_calls }} turn{{ '' if run.llm_calls == 1 else 's' }}</div>
  <div class="stat">{{ run.tokens_short }}</div>
  {% if run.has_error %}
    <div class="pill err">error</div>
  {% else %}
    <div class="pill">ok</div>
  {% endif %}
  <div class="arrow">→</div>
</a>
```

- [ ] **Step 4: Modify `nuvel/dashboard/app.py`** — switch from raw HTML strings to Jinja2 templates and a small `RunView` helper. Replace the file with:

```python
"""FastAPI app factory for the dashboard.

Renders Jinja templates over `Run` records. Wraps each `Run` in a small
`RunView` adapter that pre-computes display fields the templates use, so
the templates stay logic-light.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from nuvel.dashboard.headlines import describe_run
from nuvel.dashboard.loader import TraceLoader
from nuvel.traces_cli import Run


_DASHBOARD_DIR = Path(__file__).resolve().parent
_ERROR_EVENTS = {"llm_error", "tool_exception"}


def _short_int(n: int) -> str:
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


def _cost_label(cost: float | None) -> str:
    return "—" if cost in (None, 0) else f"${cost:.3f}"


def _run_has_error(run: Run) -> bool:
    for ev in run.events:
        if ev.get("event") in _ERROR_EVENTS:
            return True
        if ev.get("event") == "tool_end" and ev.get("status") == "error":
            return True
    # Even without events kept, check schema-level signals.
    return False


@dataclass
class RunView:
    """Pre-computed display fields wrapped over a Run."""
    run: Run
    started_at_short: str
    agent_display: str
    user_input_short: str
    headline: str
    llm_calls: int
    tokens_short: str
    cost_label: str
    has_error: bool

    @property
    def trace_id(self) -> str | None: return self.run.trace_id
    @property
    def session_id(self) -> str: return self.run.session_id


def _view(run: Run) -> RunView:
    ts = (run.started_at or run.ended_at or "")[:16].replace("T", " ")
    user = (run.user_input or "")
    user_short = user[:80] + ("…" if len(user) > 80 else "")
    return RunView(
        run=run,
        started_at_short=ts,
        agent_display=run.agent,
        user_input_short=user_short,
        headline=describe_run(run),
        llm_calls=run.llm_calls or 0,
        tokens_short=_short_int(run.total_tokens or 0),
        cost_label=_cost_label(run.cost_usd),
        has_error=_run_has_error(run),
    )


def build_app(loader: TraceLoader, watcher: object | None = None) -> FastAPI:
    app = FastAPI(title="nuvel dashboard", docs_url=None, redoc_url=None)
    app.state.loader = loader
    app.state.watcher = watcher
    templates = Jinja2Templates(directory=str(_DASHBOARD_DIR / "templates"))
    app.mount("/static", StaticFiles(directory=str(_DASHBOARD_DIR / "static")), name="static")

    def _home_context(runs: list[Run]) -> dict:
        views = [_view(r) for r in runs]
        agents = sorted({r.agent.split("/")[0] for r in runs})
        total_tokens = sum(r.total_tokens or 0 for r in runs)
        total_cost = sum(r.cost_usd or 0 for r in runs if r.cost_usd is not None)
        return {
            "runs": views[:20],
            "total_runs": len(views),
            "agents": agents,
            "total_tokens_short": _short_int(total_tokens) if total_tokens else "0",
            "total_cost_label": _cost_label(total_cost or None),
            "error_count": sum(1 for v in views if v.has_error),
            "recent_error": next((v for v in views if v.has_error), None),
            "window_label": "all time",
        }

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request) -> HTMLResponse:
        runs = loader.runs()
        return templates.TemplateResponse(
            "home.html",
            {"request": request, **_home_context(runs)},
        )

    @app.get("/run/{trace_id}", response_class=HTMLResponse)
    def run_detail(request: Request, trace_id: str) -> HTMLResponse:
        run = loader.find_by_id(trace_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        return templates.TemplateResponse(
            "run_detail.html",
            {"request": request, "run": _view(run), "raw": run},
        )

    @app.get("/api/runs/feed", response_class=HTMLResponse)
    def runs_feed(request: Request) -> HTMLResponse:
        runs = loader.runs()
        return templates.TemplateResponse(
            "_feed.html",
            {"request": request, "runs": [_view(r) for r in runs[:20]]},
        )

    return app
```

Note `_feed.html` is referenced — create it in the next step.

- [ ] **Step 5: Create `nuvel/dashboard/templates/_feed.html`** (wraps a list of runs for the feed partial endpoint):

```html
{% for run in runs %}{% include "_run_card.html" %}{% endfor %}
```

- [ ] **Step 6: Re-run app tests**

Run: `pytest tests/test_dashboard_app.py -v`
Expected: All four tests still pass (templates render and contain the trace_id).

- [ ] **Step 7: Manual visual check**

Run: `nuvel dashboard --demo --no-open --port 8766`
Then in another terminal: `curl -s http://127.0.0.1:8766/ | head -40`
Expected: HTML containing `nuvel dashboard`, the hero headline ("3 runs"), demo trace ids. Kill the server with Ctrl-C.

- [ ] **Step 8: Commit**

```bash
git add nuvel/dashboard/templates/ nuvel/dashboard/app.py
git commit -m "feat(dashboard): Jinja templates for base + home + run card"
```

---

## Task 10: Templates — run_detail.html, _event_row.html

The run detail page is the second visual surface and the place the Editorial style does its most distinctive work — `llm_response.response_text` rendered in orange italic serif.

**Files:**
- Create: `nuvel/dashboard/templates/run_detail.html`
- Create: `nuvel/dashboard/templates/_event_row.html`

- [ ] **Step 1: Create `nuvel/dashboard/templates/run_detail.html`**

```html
{% extends "base.html" %}
{% block title %}nuvel · {{ run.trace_id[:8] if run.trace_id else run.session_id[:8] }}{% endblock %}
{% block topbar_right %}session {{ run.session_id[:8] }}…{% endblock %}
{% block body %}

<div class="ed-back"><a href="/" class="arrow">←</a> all runs</div>
<div class="ed-id">{{ run.trace_id or run.session_id }} · {{ run.started_at_short }} · {{ raw.duration_ms / 1000 if raw.duration_ms else 0 }}s</div>
<h1 class="ed-detail-h">{{ run.headline }}</h1>

{% if run.user_input_short %}
<div class="ed-quote">"{{ raw.user_input or run.user_input_short }}"</div>
{% endif %}

<div class="ed-summary">
  <div class="ed-stat"><div class="ed-stat-num">{{ raw.llm_calls or 0 }}</div><div class="ed-stat-lbl">LLM calls</div></div>
  <div class="ed-stat"><div class="ed-stat-num">{{ raw.tool_calls or 0 }}</div><div class="ed-stat-lbl">Tool calls</div></div>
  <div class="ed-stat"><div class="ed-stat-num">{{ run.tokens_short }}</div><div class="ed-stat-lbl">Tokens</div></div>
  <div class="ed-stat"><div class="ed-stat-num">{{ run.cost_label }}</div><div class="ed-stat-lbl">Cost</div></div>
  <div class="ed-stat"><div class="ed-stat-num">{{ (raw.duration_ms / 1000)|round(1) if raw.duration_ms else 0 }}s</div><div class="ed-stat-lbl">Duration</div></div>
  <div class="ed-stat"><div class="ed-stat-num {% if run.has_error %}err{% endif %}">{{ '1+' if run.has_error else '0' }}</div><div class="ed-stat-lbl">Errors</div></div>
</div>

<div class="ed-section-lbl">Thinking timeline</div>
<div class="ed-timeline">
  {% for ev in raw.events %}
    {% include "_event_row.html" %}
  {% endfor %}
</div>

{% endblock %}
```

- [ ] **Step 2: Create `nuvel/dashboard/templates/_event_row.html`**

```html
{% set kind = ev.event %}
{% set ts = (ev.timestamp or "")[11:23] %}
{% set depth = ev.agent_depth or 0 %}
<div class="ed-event {% if kind == 'llm_response' %}llm-resp{% elif kind == 'agent_transfer' %}transfer{% elif kind in ('tool_exception','llm_error') or (kind == 'tool_end' and ev.status == 'error') %}err{% endif %}">
  <div class="ts">{{ ts }}</div>
  <div class="marker {% if depth >= 2 %}depth{% endif %}">
    {% if kind == 'run_start' %}▶{% elif kind == 'run_end' %}■{% elif kind == 'agent_transfer' %}⇢{% elif kind == 'tool_start' %}▸{% elif kind == 'tool_end' %}◂{% elif kind == 'llm_request' %}→{% elif kind == 'llm_response' %}←{% else %}·{% endif %}
  </div>
  <div class="body">
    {% if kind == 'llm_response' %}
      {{ ev.response_text or "" }}
      <div class="detail">{{ ev.latency_ms }}ms · {{ ev.usage.total_tokens or 0 }} tokens · {{ (ev.function_calls or [])|length }} tool calls planned</div>
    {% elif kind == 'tool_start' %}
      <span class="kind">tool</span> <strong>{{ ev.tool }}</strong>
      {% if ev.args %}<span class="detail">{{ ev.args | tojson }}</span>{% endif %}
    {% elif kind == 'tool_end' %}
      <span class="kind">tool</span> <strong>{{ ev.tool }}</strong>
      <span class="detail">→ {{ ev.status }}, {{ ev.duration_ms or 0 }}ms</span>
    {% elif kind == 'agent_transfer' %}
      <span class="kind">transfer</span> {{ ev.from_agent }} <strong>→</strong> {{ ev.to_agent }}
    {% elif kind == 'tool_exception' %}
      <span class="kind">tool error</span> <strong>{{ ev.tool }}</strong>
      <span class="detail">{{ ev.error_type }}: {{ ev.error_message }}</span>
    {% elif kind == 'llm_error' %}
      <span class="kind">llm error</span>
      <span class="detail">{{ ev.error_type }}: {{ ev.error_message }}</span>
    {% elif kind == 'run_start' %}
      <span class="kind">run start</span>
    {% elif kind == 'run_end' %}
      <span class="kind">run end</span>
      <span class="detail">{{ ev.llm_calls or 0 }} LLM · {{ ev.tool_calls or 0 }} tool · {{ ev.total_tokens or 0 }} tokens</span>
    {% elif kind == 'llm_request' %}
      <span class="kind">llm request</span>
      <span class="detail">{{ ev.model }} · {{ ev.message_count or 0 }} msgs · {{ (ev.tools_available or [])|length }} tools</span>
    {% else %}
      <span class="kind">{{ kind }}</span>
    {% endif %}
  </div>
</div>
```

- [ ] **Step 3: Re-run app tests**

Run: `pytest tests/test_dashboard_app.py -v`
Expected: All four pass (run_detail route works against the template).

- [ ] **Step 4: Manual visual check**

Run: `nuvel dashboard --demo --no-open --port 8766` in one terminal.
`curl -s http://127.0.0.1:8766/run/b1aef763 | grep -E "thinking|tool_start|agent_transfer" | head`
Expected: lines mentioning the thinking timeline, tool events, and the agent transfer.

- [ ] **Step 5: Commit**

```bash
git add nuvel/dashboard/templates/run_detail.html nuvel/dashboard/templates/_event_row.html
git commit -m "feat(dashboard): run detail template with thinking timeline"
```

---

## Task 11: Editorial style.css

All CSS class hooks the templates use are pinned in this file. The design was validated as a mockup during brainstorming; the rules below are the production-ready version distilled from it.

**Files:**
- Create: `nuvel/dashboard/static/style.css`

- [ ] **Step 1: Create `nuvel/dashboard/static/style.css`** with this exact content:

```css
/* Editorial style — see docs/superpowers/specs/2026-05-15-traces-dashboard-design.md */

:root {
  --ed-bg: #f8f6f2;
  --ed-fg: #1f1d1a;
  --ed-muted: #6b6b6b;
  --ed-accent: #c2410c;
}

body { font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", system-ui, sans-serif; }

.brand { font-family: "Source Serif 4", "Source Serif Pro", Georgia, serif; font-style: italic; font-size: 16px; color: var(--ed-fg); letter-spacing: -0.01em; text-decoration: none; }
.live::before { content: "●"; color: #16a34a; margin-right: 6px; animation: pulse 2s infinite; }
@keyframes pulse { 50% { opacity: 0.3; } }
.accent { color: var(--ed-accent); }

/* Home hero */
.ed-h1 { font-family: "Source Serif 4", "Source Serif Pro", Georgia, serif; font-size: 44px; font-weight: 500; line-height: 1.05; letter-spacing: -0.02em; margin: 0 0 8px; }
.ed-h1 .accent { font-style: italic; font-weight: 400; }
.ed-sub { font-size: 13px; color: var(--ed-muted); margin-bottom: 32px; }

/* Stats grid */
.ed-stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 1px; background: rgba(0,0,0,0.08); border: 1px solid rgba(0,0,0,0.08); border-radius: 6px; overflow: hidden; margin-bottom: 36px; }
.ed-stat { background: var(--ed-bg); padding: 18px 20px; }
.ed-stat-num { font-family: "Source Serif 4", "Source Serif Pro", Georgia, serif; font-size: 28px; font-weight: 500; line-height: 1; letter-spacing: -0.01em; color: var(--ed-fg); }
.ed-stat-num.err { color: #b45309; }
.ed-stat-lbl { font-size: 9px; text-transform: uppercase; letter-spacing: 0.08em; color: var(--ed-muted); margin-top: 6px; font-weight: 600; }

/* Section labels */
.ed-section-lbl { font-size: 9px; text-transform: uppercase; letter-spacing: 0.1em; color: var(--ed-muted); font-weight: 600; margin-bottom: 14px; }

/* Recent runs feed */
a.ed-run { color: inherit; text-decoration: none; display: grid; grid-template-columns: 56px 1fr auto auto auto 16px; gap: 16px; align-items: center; padding: 14px 0; border-bottom: 1px solid rgba(0,0,0,0.06); font-size: 13px; }
a.ed-run:hover { background: rgba(0,0,0,0.02); }
.ed-run .time { font-family: ui-monospace, monospace; font-size: 11px; color: #9a9a9a; }
.ed-run .agent { font-weight: 500; }
.ed-run .input { color: var(--ed-muted); font-style: italic; font-size: 12px; }
.ed-run .stat { font-family: ui-monospace, monospace; font-size: 11px; color: var(--ed-muted); }
.ed-run .pill { font-size: 9px; padding: 2px 7px; border-radius: 100px; background: #ede9e0; color: var(--ed-muted); font-weight: 600; letter-spacing: 0.04em; text-transform: uppercase; }
.ed-run .pill.err { background: #fef3c7; color: #92400e; }
.ed-run .arrow { color: var(--ed-accent); font-size: 14px; }

/* Error callout */
.ed-error-callout { background: #fef3c7; border-left: 3px solid var(--ed-accent); padding: 14px 20px; margin: 24px 0; font-size: 12px; }
.ed-error-callout .title { font-family: "Source Serif 4", "Source Serif Pro", Georgia, serif; font-size: 15px; margin-bottom: 4px; color: #92400e; }
.ed-error-callout code { font-family: ui-monospace, monospace; font-size: 11px; }

/* Empty state */
.ed-empty { padding: 40px 0; color: var(--ed-muted); }
.ed-empty code { font-family: ui-monospace, monospace; background: #ede9e0; padding: 2px 5px; border-radius: 3px; }

/* Run detail header */
.ed-back { font-size: 11px; color: var(--ed-muted); margin-bottom: 18px; }
.ed-back a.arrow { color: var(--ed-accent); margin-right: 6px; text-decoration: none; }
.ed-id { font-family: ui-monospace, monospace; font-size: 11px; color: #9a9a9a; margin-bottom: 4px; }
.ed-detail-h { font-family: "Source Serif 4", "Source Serif Pro", Georgia, serif; font-size: 32px; font-weight: 500; line-height: 1.1; letter-spacing: -0.02em; margin: 0 0 6px; }
.ed-quote { font-family: "Source Serif 4", "Source Serif Pro", Georgia, serif; font-style: italic; font-size: 18px; line-height: 1.4; color: #3a3a3a; border-left: 2px solid var(--ed-accent); padding-left: 18px; margin: 24px 0; }

/* Run detail summary grid */
.ed-summary { display: grid; grid-template-columns: repeat(6, 1fr); gap: 1px; background: rgba(0,0,0,0.08); border: 1px solid rgba(0,0,0,0.08); border-radius: 6px; overflow: hidden; margin-bottom: 32px; }
.ed-summary .ed-stat-num { font-size: 18px; }

/* Thinking timeline */
.ed-timeline { font-size: 12px; }
.ed-event { display: grid; grid-template-columns: 60px 22px 1fr; gap: 14px; padding: 9px 0; border-bottom: 1px solid rgba(0,0,0,0.04); align-items: flex-start; }
.ed-event .ts { font-family: ui-monospace, monospace; color: #9a9a9a; font-size: 10px; }
.ed-event .marker { font-family: ui-monospace, monospace; font-size: 10px; text-align: right; color: #9a9a9a; }
.ed-event .marker.depth { padding-left: 8px; border-left: 1px solid rgba(194,65,12,0.2); margin-left: 4px; }
.ed-event .body { line-height: 1.5; }
.ed-event .kind { font-family: ui-monospace, monospace; font-size: 10px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--ed-muted); font-weight: 600; margin-right: 8px; }
.ed-event .detail { color: #3a3a3a; font-family: ui-monospace, monospace; font-size: 10px; }
.ed-event.transfer .kind { color: var(--ed-accent); }
.ed-event.err .kind { color: #b45309; }
.ed-event.llm-resp .body { color: var(--ed-fg); font-style: italic; font-family: "Source Serif 4", "Source Serif Pro", Georgia, serif; font-size: 13px; }
```

- [ ] **Step 2: Manual visual check** (this is the moment to actually look at it)

Run: `nuvel dashboard --demo --port 8766`
Browser opens; spend 60 seconds clicking around. Check:
- Home headline reads `"3 runs."` with the orange accent on the token count.
- Stats grid renders four cards (Runs / Tokens / Spend / Errors).
- Error callout shows the SMTPAuthenticationError run.
- Recent activity feed renders three rows.
- Click any row → run detail loads.
- On the run detail page, the `llm_response` events render in orange italic serif.

If anything looks broken, fix `style.css` (most likely culprit: missing class hook).

- [ ] **Step 3: Commit**

```bash
git add nuvel/dashboard/static/style.css
git commit -m "feat(dashboard): editorial style.css — Source Serif headline, warm palette"
```

---

## Task 12: Watcher — JSONL polling → asyncio.Queue (TDD)

**Files:**
- Modify: `nuvel/dashboard/watcher.py`
- Create: `tests/test_dashboard_watcher.py`

- [ ] **Step 1: Write the failing integration test**

```python
# tests/test_dashboard_watcher.py
"""Tests for nuvel.dashboard.watcher."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from nuvel.dashboard.watcher import RunWatcher
from nuvel.traces_cli import Run


def _write_run(path: Path, trace_id: str, ts: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    records = [
        {"trace_id": trace_id, "session_id": "s", "event": "run_start",
         "timestamp": ts, "agent": "test_agent", "user_input": "hi"},
        {"trace_id": trace_id, "session_id": "s", "event": "run_end",
         "timestamp": ts, "duration_ms": 100, "llm_calls": 1, "tool_calls": 0,
         "total_tokens": 50},
    ]
    with path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


@pytest.mark.asyncio
async def test_watcher_emits_new_run_when_file_appears(tmp_path: Path) -> None:
    watcher = RunWatcher(sources=[tmp_path], poll_seconds=0.2)
    queue: asyncio.Queue[Run] = asyncio.Queue()
    task = asyncio.create_task(watcher.run(queue))

    # Give the watcher a beat to do its first scan (which establishes baseline).
    await asyncio.sleep(0.5)

    _write_run(tmp_path / "2026-05-15_new.jsonl", "newrun01", "2026-05-15T11:00:00+00:00")

    run = await asyncio.wait_for(queue.get(), timeout=3.0)
    assert run.trace_id == "newrun01"

    watcher.stop()
    await asyncio.wait_for(task, timeout=2.0)


@pytest.mark.asyncio
async def test_watcher_ignores_files_seen_in_baseline(tmp_path: Path) -> None:
    _write_run(tmp_path / "2026-05-15_pre.jsonl", "preexist", "2026-05-15T10:00:00+00:00")

    watcher = RunWatcher(sources=[tmp_path], poll_seconds=0.2)
    queue: asyncio.Queue[Run] = asyncio.Queue()
    task = asyncio.create_task(watcher.run(queue))

    await asyncio.sleep(0.5)

    # No new files. Queue should stay empty.
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(queue.get(), timeout=0.6)

    watcher.stop()
    await asyncio.wait_for(task, timeout=2.0)
```

(`pytest-asyncio` was added in Task 1 and `asyncio_mode = "auto"` set in pyproject — no extra setup here.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_dashboard_watcher.py -v`
Expected: Both fail with `ImportError: cannot import name 'RunWatcher'`.

- [ ] **Step 3: Implement `nuvel/dashboard/watcher.py`**

```python
"""JSONL trace file watcher.

Polls the configured source directories at a fixed interval. When a file
appears or grows, re-parses it and emits any newly-seen `Run` objects to
an `asyncio.Queue` the SSE endpoint drains.

Polling over inotify because cross-platform behavior is consistent and
1s is well within HTMX's perceived-real-time threshold for a demo.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from nuvel.traces_cli import (
    Run,
    _iter_trace_files,
    _parse_file_runs,
)

logger = logging.getLogger(__name__)


class RunWatcher:
    """Polls trace dirs and emits newly-arrived runs onto a queue."""

    def __init__(self, sources: list[Path], poll_seconds: float = 1.0) -> None:
        self._sources = sources
        self._poll = poll_seconds
        self._seen: set[str] = set()
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    def _key(self, run: Run) -> str:
        return run.trace_id or f"{run.session_id}:{run.started_at}"

    def _snapshot(self) -> dict[str, Run]:
        out: dict[str, Run] = {}
        for f in _iter_trace_files(self._sources):
            for run in _parse_file_runs(f, keep_events=False):
                out[self._key(run)] = run
        return out

    async def run(self, queue: asyncio.Queue[Run]) -> None:
        # Baseline scan: anything present at startup is "already known".
        try:
            baseline = self._snapshot()
        except Exception:  # noqa: BLE001 — never crash the watcher
            logger.exception("watcher baseline scan failed")
            baseline = {}
        self._seen = set(baseline.keys())

        while not self._stop.is_set():
            try:
                await asyncio.sleep(self._poll)
                current = self._snapshot()
            except Exception:  # noqa: BLE001
                logger.exception("watcher scan failed")
                continue

            new_keys = set(current.keys()) - self._seen
            for k in new_keys:
                await queue.put(current[k])
            self._seen |= new_keys
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_dashboard_watcher.py -v`
Expected: Both tests pass within ~3 seconds.

- [ ] **Step 5: Commit**

```bash
git add nuvel/dashboard/watcher.py tests/test_dashboard_watcher.py
git commit -m "feat(dashboard): RunWatcher polling new runs onto asyncio.Queue"
```

---

## Task 13: SSE endpoint + wire watcher into the app

**Files:**
- Modify: `nuvel/dashboard/app.py`
- Modify: `nuvel/dashboard/cli.py` (start the watcher)
- Modify: `tests/test_dashboard_app.py`

- [ ] **Step 1: Add an SSE test**

Append to `tests/test_dashboard_app.py`:

```python
def test_sse_endpoint_is_event_stream_when_watcher_attached(tmp_path) -> None:
    import asyncio
    from nuvel.dashboard.watcher import RunWatcher

    watcher = RunWatcher(sources=[tmp_path], poll_seconds=0.2)
    loader = TraceLoader(sources=[tmp_path])
    client = TestClient(build_app(loader, watcher))

    # Pass stream=True so TestClient lets us pull the header without consuming.
    with client.stream("GET", "/sse") as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")


def test_sse_endpoint_404_when_no_watcher(loader) -> None:
    client = TestClient(build_app(loader, watcher=None))
    r = client.get("/sse")
    assert r.status_code == 404
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `pytest tests/test_dashboard_app.py::test_sse_endpoint_is_event_stream_when_watcher_attached tests/test_dashboard_app.py::test_sse_endpoint_404_when_no_watcher -v`
Expected: both fail (no `/sse` route yet).

- [ ] **Step 3: Add the SSE route to `nuvel/dashboard/app.py`**

Inside `build_app`, after the `/api/runs/feed` route:

```python
    from fastapi import Response
    from fastapi.responses import StreamingResponse
    import asyncio

    @app.get("/sse")
    async def sse_stream() -> StreamingResponse:
        if watcher is None:
            raise HTTPException(status_code=404, detail="Live updates disabled")
        queue: asyncio.Queue[Run] = asyncio.Queue()
        # Spawn the watcher as a background task on the running loop.
        task = asyncio.create_task(watcher.run(queue))

        async def event_stream():
            try:
                while True:
                    run = await queue.get()
                    view = _view(run)
                    html = templates.get_template("_run_card.html").render(run=view)
                    # Collapse to one line per SSE protocol.
                    one_line = html.replace("\n", " ")
                    yield f"event: run\ndata: {one_line}\n\n"
            finally:
                watcher.stop()
                task.cancel()

        return StreamingResponse(event_stream(), media_type="text/event-stream")
```

(Move the `from fastapi import …` and `import asyncio` to the top of the file rather than inline if you prefer.)

- [ ] **Step 4: Wire the watcher into the CLI launch**

Modify `nuvel/dashboard/cli.py:_cmd_dashboard` to construct a watcher when not in demo mode and pass it to `build_app`. Replace the body of `_cmd_dashboard`:

```python
def _cmd_dashboard(args: argparse.Namespace) -> int:
    sources = _resolve_sources(args)
    loader = TraceLoader(sources=sources)

    from nuvel.dashboard.watcher import RunWatcher
    watcher = None if args.demo else RunWatcher(sources=sources)

    app = build_app(loader, watcher=watcher)

    if _port_in_use(args.host, args.port):
        print(
            f"Port {args.port} is in use. Try `--port <other>`.",
            file=sys.stderr,
        )
        return 1

    url = f"http://{args.host}:{args.port}"
    print(f"nuvel dashboard → {url}")
    if args.demo:
        print("  (demo mode: bundled fixtures · live updates disabled)")

    if args.open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0
```

- [ ] **Step 5: Run all dashboard tests**

Run: `pytest tests/test_dashboard_*.py -v`
Expected: every test passes.

- [ ] **Step 6: Manual live-update check**

In one terminal: `nuvel dashboard --port 8766 --source /tmp/livetest` (create the dir first).
In another terminal:

```bash
mkdir -p /tmp/livetest
cat > /tmp/livetest/2026-05-15_live.jsonl <<'EOF'
{"trace_id": "live0001", "session_id": "ls", "event": "run_start", "timestamp": "2026-05-15T12:00:00+00:00", "agent": "live_test", "user_input": "live update test"}
{"trace_id": "live0001", "session_id": "ls", "event": "run_end", "timestamp": "2026-05-15T12:00:01+00:00", "duration_ms": 1000, "llm_calls": 1, "tool_calls": 0, "total_tokens": 100}
EOF
```

Watch the browser — a new run row should appear at the top of the feed within ~1 second without you refreshing.

Kill the server with Ctrl-C. `rm -rf /tmp/livetest`.

- [ ] **Step 7: Commit**

```bash
git add nuvel/dashboard/app.py nuvel/dashboard/cli.py tests/test_dashboard_app.py
git commit -m "feat(dashboard): SSE endpoint streaming new runs via watcher"
```

---

## Task 14: End-to-end smoke test + docs

**Files:**
- Create: `tests/test_dashboard_smoke.py`
- Modify: `docs/reference/cli.md`
- Modify: `README.md`

- [ ] **Step 1: Write the smoke test**

```python
# tests/test_dashboard_smoke.py
"""End-to-end smoke: launch dashboard --demo, hit the two pages."""

from __future__ import annotations

import socket
import subprocess
import sys
import time

import httpx


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_demo_smoke() -> None:
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "nuvel.cli", "dashboard",
         "--demo", "--no-open", "--port", str(port)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.time() + 8.0
        while time.time() < deadline:
            try:
                r = httpx.get(f"http://127.0.0.1:{port}/", timeout=1.0)
                if r.status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.2)
        else:
            raise AssertionError("dashboard did not become ready within 8s")

        r = httpx.get(f"http://127.0.0.1:{port}/", timeout=2.0)
        assert r.status_code == 200
        assert "nuvel" in r.text.lower()
        assert "b1aef763f4e48660" in r.text or "b1aef76" in r.text

        r = httpx.get(f"http://127.0.0.1:{port}/run/b1aef763", timeout=2.0)
        assert r.status_code == 200
        assert "thinking timeline" in r.text.lower()
    finally:
        proc.terminate()
        proc.wait(timeout=5)
```

Note: `nuvel.cli:main` needs to support `python -m nuvel.cli`. Check via `python -m nuvel.cli --help`. If it fails, add a `if __name__ == "__main__": main()` clause at the bottom of `nuvel/cli.py`. (Or `python -m nuvel` if a `__main__.py` exists.)

- [ ] **Step 2: Run the smoke test**

Run: `pytest tests/test_dashboard_smoke.py -v`
Expected: PASS within ~5 seconds.

- [ ] **Step 3: Add a docs section to `docs/reference/cli.md`**

Append below the `## nuvel pricing` section (and before `## nuvel run`):

```markdown
## `nuvel dashboard`

Open the local web dashboard over your trace logs.

```bash
nuvel dashboard [--host HOST] [--port PORT] [--source DIR ...] [--demo] [--no-open]
```

| Flag | Default | Notes |
|---|---|---|
| `--host` | `127.0.0.1` | Bind address. Localhost by design. |
| `--port` | `8765` | Bind port. |
| `--source`, `-s` | auto-discovery | Same semantics as `nuvel traces --source`. Repeatable. |
| `--demo` | off | Load bundled demo fixtures instead of real traces. |
| `--no-open` | off | Don't open the browser automatically. |

Two pages: a home view (hero + headline stats + recent activity) and a per-run detail with the thinking timeline. Live updates push new runs as the watcher sees them.
```

- [ ] **Step 4: Add a row to the README cheat-sheet**

Find the table near line 162 of `README.md` (lines listing `nuvel traces`, `nuvel pricing`). Add directly after `nuvel pricing`:

```markdown
| `nuvel dashboard [--demo]` | Open a local web dashboard over your trace logs |
```

- [ ] **Step 5: Run the full dashboard test suite to make sure docs didn't break anything**

Run: `pytest tests/test_dashboard_*.py -v`
Expected: every test passes.

- [ ] **Step 6: Commit**

```bash
git add tests/test_dashboard_smoke.py docs/reference/cli.md README.md
git commit -m "test(dashboard): subprocess smoke + docs

Smoke test boots nuvel dashboard --demo in a subprocess and asserts
home + run detail return 200 with expected content. Adds dashboard
section to docs/reference/cli.md and a row in the README cheat-sheet."
```

---

## Final integration check

- [ ] **Step 1: Full test suite**

Run: `pytest tests/ -v`
Expected: everything green, including pre-existing tests.

- [ ] **Step 2: Manual demo walkthrough**

`nuvel dashboard --demo` — confirm the experience matches the acceptance criteria from the spec:
- Polished, intentional-looking home page within five seconds. ✓
- Click any run → detail page with readable timeline. ✓
- Live updates work (re-run Task 13 Step 6's manual check, this time without `--demo`). ✓

- [ ] **Step 3: Open the PR**

```bash
git push -u origin worktree-traces-dashboard
gh pr create --title "feat(dashboard): nuvel dashboard — local web command center" --body-file <(echo "Implements docs/superpowers/specs/2026-05-15-traces-dashboard-design.md. See that spec for the full design rationale. Plan executed at docs/superpowers/plans/2026-05-15-traces-dashboard.md.")
```
