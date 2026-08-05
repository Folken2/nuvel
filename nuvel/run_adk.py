"""
Meta-Agent entrypoint.

Usage:
  Development: DEV_MODE=true python run_adk.py
  Production:  python run_adk.py (requires SESSION_SERVICE_URI)
"""

import os
import secrets
import socket
import sys
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from google.adk.cli.fast_api import get_fast_api_app
from nuvel.plugins import PLUGIN_PATHS
from nuvel.config.logging import setup_logging, generate_request_id, request_id_var

try:
    from dotenv import load_dotenv
    load_dotenv()
except ModuleNotFoundError:
    pass


class APIKeyMiddleware(BaseHTTPMiddleware):
    PUBLIC_PREFIXES = ("/health", "/favicon.ico")

    def __init__(self, app, api_key: str):
        super().__init__(app)
        self.api_key = api_key

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path == "/" or any(path.startswith(p) for p in self.PUBLIC_PREFIXES):
            return await call_next(request)
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth[7:]
        else:
            token = request.headers.get("X-API-Key", "")
        if not secrets.compare_digest(token, self.api_key):
            return JSONResponse(
                status_code=401,
                content={"error": "Unauthorized", "message": "Invalid or missing API key"},
            )
        return await call_next(request)


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        rid = request.headers.get("X-Request-ID", generate_request_id())
        request_id_var.set(rid)
        response = await call_next(request)
        response.headers["X-Request-ID"] = rid
        return response


def add_endpoints(app: FastAPI) -> None:
    @app.get("/health")
    async def health_check():
        return JSONResponse(content={
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "service": "meta-agent",
            "status": "healthy",
        })

    @app.get("/")
    async def root():
        return JSONResponse(content={
            "message": "Meta-Agent API — Creates production-ready ADK agents",
            "endpoints": {
                "/health": "Health check",
                "/run/": "Non-streaming agent execution (POST)",
                "/run_sse/": "Streaming agent execution (POST)",
            },
        })

    print("[ADK] Endpoints added: /health, /")


def main() -> None:
    setup_logging()
    agents_dir = os.getenv("AGENTS_DIR", ".")
    dev_mode = os.getenv("DEV_MODE", "false").lower() in ("true", "1", "yes")
    port = int(os.getenv("PORT", "8000"))

    print(f"[ADK] Meta-Agent starting: PORT={port}, DEV_MODE={dev_mode}")

    memory_uri = os.getenv("NUVEL_ORG_MEMORY_URI")
    if memory_uri:
        from nuvel.memory.adk_registry import register_org_memory_scheme
        register_org_memory_scheme()
        scheme_prefix = memory_uri.split("://", 1)[0]
        print(f"[ADK] OrgMemoryService registered (scheme: {scheme_prefix}://...)")

    if dev_mode:
        print("[ADK] DEVELOPMENT mode (in-memory sessions)")
        app = get_fast_api_app(
            agents_dir=agents_dir,
            memory_service_uri=memory_uri,
            session_service_uri=None,
            use_local_storage=False,
            web=False,
            a2a=False,
            host="",
            port=port,
            url_prefix=None,
            reload_agents=True,
            extra_plugins=PLUGIN_PATHS,
        )
    else:
        session_uri = os.getenv("SESSION_SERVICE_URI")
        if not session_uri:
            raise RuntimeError("SESSION_SERVICE_URI is required in production mode.")
        session_uri = _normalize_to_asyncpg_uri(session_uri)
        connect_args = {"ssl": "require"}
        print("[ADK] PRODUCTION mode")
        app = get_fast_api_app(
            agents_dir=agents_dir,
            memory_service_uri=memory_uri,
            session_service_uri=session_uri,
            session_db_kwargs={"connect_args": connect_args},
            web=False,
            a2a=False,
            host="",
            port=port,
            url_prefix=None,
            reload_agents=True,
            extra_plugins=PLUGIN_PATHS,
        )

    app.router.redirect_slashes = False
    app.add_middleware(RequestIDMiddleware)

    api_key = os.getenv("API_KEY")
    if api_key:
        app.add_middleware(APIKeyMiddleware, api_key=api_key)
        if not os.getenv("DOCS_ENABLED", "").lower() in ("true", "1", "yes"):
            app.openapi_url = None
            app.docs_url = None
            app.redoc_url = None
        print("[ADK] API key authentication enabled")
    else:
        print("[ADK] WARNING: No API_KEY set — endpoints are unauthenticated")

    add_endpoints(app)
    print(f"[ADK] Meta-Agent ready: http://0.0.0.0:{port}")
    uvicorn.run(app, host="", port=port)


def _normalize_to_asyncpg_uri(uri: str) -> str:
    if uri.startswith("postgresql://"):
        uri = uri.replace("postgresql://", "postgresql+asyncpg://", 1)
    parsed = urlsplit(uri)
    qs = parse_qsl(parsed.query, keep_blank_values=True)
    filtered = [(k, v) for (k, v) in qs if k.lower() not in {"sslmode", "channel_binding", "channelbinding"}]
    new_query = urlencode(filtered)
    return urlunsplit(parsed._replace(query=new_query))


if __name__ == "__main__":
    main()
