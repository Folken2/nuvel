"""End-to-end: OrgMemoryService + PostgresStore against a real Neon branch."""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

from nuvel.memory import (
    ConfigScopeResolver,
    NullEmbedder,
    Scope,
)
from nuvel.memory.admin import OrgMemoryAdmin
from nuvel.memory.backends.postgres_store import PostgresStore
from nuvel.memory.org_memory_service import OrgMemoryService

DSN = os.getenv("NUVEL_MEMORY_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="NUVEL_MEMORY_TEST_DSN not set")

FIXTURE = Path(__file__).parent / "fixtures" / "org_graph.yaml"


def _entry_text(entry) -> str:
    """Extract plain text from a MemoryEntry (whose .content is google.genai Content)."""
    parts = entry.content.parts or []
    return "\n".join(p.text for p in parts if getattr(p, "text", None))


@pytest.mark.asyncio
async def test_inheritance_and_isolation():
    store = PostgresStore(DSN)  # type: ignore[arg-type]
    await store.migrate()
    resolver = ConfigScopeResolver.from_yaml(FIXTURE)
    svc = OrgMemoryService(store=store, resolver=resolver, embedder=NullEmbedder())

    suffix = uuid.uuid4().hex[:6]
    # Albert writes a user-scoped memory
    await svc.add_memory(
        app_name="agent", user_id="albert",
        memories=[{"content": f"albert-only-{suffix}"}],
    )
    # Albert writes a team-scoped memory (platform)
    await svc.add_memory(
        app_name="agent", user_id="albert",
        memories=[{"content": f"platform-shared-{suffix}"}],
        custom_metadata={"scope": {"level": "team", "id": "platform"}},
    )
    # Bea (same team as Albert) and Carlos (different team) try to read
    bea = await svc.search_memory(app_name="agent", user_id="bea", query=f"platform-shared-{suffix}")
    carlos = await svc.search_memory(app_name="agent", user_id="carlos", query=f"platform-shared-{suffix}")
    bea_contents = [_entry_text(m) for m in bea.memories]
    carlos_contents = [_entry_text(m) for m in carlos.memories]

    assert any(f"platform-shared-{suffix}" in c for c in bea_contents), "team-mate must see team memory"
    assert not any(f"albert-only-{suffix}" in c for c in bea_contents), "team-mate must NOT see user memory"
    assert not any(f"platform-shared-{suffix}" in c for c in carlos_contents), "other team must NOT see"


@pytest.mark.asyncio
async def test_admin_move_promotes_user_to_team():
    store = PostgresStore(DSN)  # type: ignore[arg-type]
    await store.migrate()
    resolver = ConfigScopeResolver.from_yaml(FIXTURE)
    svc = OrgMemoryService(store=store, resolver=resolver, embedder=NullEmbedder())

    suffix = uuid.uuid4().hex[:6]
    await svc.add_memory(
        app_name="agent", user_id="albert",
        memories=[{"content": f"promote-me-{suffix}"}],
    )
    # Find the id
    rows = await store.list_by_scope(org_id="acme", scope=Scope(level="user", id="albert"))
    row = next(r for r in rows if f"promote-me-{suffix}" in r.content)

    admin = OrgMemoryAdmin(
        store=store,
        chain_for_scope=lambda s: {
            "team:platform": ["team:platform", "division:eu", "org:acme"],
        }[s.tag()],
        org_id="acme",
    )
    await admin.move(row.id, Scope(level="team", id="platform"))

    # Bea now sees it
    bea = await svc.search_memory(app_name="agent", user_id="bea", query=f"promote-me-{suffix}")
    bea_contents = [_entry_text(m) for m in bea.memories]
    assert any(f"promote-me-{suffix}" in c for c in bea_contents)
