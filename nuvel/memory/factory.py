"""Convenience factory for building a fully-wired OrgMemoryService from env."""

from __future__ import annotations

import logging
import os

from nuvel.memory.backends.postgres_store import PostgresStore
from nuvel.memory.embedder import Embedder, GoogleEmbedder, NullEmbedder
from nuvel.memory.org_memory_service import OrgMemoryService
from nuvel.memory.resolver import ConfigScopeResolver

log = logging.getLogger(__name__)


def _pick_embedder() -> Embedder:
    if os.getenv("GOOGLE_API_KEY"):
        return GoogleEmbedder()
    return NullEmbedder()


async def build_default_service(
    *,
    dsn: str | None = None,
    org_graph_path: str | None = None,
    migrate: bool = True,
) -> OrgMemoryService:
    """Build an OrgMemoryService from env (or explicit args).

    Reads NUVEL_ORG_MEMORY_DSN and NUVEL_ORG_GRAPH_PATH if args aren't passed.
    Runs PostgresStore.migrate() unless migrate=False.
    """
    dsn = dsn or os.environ["NUVEL_ORG_MEMORY_DSN"]
    org_graph_path = org_graph_path or os.environ["NUVEL_ORG_GRAPH_PATH"]
    store = PostgresStore(dsn)
    if migrate:
        await store.migrate()
    resolver = ConfigScopeResolver.from_yaml(org_graph_path)
    # PostgresStore is also the GraphWriter — self-wiring knowledge-graph
    # extraction runs fire-and-forget on every write.
    return OrgMemoryService(
        store=store,
        resolver=resolver,
        embedder=_pick_embedder(),
        graph_writer=store,
    )
