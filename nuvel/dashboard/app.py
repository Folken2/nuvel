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
