"""
Custom entrypoint to start ADK with session service using .env.

Usage:
  Development mode (in-memory):
    1) Set DEV_MODE=true in .env or environment
    2) Run: python run_adk.py

  Production mode (database):
    1) Ensure .env contains SESSION_SERVICE_URI (and optionally AGENTS_DIR)
    2) Run: python run_adk.py
"""

import os
import secrets
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from google.adk.cli.fast_api import get_fast_api_app
from {{agent_package}}.config.logging import setup_logging, generate_request_id, request_id_var
{{gateway_imports}}
# Optional: load .env automatically if python-dotenv is installed.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ModuleNotFoundError:
    pass


# ── API Key Authentication Middleware ────────────────────────────────


class APIKeyMiddleware(BaseHTTPMiddleware):
    """
    Bearer token / API key authentication for agent endpoints.

    Only /health and /favicon.ico are public. Everything else requires auth,
    including /docs, /openapi.json, and /debug-info.
    """

    PUBLIC_PREFIXES = ("/health", "/favicon.ico", "/gateways")

    def __init__(self, app, api_key: str):
        super().__init__(app)
        self.api_key = api_key

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Public endpoints — no auth required
        if path == "/" or any(path.startswith(p) for p in self.PUBLIC_PREFIXES):
            return await call_next(request)

        # Check Authorization header
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth[7:]
        else:
            # Also accept X-API-Key header
            token = request.headers.get("X-API-Key", "")

        if not secrets.compare_digest(token, self.api_key):
            return JSONResponse(
                status_code=401,
                content={"error": "Unauthorized", "message": "Invalid or missing API key"},
            )

        return await call_next(request)


