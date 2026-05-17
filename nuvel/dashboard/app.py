"""FastAPI app factory for the dashboard.

Renders Jinja templates over `Run` records. Wraps each `Run` in a small
`RunView` adapter that pre-computes display fields the templates use, so
the templates stay logic-light.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
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
    trace_id_short: str
    summary: str

    @property
    def trace_id(self) -> str | None: return self.run.trace_id
    @property
    def session_id(self) -> str: return self.run.session_id


def _view(run: Run) -> RunView:
    ts = (run.started_at or run.ended_at or "")[:16].replace("T", " ")
    user = (run.user_input or "")
    user_short = user[:80] + ("…" if len(user) > 80 else "")
    short = (run.trace_id or run.session_id or "")[:8]
    headline = describe_run(run)
    return RunView(
        run=run,
        started_at_short=ts,
        agent_display=run.agent,
        user_input_short=user_short,
        headline=headline,
        llm_calls=run.llm_calls or 0,
        tokens_short=_short_int(run.total_tokens or 0),
        cost_label=_cost_label(run.cost_usd),
        has_error=_run_has_error(run),
        trace_id_short=short,
        summary=headline,
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
        return templates.TemplateResponse(request, "home.html", _home_context(runs))

    @app.get("/run/{trace_id}", response_class=HTMLResponse)
    def run_detail(request: Request, trace_id: str) -> HTMLResponse:
        run = loader.find_by_id(trace_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        return templates.TemplateResponse(request, "run_detail.html", {"run": _view(run), "raw": run})

    @app.get("/api/runs/feed", response_class=HTMLResponse)
    def runs_feed(request: Request) -> HTMLResponse:
        runs = loader.runs()
        return templates.TemplateResponse(request, "_feed.html", {"runs": [_view(r) for r in runs[:20]]})

    @app.get("/sse")
    async def sse_stream() -> StreamingResponse:
        if watcher is None:
            raise HTTPException(status_code=404, detail="Live updates disabled")

        # Each SSE connection gets its own watcher + queue so multi-tab and
        # reconnect work cleanly. Disconnect tears down only this client's loop.
        from nuvel.dashboard.watcher import RunWatcher
        own_watcher = RunWatcher(sources=loader.sources())
        queue: asyncio.Queue[Run] = asyncio.Queue()
        task = asyncio.create_task(own_watcher.run(queue))
        card_template = templates.get_template("_run_card.html")

        async def event_stream():
            # Prime the connection so headers flush and the browser fires
            # `htmx:sseOpen`.
            yield ": connected\n\n"
            try:
                while True:
                    run = await queue.get()
                    view = _view(run)
                    html = card_template.render(run=view)
                    # SSE framing terminates on \n, \r, or \r\n — strip all of them
                    # so payloads with Windows-origin newlines don't truncate.
                    one_line = " ".join(html.splitlines())
                    yield f"event: run\ndata: {one_line}\n\n"
            finally:
                own_watcher.stop()
                task.cancel()

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    return app
