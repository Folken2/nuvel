"""End-to-end: nuvel.run_adk constructs an ADK FastAPI app that has
OrgMemoryService wired through the service registry — proved by inspecting
the app's memory_service attribute (via AdkWebServer route closures) and by
exercising a memory write/read against the real Neon test branch.

Design note
-----------
`get_fast_api_app` (and therefore the nuvel-org-memory factory) is called
*synchronously* at process startup, before any event loop is running — exactly
as `nuvel run-adk` does it via uvicorn.  The factory uses `asyncio.run()`, so
it must NOT be called from inside an async test function (that would already
have a loop running).  We therefore construct the ADK app in a *sync* function
and only switch to async for the memory write/read operations.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path

import pytest

DSN = os.getenv("NUVEL_MEMORY_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="NUVEL_MEMORY_TEST_DSN not set")

FIXTURE = Path(__file__).parent / "fixtures" / "org_graph.yaml"
AGENTS_DIR = Path(__file__).parent / "fixtures" / "minimal_agent"


def _set_env(monkeypatch):
    monkeypatch.setenv("NUVEL_ORG_MEMORY_DSN", DSN)
    monkeypatch.setenv("NUVEL_ORG_GRAPH_PATH", str(FIXTURE))
    monkeypatch.setenv("NUVEL_ORG_MEMORY_URI", "nuvel-org-memory://default")
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)


def _find_adk_web_server(app):
    """Locate the AdkWebServer instance captured in route-handler closures.

    ADK's get_fast_api_app() builds the FastAPI app inside AdkWebServer and
    captures ``self`` (the AdkWebServer) in the closures of the route handlers
    it defines.  We walk the first matching endpoint and extract it.
    """
    from google.adk.cli.adk_web_server import AdkWebServer

    for route in app.routes:
        endpoint = getattr(route, "endpoint", None)
        if endpoint is None:
            continue
        closure = getattr(endpoint, "__closure__", None)
        if not closure:
            continue
        for cell in closure:
            try:
                value = cell.cell_contents
            except ValueError:
                continue
            if isinstance(value, AdkWebServer):
                return value
    return None


def _find_org_service(app):
    """Return the OrgMemoryService wired into the ADK app, or None."""
    from nuvel.memory.org_memory_service import OrgMemoryService

    server = _find_adk_web_server(app)
    if server is None:
        return None
    svc = getattr(server, "memory_service", None)
    if isinstance(svc, OrgMemoryService):
        return svc
    return None


def test_run_adk_wires_org_memory_service(monkeypatch):
    """When NUVEL_ORG_MEMORY_URI is set, get_fast_api_app uses OrgMemoryService.

    This test is intentionally *synchronous*: ``get_fast_api_app`` calls the
    nuvel-org-memory factory via ``asyncio.run()``, which requires that no
    event loop is running at call time — the same precondition that holds in
    production when uvicorn has not yet started.
    """
    _set_env(monkeypatch)

    # Register the scheme (run_adk does this at startup; we do it directly here
    # since we call get_fast_api_app directly rather than going through main()).
    from nuvel.memory.adk_registry import register_org_memory_scheme
    register_org_memory_scheme()

    from google.adk.cli.fast_api import get_fast_api_app

    # --- sync construction (matches production startup path) ---
    app = get_fast_api_app(
        agents_dir=str(AGENTS_DIR),
        memory_service_uri=os.environ["NUVEL_ORG_MEMORY_URI"],
        web=False,
        a2a=False,
        host="",
        port=0,
        url_prefix=None,
    )

    found = _find_org_service(app)
    assert found is not None, (
        "OrgMemoryService not found in the ADK app. "
        "ADK may have changed how it stores the memory service in route closures — "
        "inspect app.routes closures and update _find_adk_web_server()."
    )

    # --- async write/read through the wired service ---
    marker = f"wiring-e2e-{uuid.uuid4().hex[:6]}"

    async def _exercise(svc):
        await svc.add_memory(
            app_name="test_agent",
            user_id="albert",
            memories=[{"content": marker}],
        )
        return await svc.search_memory(
            app_name="test_agent",
            user_id="albert",
            query=marker,
        )

    resp = asyncio.run(_exercise(found))

    texts: list[str] = []
    for entry in resp.memories:
        parts = entry.content.parts or []
        texts.extend(p.text for p in parts if getattr(p, "text", None))
    assert any(marker in t for t in texts), (
        f"Expected {marker!r} to appear in search results but got: {texts!r}"
    )


def test_no_memory_uri_means_default_adk_memory(monkeypatch):
    """Smoke: omitting the URI still constructs an ADK app with InMemoryMemoryService."""
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

    from google.adk.cli.fast_api import get_fast_api_app
    from google.adk.memory.in_memory_memory_service import InMemoryMemoryService

    app = get_fast_api_app(
        agents_dir=str(AGENTS_DIR),
        memory_service_uri=None,
        web=False,
        a2a=False,
        host="",
        port=0,
        url_prefix=None,
    )
    assert app is not None

    server = _find_adk_web_server(app)
    assert server is not None, "Could not locate AdkWebServer in app routes"
    assert isinstance(server.memory_service, InMemoryMemoryService), (
        f"Expected InMemoryMemoryService when no URI set, got {type(server.memory_service)}"
    )