# ── Request ID Middleware ────────────────────────────────────────────


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Attach a unique request_id to each request for log tracing."""

    async def dispatch(self, request: Request, call_next):
        rid = request.headers.get("X-Request-ID", generate_request_id())
        request_id_var.set(rid)
        response = await call_next(request)
        response.headers["X-Request-ID"] = rid
        return response


# ── Health Check ────────────────────────────────────────────────────


def add_endpoints(app: FastAPI) -> None:
    """Add health check and debug endpoints."""

    @app.get("/health")
    async def health_check():
        """Health check endpoint."""
        return JSONResponse(content={
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "service": "{{agent_name}}",
            "status": "healthy",
        })

    @app.get("/debug-info")
    async def debug_info():
        """Debug information for networking troubleshooting."""
        local_ips = []
        try:
            for info in socket.getaddrinfo(socket.gethostname(), None):
                ip = info[4][0]
                if ip not in local_ips:
                    local_ips.append(ip)
        except Exception as e:
            local_ips = [f"Error: {e}"]

        return JSONResponse(
            content={
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "environment": {
                    "PORT": os.environ.get("PORT", "not set"),
                    "HOST": os.environ.get("HOST", "not set"),
                    "DEV_MODE": os.environ.get("DEV_MODE", "not set"),
                    "LOG_FORMAT": os.environ.get("LOG_FORMAT", "text"),
                },
                "network": {
                    "hostname": socket.gethostname(),
                    "local_ips": local_ips,
                },
                "python": {"version": sys.version, "platform": sys.platform},
            }
        )

    @app.get("/")
    async def root():
        return JSONResponse(
            content={
                "message": "{{agent_name}} API",
                "endpoints": {
                    "/health": "Health check",
                    "/debug-info": "Debug information",
                    "/list-apps": "List available agents",
                    "/run/": "Non-streaming agent execution (POST)",
                    "/run_sse/": "Streaming agent execution (POST)",
                },
            }
        )

    print("[ADK] Endpoints added: /health, /debug-info, /")


# ── Plugin Chain ─────────────────────────────────────────────────────
# Plugins are pre-configured instances in {{agent_package}}.plugins.
# AgentHarness (harness.py) is the single place that wires them into
# Runners/Apps and exposes extra_plugins for get_fast_api_app.


# ── Main ─────────────────────────────────────────────────────────────


def main() -> None:
    # Initialize structured logging
    setup_logging()

    agents_dir = os.getenv("AGENTS_DIR", ".")
    dev_mode = os.getenv("DEV_MODE", "false").lower() in ("true", "1", "yes")
    streaming_enabled = os.getenv("STREAMING_ENABLED", "false").lower() in ("true", "1", "yes")
    port = int(os.getenv("PORT", "8000"))

    print(f"[ADK] Starting server: PORT={port}, DEV_MODE={dev_mode}, STREAMING={streaming_enabled}")

    if streaming_enabled:
        # ── Streaming mode: build app manually for WebSocket support ──
        from fastapi.staticfiles import StaticFiles
        from google.adk.agents import LlmAgent
        from {{agent_package}}.harness import AgentHarness
        from {{agent_package}}.streaming import mount_streaming
        from {{agent_package}}.agent import root_agent
        from {{agent_package}}.config.llm import LIVE_MODEL

        app = FastAPI(title="{{agent_name}}")
        app_name = "{{agent_name}}"
        harness = AgentHarness.get(app_name)
        print(f"[ADK] STREAMING + {'DEV' if dev_mode else 'PRODUCTION'} mode "
              f"({'in-memory' if dev_mode else 'database'} sessions)")

        # Override model to Gemini for live streaming
        live_agent = LlmAgent(
            model=LIVE_MODEL,
            name=root_agent.name,
            description=root_agent.description,
            instruction=root_agent.instruction,
            tools=root_agent.tools,
            sub_agents=root_agent.sub_agents,
        )

        runner = harness.build_runner(agent=live_agent)

        # Mount streaming WebSocket
        mount_streaming(app, runner, harness.session_service, app_name)

        # Serve test client
        static_dir = Path(__file__).parent / "static"
        if static_dir.is_dir():
            app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
            print(f"[ADK] Test client: http://0.0.0.0:{port}/static/test_client.html")

    else:
        # ── Standard mode: use get_fast_api_app, wired via AgentHarness ──
        from {{agent_package}}.harness import AgentHarness

        harness = AgentHarness.get("{{agent_name}}")
        print(f"[ADK] {'DEVELOPMENT' if dev_mode else 'PRODUCTION'} mode "
              f"({'in-memory' if dev_mode else 'database'} sessions)")
        app = get_fast_api_app(
            agents_dir=agents_dir,
            session_service_uri=harness.session_service_uri,
            session_db_kwargs={"connect_args": {"ssl": "require"}},
            artifact_service_uri=harness.artifact_service_uri,
            use_local_storage=not dev_mode,
            web=False,
            a2a=False,
            host="",
            port=port,
            url_prefix=None,
            reload_agents=True,
            extra_plugins=harness.extra_plugins,
        )

    app.router.redirect_slashes = False

    # Middleware (order matters: outermost first)
    app.add_middleware(RequestIDMiddleware)

    api_key = os.getenv("API_KEY")
    if api_key:
        app.add_middleware(APIKeyMiddleware, api_key=api_key)
        # Disable Swagger/OpenAPI docs in production unless explicitly enabled
        if not os.getenv("DOCS_ENABLED", "").lower() in ("true", "1", "yes"):
            app.openapi_url = None
            app.docs_url = None
            app.redoc_url = None
            print("[ADK] API docs disabled (set DOCS_ENABLED=true to enable)")
        print("[ADK] API key authentication enabled")
    else:
        print("[ADK] WARNING: No API_KEY set — endpoints are unauthenticated")

    add_endpoints(app)
{{gateway_mounts}}
    # ── Cron scheduling ──────────────────────────────────────────────
    # Always mount the /cron HTTP routes (CRUD over jobs.json). The
    # background tick loop is opt-in via NUVEL_CRON_ENABLED=1.
    from {{agent_package}}.cron.routes import router as cron_router
    from {{agent_package}}.cron import scheduler as cron_scheduler
    app.include_router(cron_router)

    @app.on_event("startup")
    async def _start_cron_scheduler():  # noqa: D401
        if not cron_scheduler.is_enabled():
            return
        # Reuse the gateway runner if a gateway already built one. Otherwise
        # build one via AgentHarness (the singleton) so it shares the same
        # session/artifact services and plugin chain as everything else.
        if not getattr(app.state, "runner", None):
            try:
                from {{agent_package}}.agent import root_agent as _cron_root
                from {{agent_package}}.harness import AgentHarness
                app.state.app_name = getattr(app.state, "app_name", "{{agent_name}}")
                app.state.runner = AgentHarness.get(app.state.app_name).build_runner(agent=_cron_root)
            except Exception:
                import logging as _lg
                _lg.getLogger(__name__).exception(
                    "[cron] failed to build scheduler runner; scheduler disabled"
                )
                return
        invoker = cron_scheduler.make_default_invoker(
            runner=app.state.runner, app_name=app.state.app_name,
        )
        cron_scheduler.start_scheduler(invoker)

    @app.on_event("shutdown")
    async def _stop_cron_scheduler():  # noqa: D401
        await cron_scheduler.stop_scheduler()

    print(f"[ADK] Server ready: http://0.0.0.0:{port}")
    uvicorn.run(app, host="", port=port)


if __name__ == "__main__":
    main()
