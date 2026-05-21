"""Register OrgMemoryService as an ADK service-registry scheme.

After `register_org_memory_scheme()` is called, ADK's `get_fast_api_app`
will construct an `OrgMemoryService` natively when given
`memory_service_uri="nuvel-org-memory://default"`. No monkey-patching.
"""

from __future__ import annotations

import asyncio
import logging
import os
from urllib.parse import parse_qs, urlparse

from google.adk.cli.service_registry import get_service_registry
from google.adk.memory.base_memory_service import BaseMemoryService

from nuvel.memory.factory import build_default_service

ORG_MEMORY_SCHEME = "nuvel-org-memory"

log = logging.getLogger(__name__)


def _factory(uri: str, **_: object) -> BaseMemoryService:
    """ADK ServiceFactory adapter — sync wrapper around build_default_service.

    Honors a `?migrate=0` query param to skip migrate() (used by unit tests
    that mustn't hit a real DB).
    """
    dsn = os.getenv("NUVEL_ORG_MEMORY_DSN")
    graph_path = os.getenv("NUVEL_ORG_GRAPH_PATH")
    if not dsn:
        raise RuntimeError(
            "NUVEL_ORG_MEMORY_DSN must be set when memory_service_uri "
            f"uses the {ORG_MEMORY_SCHEME!r} scheme."
        )
    if not graph_path:
        raise RuntimeError(
            "NUVEL_ORG_GRAPH_PATH must be set when memory_service_uri "
            f"uses the {ORG_MEMORY_SCHEME!r} scheme."
        )

    parsed = urlparse(uri)
    params = parse_qs(parsed.query or "")
    migrate = params.get("migrate", ["1"])[0] != "0"

    return asyncio.run(
        build_default_service(dsn=dsn, org_graph_path=graph_path, migrate=migrate)
    )


def register_org_memory_scheme() -> None:
    """Idempotent — safe to call from process startup."""
    registry = get_service_registry()
    registry.register_memory_service(ORG_MEMORY_SCHEME, _factory)
    log.info("Registered ADK memory scheme %r", ORG_MEMORY_SCHEME)
