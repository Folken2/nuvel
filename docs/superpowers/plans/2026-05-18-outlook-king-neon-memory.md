# Outlook-King Neon Memory — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `outlook-king`'s file-based markdown memory with a multi-tenant Postgres backend on Neon, exposing the ADK `BaseMemoryService` search seam while preserving the agent's existing tool surface.

**Architecture:** New `NeonMemoryService` class subclasses `BaseMemoryService`, owns all SQL, and exposes both ADK-standard (`search_memory`, `add_session_to_memory`) and curated (`save`, `recall`, `update`, `forget_topic`, `stats`) methods. A surrogate-id `users` table is upserted from an `X-User-Email` header on every backend request. Memories live in one table with a `tsvector` FTS column; row-level isolation is enforced by always filtering on `user_id` + `app_name` inside the service.

**Tech Stack:** Python 3.11, FastAPI, Google ADK 1.26, Postgres 16 (Neon), `psycopg[binary,pool]` v3, `testcontainers-python` for tests.

**Spec:** [docs/superpowers/specs/2026-05-18-outlook-king-neon-memory-design.md](../specs/2026-05-18-outlook-king-neon-memory-design.md)

**Working directory for all paths below:** `generated-agents/outlook-king/` (the worktree root). All file paths in this plan are relative to it unless explicitly absolute.

---

## Task 1: Add Postgres dependencies & schema file

**Files:**
- Modify: `requirements.txt`
- Create: `db/001_init_memory.sql`

- [ ] **Step 1: Add the runtime and test dependencies**

Edit `requirements.txt`, append after the existing entries:

```
psycopg[binary,pool]>=3.2.0,<4.0.0
testcontainers[postgresql]>=4.7.0,<5.0.0  # test-only, kept here for simplicity
pytest-asyncio>=0.23.0,<1.0.0             # test-only
```

- [ ] **Step 2: Install locally**

Run: `pip install -r requirements.txt`
Expected: psycopg, testcontainers, pytest-asyncio installed without conflicts.

- [ ] **Step 3: Create the schema file**

Create `db/001_init_memory.sql` with:

```sql
-- outlook-king memory schema — applied once per Neon database.

CREATE SCHEMA IF NOT EXISTS nuvel_memory;

CREATE TABLE IF NOT EXISTS nuvel_memory.users (
    user_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email        TEXT NOT NULL UNIQUE,
    display_name TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS nuvel_memory.memories (
    id          BIGSERIAL PRIMARY KEY,
    user_id     UUID NOT NULL REFERENCES nuvel_memory.users(user_id) ON DELETE CASCADE,
    app_name    TEXT NOT NULL,
    topic       TEXT NOT NULL DEFAULT 'core',
    content     TEXT NOT NULL,
    fts         tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS memories_user_app_topic_idx
    ON nuvel_memory.memories (user_id, app_name, topic);
CREATE INDEX IF NOT EXISTS memories_fts_idx
    ON nuvel_memory.memories USING GIN (fts);
```

- [ ] **Step 4: Commit**

```bash
git add requirements.txt db/001_init_memory.sql
git commit -m "feat(memory): add Postgres deps and initial schema"
```

---

## Task 2: Test infrastructure — Neon test-branch fixture

> **Revised from the original plan.** Docker is not available on the developer's machine, so testcontainers is replaced by a real Neon dev branch. Pre-provisioned: project `outlook-king` (id `crimson-band-31080272`), branch `test` (id `br-damp-tree-abzz8rjb`). Schema already applied. The branch's connection string lives in the gitignored file `generated-agents/outlook-king/.env.test` as `TEST_DATABASE_URL`.

**Files:**
- Create: `tests/conftest.py` (or append if it exists)

- [ ] **Step 1: Check if conftest.py exists**

Run: `ls tests/conftest.py 2>&1`
If it exists, you will APPEND to it. If not, you create it. Read existing content first if appending.

- [ ] **Step 2: Add the fixture**

Append (or create) `tests/conftest.py`:

```python
"""Shared pytest fixtures for outlook-king."""
from __future__ import annotations

import os
from pathlib import Path
from typing import AsyncIterator

import pytest
import pytest_asyncio
from dotenv import load_dotenv
from psycopg_pool import AsyncConnectionPool

# Load TEST_DATABASE_URL from .env.test (gitignored, points at the Neon `test` branch).
_ENV_TEST = Path(__file__).resolve().parents[1] / ".env.test"
if _ENV_TEST.is_file():
    load_dotenv(_ENV_TEST, override=False)


def _require_test_db_url() -> str:
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip(
            "TEST_DATABASE_URL not set. Provision a Neon test branch and put "
            "its connection string in generated-agents/outlook-king/.env.test."
        )
    return url


@pytest_asyncio.fixture
async def memory_pool() -> AsyncIterator[AsyncConnectionPool]:
    """Pool against the Neon test branch. Truncates both tables per-test.

    The schema is already applied to the test branch; we only TRUNCATE to
    keep tests isolated. If the schema is somehow missing, the TRUNCATE
    will raise — re-apply db/001_init_memory.sql.
    """
    url = _require_test_db_url()
    pool = AsyncConnectionPool(url, min_size=1, max_size=2, open=False)
    await pool.open()
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "TRUNCATE nuvel_memory.memories, nuvel_memory.users CASCADE"
            )
    try:
        yield pool
    finally:
        await pool.close()
```

Also ensure `pytest_asyncio` has its mode set so async tests run automatically. Append to `tests/conftest.py`:

```python
# Default asyncio mode for all async tests.
def pytest_collection_modifyitems(config, items):
    pass  # placeholder; mode is set via pyproject/pytest.ini-style config below
```

If `pyproject.toml` or `pytest.ini` does not already declare `asyncio_mode = "auto"`, add this minimal `pytest.ini` at `generated-agents/outlook-king/pytest.ini` (skip if the project already has one — read first):

```ini
[pytest]
asyncio_mode = auto
```

- [ ] **Step 3: Verify the fixture loads against the real Neon branch**

Create `tests/test_memory_fixture_smoke.py`:

```python
import pytest


async def test_pool_opens(memory_pool):
    async with memory_pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT 1")
            row = await cur.fetchone()
            assert row[0] == 1


async def test_schema_is_applied(memory_pool):
    """Sanity: the test branch already has nuvel_memory tables."""
    async with memory_pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'nuvel_memory' ORDER BY table_name"
            )
            rows = await cur.fetchall()
    assert [r[0] for r in rows] == ["memories", "users"]
```

Run: `pytest tests/test_memory_fixture_smoke.py -v`
Expected: both PASS. First request may pay Neon cold-start (~1-2s); subsequent runs are fast.

If you see `TEST_DATABASE_URL not set`, check that `.env.test` exists at `generated-agents/outlook-king/.env.test` and contains the line `TEST_DATABASE_URL=postgresql://...`. The file is gitignored on purpose.

- [ ] **Step 4: Delete the smoke test (it was just verification)**

Run: `rm tests/test_memory_fixture_smoke.py`

- [ ] **Step 5: Commit**

```bash
git add tests/conftest.py
git commit -m "test(memory): add Postgres container fixture"
```

---

## Task 3: NeonMemoryService — `upsert_user` (identity)

**Files:**
- Create: `outlook_king/state/memory_service.py`
- Create: `tests/test_memory_service.py`

- [ ] **Step 1: Write the failing test for first-time upsert**

Create `tests/test_memory_service.py`:

```python
"""Tests for NeonMemoryService."""
from __future__ import annotations

import pytest

from outlook_king.state.memory_service import NeonMemoryService


@pytest.fixture
def service(memory_pool):
    return NeonMemoryService(memory_pool, app_name="outlook-king-test")


@pytest.mark.asyncio
async def test_upsert_user_creates_new_user(service):
    user_id = await service.upsert_user("alice@example.com", "Alice Smith")
    assert isinstance(user_id, str)
    assert len(user_id) == 36  # UUID
```

- [ ] **Step 2: Run the test to confirm failure**

Run: `pytest tests/test_memory_service.py::test_upsert_user_creates_new_user -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'outlook_king.state.memory_service'`.

- [ ] **Step 3: Create the service skeleton + `upsert_user`**

Create `outlook_king/state/memory_service.py`:

```python
"""Multi-tenant Postgres-backed memory service for outlook-king.

Implements the ADK ``BaseMemoryService`` interface for the search seam
and exposes curated CRUD methods (``save``, ``recall``, ``update``,
``forget_topic``, ``stats``) for the explicit memory tools.

All SQL lives in this module. Tools and routes must never issue raw
queries against the memory tables — that is the multi-tenant isolation
boundary. Every memory query filters on ``user_id`` AND ``app_name``.
"""
from __future__ import annotations

import logging
from typing import Optional

from google.adk.memory import BaseMemoryService, SearchMemoryResponse
from google.adk.sessions import Session
from psycopg_pool import AsyncConnectionPool

logger = logging.getLogger(__name__)


class NeonMemoryService(BaseMemoryService):
    """Postgres memory service. Construct once per process."""

    def __init__(self, pool: AsyncConnectionPool, app_name: str) -> None:
        self._pool = pool
        self._app_name = app_name

    async def upsert_user(
        self, email: str, display_name: Optional[str] = None
    ) -> str:
        """Insert the user if new, otherwise bump last_seen_at. Returns user_id."""
        async with self._pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO nuvel_memory.users (email, display_name)
                    VALUES (%s, %s)
                    ON CONFLICT (email) DO UPDATE
                      SET last_seen_at = now(),
                          display_name = COALESCE(EXCLUDED.display_name, nuvel_memory.users.display_name)
                    RETURNING user_id::text
                    """,
                    (email, display_name),
                )
                row = await cur.fetchone()
                return row[0]

    # BaseMemoryService interface stubs — implemented in later tasks.
    async def add_session_to_memory(self, session: Session) -> None:
        return None

    async def search_memory(self, *, app_name: str, user_id: str, query: str) -> SearchMemoryResponse:
        raise NotImplementedError
```

- [ ] **Step 4: Run the test to confirm pass**

Run: `pytest tests/test_memory_service.py::test_upsert_user_creates_new_user -v`
Expected: PASS.

- [ ] **Step 5: Write the idempotency test**

Append to `tests/test_memory_service.py`:

```python
@pytest.mark.asyncio
async def test_upsert_user_is_idempotent(service):
    a = await service.upsert_user("bob@example.com", "Bob")
    b = await service.upsert_user("bob@example.com", "Bob")
    assert a == b
```

- [ ] **Step 6: Run and verify pass (no impl change needed — ON CONFLICT handles it)**

Run: `pytest tests/test_memory_service.py::test_upsert_user_is_idempotent -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add outlook_king/state/memory_service.py tests/test_memory_service.py
git commit -m "feat(memory): NeonMemoryService.upsert_user with idempotency"
```

---

## Task 4: `save` + `recall` for core memory

**Files:**
- Modify: `outlook_king/state/memory_service.py`
- Modify: `tests/test_memory_service.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_memory_service.py`:

```python
@pytest.mark.asyncio
async def test_save_then_recall_core(service):
    user_id = await service.upsert_user("carol@example.com")
    save_result = await service.save(user_id, "carol prefers concise replies")
    assert save_result["status"] == "ok"

    recall = await service.recall(user_id)
    assert recall["status"] == "ok"
    assert "concise replies" in recall["content"]
```

- [ ] **Step 2: Run to confirm failure**

Run: `pytest tests/test_memory_service.py::test_save_then_recall_core -v`
Expected: FAIL — `AttributeError: 'NeonMemoryService' object has no attribute 'save'`.

- [ ] **Step 3: Implement `save` and `recall`**

Append to `outlook_king/state/memory_service.py` (inside the class, after `upsert_user`):

```python
    async def save(
        self, user_id: str, content: str, topic: str = "core"
    ) -> dict:
        """Append a memory row. Topic defaults to 'core' (the legacy AGENT_MEMORY.md)."""
        async with self._pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO nuvel_memory.memories (user_id, app_name, topic, content)
                    VALUES (%s, %s, %s, %s)
                    RETURNING id
                    """,
                    (user_id, self._app_name, topic, content),
                )
                row = await cur.fetchone()
                return {"status": "ok", "id": row[0], "topic": topic}

    async def recall(
        self, user_id: str, topic: Optional[str] = None
    ) -> dict:
        """Return all rows for a topic concatenated. None / '' → 'core'."""
        topic_filter = topic or "core"
        async with self._pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT content
                      FROM nuvel_memory.memories
                     WHERE user_id = %s AND app_name = %s AND topic = %s
                     ORDER BY created_at ASC
                    """,
                    (user_id, self._app_name, topic_filter),
                )
                rows = await cur.fetchall()
        if not rows:
            return {"status": "ok", "topic": topic_filter, "content": ""}
        return {
            "status": "ok",
            "topic": topic_filter,
            "content": "\n\n".join(r[0] for r in rows),
        }
```

- [ ] **Step 4: Run the test to confirm pass**

Run: `pytest tests/test_memory_service.py::test_save_then_recall_core -v`
Expected: PASS.

- [ ] **Step 5: Write the topic-based test**

Append:

```python
@pytest.mark.asyncio
async def test_save_then_recall_topic(service):
    user_id = await service.upsert_user("dave@example.com")
    await service.save(user_id, "dave is a senior PM", topic="user-bio")
    await service.save(user_id, "dave works at Acme", topic="user-bio")

    recall = await service.recall(user_id, topic="user-bio")
    assert "senior PM" in recall["content"]
    assert "Acme" in recall["content"]

    # Core memory is empty
    core = await service.recall(user_id)
    assert core["content"] == ""
```

- [ ] **Step 6: Run and confirm pass**

Run: `pytest tests/test_memory_service.py::test_save_then_recall_topic -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add outlook_king/state/memory_service.py tests/test_memory_service.py
git commit -m "feat(memory): save and recall by topic"
```

---

## Task 5: `update` + `forget_topic` + `stats`

**Files:**
- Modify: `outlook_king/state/memory_service.py`
- Modify: `tests/test_memory_service.py`

- [ ] **Step 1: Write the failing test for update (overwrite-topic semantics)**

Append to `tests/test_memory_service.py`:

```python
@pytest.mark.asyncio
async def test_update_replaces_topic(service):
    user_id = await service.upsert_user("eve@example.com")
    await service.save(user_id, "eve uses dark mode", topic="user-prefs")
    await service.save(user_id, "eve prefers Slack over email", topic="user-prefs")

    # update() replaces all rows in the topic with a single new row.
    await service.update(user_id, "eve uses dark mode and prefers Slack", topic="user-prefs")

    recall = await service.recall(user_id, topic="user-prefs")
    # Old rows are gone, only the consolidated content remains.
    assert "dark mode and prefers Slack" in recall["content"]
    assert recall["content"].count("eve") == 1
```

- [ ] **Step 2: Run to confirm failure**

Run: `pytest tests/test_memory_service.py::test_update_replaces_topic -v`
Expected: FAIL — `AttributeError` for `update`.

- [ ] **Step 3: Implement `update`**

Append inside the class:

```python
    async def update(
        self, user_id: str, content: str, topic: str = "core"
    ) -> dict:
        """Overwrite-semantic: delete all rows for the topic, insert one new row.

        Used by the agent when it wants to summarize/reorganize, not append.
        """
        async with self._pool.connection() as conn:
            async with conn.transaction():
                async with conn.cursor() as cur:
                    await cur.execute(
                        """
                        DELETE FROM nuvel_memory.memories
                         WHERE user_id = %s AND app_name = %s AND topic = %s
                        """,
                        (user_id, self._app_name, topic),
                    )
                    await cur.execute(
                        """
                        INSERT INTO nuvel_memory.memories (user_id, app_name, topic, content)
                        VALUES (%s, %s, %s, %s)
                        RETURNING id
                        """,
                        (user_id, self._app_name, topic, content),
                    )
                    row = await cur.fetchone()
        return {"status": "ok", "id": row[0], "topic": topic}
```

- [ ] **Step 4: Run and confirm pass**

Run: `pytest tests/test_memory_service.py::test_update_replaces_topic -v`
Expected: PASS.

- [ ] **Step 5: Write the failing test for forget_topic**

Append:

```python
@pytest.mark.asyncio
async def test_forget_topic_removes_only_that_topic(service):
    user_id = await service.upsert_user("frank@example.com")
    await service.save(user_id, "core fact", topic="core")
    await service.save(user_id, "topic fact", topic="other")

    result = await service.forget_topic(user_id, "other")
    assert result["status"] == "ok"
    assert result["deleted"] == 1

    # Core still there
    assert "core fact" in (await service.recall(user_id))["content"]
    # Other gone
    assert (await service.recall(user_id, topic="other"))["content"] == ""
```

- [ ] **Step 6: Run to confirm failure**

Run: `pytest tests/test_memory_service.py::test_forget_topic_removes_only_that_topic -v`
Expected: FAIL — `AttributeError` for `forget_topic`.

- [ ] **Step 7: Implement `forget_topic`**

Append inside the class:

```python
    async def forget_topic(self, user_id: str, topic: str) -> dict:
        """Delete every row for (user_id, app_name, topic). Returns rowcount."""
        async with self._pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    DELETE FROM nuvel_memory.memories
                     WHERE user_id = %s AND app_name = %s AND topic = %s
                    """,
                    (user_id, self._app_name, topic),
                )
                deleted = cur.rowcount
        return {"status": "ok", "topic": topic, "deleted": deleted}
```

- [ ] **Step 8: Run and confirm pass**

Run: `pytest tests/test_memory_service.py::test_forget_topic_removes_only_that_topic -v`
Expected: PASS.

- [ ] **Step 9: Write the failing test for stats**

Append:

```python
@pytest.mark.asyncio
async def test_stats_reports_counts(service):
    user_id = await service.upsert_user("gina@example.com")
    await service.save(user_id, "a", topic="core")
    await service.save(user_id, "b", topic="core")
    await service.save(user_id, "c", topic="prefs")

    stats = await service.stats(user_id)
    assert stats["total_rows"] == 3
    assert stats["topics"] == {"core": 2, "prefs": 1}
```

- [ ] **Step 10: Run to confirm failure**

Run: `pytest tests/test_memory_service.py::test_stats_reports_counts -v`
Expected: FAIL — `AttributeError` for `stats`.

- [ ] **Step 11: Implement `stats`**

Append inside the class:

```python
    async def stats(self, user_id: str) -> dict:
        """Return per-topic row counts and total."""
        async with self._pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT topic, COUNT(*)
                      FROM nuvel_memory.memories
                     WHERE user_id = %s AND app_name = %s
                     GROUP BY topic
                    """,
                    (user_id, self._app_name),
                )
                rows = await cur.fetchall()
        topics = {topic: int(count) for topic, count in rows}
        return {
            "status": "ok",
            "total_rows": sum(topics.values()),
            "topics": topics,
        }
```

- [ ] **Step 12: Run and confirm pass**

Run: `pytest tests/test_memory_service.py::test_stats_reports_counts -v`
Expected: PASS.

- [ ] **Step 13: Commit**

```bash
git add outlook_king/state/memory_service.py tests/test_memory_service.py
git commit -m "feat(memory): update, forget_topic, stats"
```

---

## Task 6: `search_memory` — Postgres FTS

**Files:**
- Modify: `outlook_king/state/memory_service.py`
- Modify: `tests/test_memory_service.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_memory_service.py`:

```python
@pytest.mark.asyncio
async def test_search_memory_finds_via_stemming(service):
    user_id = await service.upsert_user("henry@example.com")
    await service.save(user_id, "henry prefers concise emails", topic="user-prefs")
    await service.save(user_id, "henry's car is red", topic="random")
    await service.save(user_id, "weather is nice today", topic="random")

    resp = await service.search_memory(
        app_name="outlook-king-test",  # ignored — service uses its own app_name
        user_id=user_id,
        query="preferences",
    )
    # ADK SearchMemoryResponse has a `memories` list.
    contents = [m.content.parts[0].text for m in resp.memories]
    assert any("concise emails" in c for c in contents)
    # The car/weather rows should not match "preferences" (FTS stemming).
    assert not any("weather" in c for c in contents)
```

- [ ] **Step 2: Run to confirm failure**

Run: `pytest tests/test_memory_service.py::test_search_memory_finds_via_stemming -v`
Expected: FAIL — `NotImplementedError` from the existing stub.

- [ ] **Step 3: Implement `search_memory`**

In `outlook_king/state/memory_service.py`, replace the existing `search_memory` stub with:

```python
    async def search_memory(
        self, *, app_name: str, user_id: str, query: str
    ) -> SearchMemoryResponse:
        """FTS query, ranked by ts_rank, top 10. Always scoped to (user_id, self._app_name).

        Note: the ``app_name`` argument from ADK is ignored — we use the
        service's configured ``self._app_name`` to keep tenant scoping
        single-sourced. ADK passes its own ``app_name`` for compatibility
        with multi-app deployments; this service is per-app.
        """
        # Import inside the method so it's lazy and matches ADK's actual export path.
        from google.adk.memory import MemoryEntry
        from google.genai.types import Content, Part

        async with self._pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT content, topic, created_at,
                           ts_rank(fts, plainto_tsquery('english', %s)) AS rank
                      FROM nuvel_memory.memories
                     WHERE user_id = %s
                       AND app_name = %s
                       AND fts @@ plainto_tsquery('english', %s)
                     ORDER BY rank DESC, created_at DESC
                     LIMIT 10
                    """,
                    (query, user_id, self._app_name, query),
                )
                rows = await cur.fetchall()

        memories = [
            MemoryEntry(
                content=Content(role="user", parts=[Part(text=content)]),
                author=topic,
                timestamp=created_at.isoformat(),
            )
            for (content, topic, created_at, _rank) in rows
        ]
        return SearchMemoryResponse(memories=memories)
```

- [ ] **Step 4: Run and confirm pass**

Run: `pytest tests/test_memory_service.py::test_search_memory_finds_via_stemming -v`
Expected: PASS.

If the ADK import path differs (`MemoryEntry` location), check `python -c "from google.adk.memory import MemoryEntry"` and adjust to whatever ADK 1.26 actually exports. The dataclass name is stable; only the module path may shift.

- [ ] **Step 5: Commit**

```bash
git add outlook_king/state/memory_service.py tests/test_memory_service.py
git commit -m "feat(memory): search_memory via Postgres FTS"
```

---

## Task 7: Multi-user isolation (the leak test)

**Files:**
- Modify: `tests/test_memory_service.py`

- [ ] **Step 1: Write the test**

Append to `tests/test_memory_service.py`:

```python
@pytest.mark.asyncio
async def test_users_cannot_see_each_others_memory(service):
    """The leak test. If this fails, the multi-tenant story is broken."""
    alice = await service.upsert_user("alice-leak@example.com")
    bob = await service.upsert_user("bob-leak@example.com")

    await service.save(alice, "alice secret", topic="core")
    await service.save(bob, "bob secret", topic="core")

    alice_recall = await service.recall(alice)
    bob_recall = await service.recall(bob)
    assert "alice secret" in alice_recall["content"]
    assert "bob secret" not in alice_recall["content"]
    assert "bob secret" in bob_recall["content"]
    assert "alice secret" not in bob_recall["content"]

    alice_search = await service.search_memory(
        app_name="outlook-king-test", user_id=alice, query="secret"
    )
    contents = [m.content.parts[0].text for m in alice_search.memories]
    assert any("alice secret" in c for c in contents)
    assert not any("bob secret" in c for c in contents)


@pytest.mark.asyncio
async def test_delete_user_cascades_memories(service):
    user_id = await service.upsert_user("ivan@example.com")
    await service.save(user_id, "fact 1")
    await service.save(user_id, "fact 2")

    async with service._pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "DELETE FROM nuvel_memory.users WHERE user_id = %s", (user_id,)
            )
            await cur.execute(
                "SELECT COUNT(*) FROM nuvel_memory.memories WHERE user_id = %s",
                (user_id,),
            )
            (count,) = await cur.fetchone()
    assert count == 0
```

- [ ] **Step 2: Run both tests — they should already pass (the impl is correct)**

Run: `pytest tests/test_memory_service.py::test_users_cannot_see_each_others_memory tests/test_memory_service.py::test_delete_user_cascades_memories -v`
Expected: PASS. If FAIL, there is a bug in `save`/`recall`/`search_memory` that omits the `user_id` filter — find and fix before committing.

- [ ] **Step 3: Run the whole test file**

Run: `pytest tests/test_memory_service.py -v`
Expected: all tests pass.

- [ ] **Step 4: Commit**

```bash
git add tests/test_memory_service.py
git commit -m "test(memory): multi-user isolation and CASCADE delete"
```

---

## Task 8: Rewrite `memory_tools.py` to delegate to the service

**Files:**
- Modify: `outlook_king/tools/memory_tools.py`
- Create: `outlook_king/state/memory_singleton.py`

- [ ] **Step 1: Create a module-level service holder**

The tools need a way to reach the live `NeonMemoryService` instance constructed by the FastAPI backend. Create `outlook_king/state/memory_singleton.py`:

```python
"""Module-level holder for the live NeonMemoryService instance.

The backend sets this in its FastAPI lifespan; tools read it via
``get_memory_service()``. Module-level holders are intentionally simple
— ADK does not pass arbitrary services into tools, and the alternatives
(thread locals, context vars) are heavier without payoff here.
"""
from __future__ import annotations

from typing import Optional

from .memory_service import NeonMemoryService

_service: Optional[NeonMemoryService] = None


def set_memory_service(service: NeonMemoryService) -> None:
    global _service
    _service = service


def get_memory_service() -> NeonMemoryService:
    if _service is None:
        raise RuntimeError(
            "NeonMemoryService not initialized. The FastAPI lifespan in "
            "backend/main.py must call set_memory_service() at startup."
        )
    return _service
```

- [ ] **Step 2: Rewrite `outlook_king/tools/memory_tools.py`**

Replace the entire file with:

```python
"""Memory tools for outlook-king.

Same five tools the agent saw before — same names, same parameters —
but now backed by NeonMemoryService over Postgres instead of markdown
files. ``user_id`` comes from session state, populated by the memory
plugin before each invocation.
"""
from __future__ import annotations

from google.adk.tools import FunctionTool, ToolContext

from ..state.memory_singleton import get_memory_service


def _resolve_user_id(tool_context: ToolContext) -> str:
    user_id = tool_context.state.get("user_id")
    if not user_id:
        raise RuntimeError(
            "tool_context.state['user_id'] is missing — memory_plugin "
            "must run before any memory tool"
        )
    return user_id


async def save_memory(content: str, topic: str = "", *, tool_context: ToolContext) -> dict:
    """Save a piece of information to long-term memory.

    Use this to remember important facts, user preferences, project details,
    or anything that should persist across conversations.

    Args:
        content: The information to remember. Be concise and specific.
        topic: Optional topic category (e.g. "user-preferences"). Empty
               string saves to the default "core" topic.

    Returns:
        Status dict confirming the save.
    """
    user_id = _resolve_user_id(tool_context)
    return await get_memory_service().save(user_id, content, topic or "core")


async def recall_memory(topic: str = "", *, tool_context: ToolContext) -> dict:
    """Recall information from long-term memory.

    Args:
        topic: Optional topic to recall. Empty string returns core memory.
               Use memory_status() to see all available topics.

    Returns:
        Dict with the memory content.
    """
    user_id = _resolve_user_id(tool_context)
    return await get_memory_service().recall(user_id, topic or None)


async def update_memory(content: str, topic: str = "", *, tool_context: ToolContext) -> dict:
    """Replace all rows for a topic with a single consolidated entry.

    Use when you need to reorganize, summarize, or rewrite memory rather
    than just append.

    Args:
        content: The new consolidated content.
        topic: Optional topic. Empty string updates core memory.

    Returns:
        Status dict confirming the update.
    """
    user_id = _resolve_user_id(tool_context)
    return await get_memory_service().update(user_id, content, topic or "core")


async def forget_topic(topic: str, *, tool_context: ToolContext) -> dict:
    """Delete every row in a topic. Use to clean up obsolete categories.

    Args:
        topic: The topic to delete.

    Returns:
        Status dict with rowcount.
    """
    user_id = _resolve_user_id(tool_context)
    return await get_memory_service().forget_topic(user_id, topic)


async def memory_status(*, tool_context: ToolContext) -> dict:
    """Get memory usage statistics: total rows and per-topic counts.

    Returns:
        Dict with row counts.
    """
    user_id = _resolve_user_id(tool_context)
    return await get_memory_service().stats(user_id)


# ── Tool exports ───────────────────────────────────────────────────────

memory_tool_list = [
    FunctionTool(save_memory),
    FunctionTool(recall_memory),
    FunctionTool(update_memory),
    FunctionTool(forget_topic),
    FunctionTool(memory_status),
]
```

- [ ] **Step 3: Verify the file imports cleanly**

Run: `python -c "from outlook_king.tools.memory_tools import memory_tool_list; print(len(memory_tool_list))"`
Expected: `5`.

- [ ] **Step 4: Commit**

```bash
git add outlook_king/tools/memory_tools.py outlook_king/state/memory_singleton.py
git commit -m "feat(memory): rewrite tools to delegate to NeonMemoryService"
```

---

## Task 9: Rewrite `memory_plugin.py` to mirror `user_id` into state

**Files:**
- Modify: `outlook_king/plugins/memory_plugin.py`

- [ ] **Step 1: Replace the whole file**

Overwrite `outlook_king/plugins/memory_plugin.py`:

```python
"""Memory plugin for outlook-king.

Mirrors ``session.user_id`` into ``state['user_id']`` before each agent
invocation so memory tools (and any future user-scoped tools) can read
it ergonomically from the ToolContext.

The backend's FastAPI dependency resolves the email header to a
surrogate user_id via NeonMemoryService.upsert_user and passes it to
Runner.run_async; this plugin just makes it visible to tools.
"""
from __future__ import annotations

import logging
from typing import Optional

from google.adk.events import Event, EventActions
from google.adk.plugins.base_plugin import BasePlugin

logger = logging.getLogger(__name__)


class MemoryPlugin(BasePlugin):
    def __init__(self) -> None:
        super().__init__(name="memory")

    async def before_agent_callback(
        self,
        *,
        invocation_context,
        **kwargs,
    ) -> Optional[Event]:
        """Copy the session's user_id into state so tools can read it."""
        session = invocation_context.session
        if not session or not session.user_id:
            logger.warning("No user_id on session; memory tools will fail")
            return None

        # Already mirrored? Skip the redundant state write.
        if session.state.get("user_id") == session.user_id:
            return None

        return Event(
            invocation_id=invocation_context.invocation_id,
            author="memory_plugin",
            actions=EventActions(state_delta={"user_id": session.user_id}),
        )
```

- [ ] **Step 2: Ensure the plugin is wired**

Run: `grep -n "MemoryPlugin" outlook_king/agent.py backend/main.py`

If neither file references `MemoryPlugin`, it is not currently registered. Skip ahead — it will be wired into the Runner in Task 10.

If `agent.py` references it, ensure the import path still resolves (the class name didn't change).

- [ ] **Step 3: Commit**

```bash
git add outlook_king/plugins/memory_plugin.py
git commit -m "feat(memory): plugin mirrors session user_id into state"
```

---

## Task 10: Backend wiring — pool, lifespan, dependency, Runner

**Files:**
- Modify: `backend/main.py`

- [ ] **Step 1: Add imports and globals**

Open `backend/main.py`. Find the existing `import` block near the top (around line 32-46). After the FastAPI/Pydantic imports, add:

```python
from fastapi import Header
from psycopg_pool import AsyncConnectionPool

from outlook_king.state.memory_service import NeonMemoryService
from outlook_king.state.memory_singleton import set_memory_service
from outlook_king.plugins.memory_plugin import MemoryPlugin
```

After the `_known_sessions: set[str] = set()` line (around line 85), add:

```python
_db_pool: AsyncConnectionPool | None = None
_memory_service: NeonMemoryService | None = None
```

- [ ] **Step 2: Replace the lifespan function**

Find the existing `lifespan` (around lines 88-92) and replace with:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    global _db_pool, _memory_service
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError(
            "DATABASE_URL is required. Point it at your Neon connection string."
        )
    _db_pool = AsyncConnectionPool(database_url, min_size=1, max_size=10, open=False)
    await _db_pool.open()
    _memory_service = NeonMemoryService(_db_pool, app_name=APP_NAME)
    set_memory_service(_memory_service)
    logger.info("outlook-king backend starting on PID %d (Neon pool open)", os.getpid())
    try:
        yield
    finally:
        logger.info("outlook-king backend shutdown")
        await _db_pool.close()
```

- [ ] **Step 3: Add the `get_user_id` dependency**

After the `_drain_pending_actions` function (around line 309), add:

```python
async def get_user_id(
    x_user_email: str = Header(..., alias="X-User-Email"),
    x_user_display_name: str | None = Header(None, alias="X-User-Display-Name"),
) -> str:
    """Resolve the X-User-Email header to a stable surrogate user_id.

    Idempotent — upserts on every request (cheap; bumps last_seen_at).
    Missing header → FastAPI returns 422 automatically (Header(...)).
    """
    assert _memory_service is not None  # lifespan invariant
    return await _memory_service.upsert_user(x_user_email, x_user_display_name)
```

- [ ] **Step 4: Replace `DEFAULT_USER_ID` fallbacks with the dependency**

Find every occurrence of `user_id = req.user_id or DEFAULT_USER_ID` in `backend/main.py` and replace.

For each endpoint that currently uses `req.user_id or DEFAULT_USER_ID`, modify its signature to include `user_id: str = Depends(get_user_id)` and remove the fallback. Endpoints to update:

- `push_context` (around line 320)
- `chat` (around line 364)
- `chat_stream` (around line 380)
- `action_result` (around line 440)
- `compose_opened` (around line 525)
- `pre_send_check` (around line 541)
- `report_spam` (around line 561)

Add `from fastapi import Depends` to the existing FastAPI import line:

```python
from fastapi import Depends, FastAPI, Header, HTTPException, Request
```

For each endpoint, the change pattern is:

**Before:**
```python
@app.post("/api/outlook/chat")
async def chat(req: ChatRequest):
    user_id = req.user_id or DEFAULT_USER_ID
    ...
```

**After:**
```python
@app.post("/api/outlook/chat")
async def chat(req: ChatRequest, user_id: str = Depends(get_user_id)):
    ...  # delete the `user_id = req.user_id or DEFAULT_USER_ID` line
```

The `user_id` field on the Pydantic request bodies (`ChatRequest`, `ContextRequest`, etc.) becomes ignored — leave it in the schema for now to avoid client breakage. Add a one-line comment to the model: `# user_id: deprecated, set by X-User-Email header`.

- [ ] **Step 5: Delete `DEFAULT_USER_ID`**

Remove the line `DEFAULT_USER_ID = "outlook-user"` near line 79.

Run: `grep -n "DEFAULT_USER_ID" backend/main.py outlook_king/`
Expected: no results. If any remain, replace them by routing through the dependency.

- [ ] **Step 6: Wire the MemoryPlugin into the Runner**

Find `_run_agent_once` (around line 335) and `chat_stream` event_gen (around line 391). Both construct a `Runner`. Change the `Runner(...)` calls in both to pass the memory service and plugin. The constructor today is:

```python
runner = Runner(
    app_name=APP_NAME,
    agent=root_agent,
    session_service=session_service,
    artifact_service=artifact_service,
)
```

Change to:

```python
runner = Runner(
    app_name=APP_NAME,
    agent=root_agent,
    session_service=session_service,
    artifact_service=artifact_service,
    memory_service=_memory_service,
    plugins=[MemoryPlugin()],
)
```

If `Runner` does not accept a `plugins` argument in ADK 1.26, check the actual signature with `python -c "import inspect; from google.adk.runners import Runner; print(inspect.signature(Runner.__init__))"`. Earlier versions used `LlmAgent(plugins=...)` instead — adjust accordingly. The MemoryPlugin must run before the agent each invocation.

- [ ] **Step 7: Sanity check the backend starts**

Set up a local Postgres (or use the same testcontainer URL) and try:

```bash
export DATABASE_URL="postgresql://postgres:postgres@localhost:5432/postgres"
psql "$DATABASE_URL" -f db/001_init_memory.sql
uvicorn backend.main:app --port 8000
```

Expected: log line "outlook-king backend starting on PID … (Neon pool open)" with no exceptions. Ctrl-C to stop.

If `DATABASE_URL` is unset, expected: `RuntimeError: DATABASE_URL is required. ...`

- [ ] **Step 8: Commit**

```bash
git add backend/main.py
git commit -m "feat(memory): wire NeonMemoryService into FastAPI lifespan and Runner"
```

---

## Task 11: Add `load_memory` built-in tool to the agent

**Files:**
- Modify: `outlook_king/agent.py`
- Modify: `outlook_king/tools/__init__.py`

- [ ] **Step 1: Add the built-in load_memory tool to the tool list**

Open `outlook_king/tools/__init__.py`. Change the import block at the top to also pull in `load_memory`:

```python
from google.adk.tools import load_memory  # built-in, calls memory_service.search_memory
```

In the `get_tools` function, after `tools.extend(memory_tool_list)`, add:

```python
    tools.append(load_memory)
```

If `google.adk.tools` does not export `load_memory` in 1.26, find the real import path via `python -c "import pkgutil, google.adk.tools as t; [print(m.name) for m in pkgutil.iter_modules(t.__path__)]"` and locate the `load_memory` symbol. The function exists in ADK — only the path may differ.

- [ ] **Step 2: Verify the import works**

Run: `python -c "from outlook_king.tools import get_tools; tools = get_tools(); print([getattr(t, 'name', type(t).__name__) for t in tools])"`
Expected: list of tool names including `load_memory` and `save_memory`/`recall_memory`/etc. (Database may be unreachable, but the imports should resolve.)

- [ ] **Step 3: Commit**

```bash
git add outlook_king/tools/__init__.py
git commit -m "feat(memory): expose load_memory built-in tool to the agent"
```

---

## Task 12: Add-in — send `X-User-Email` header on every request

**Files:**
- Modify: `addin/src/config/api.ts`
- Create: `addin/src/config/identity.ts`

- [ ] **Step 1: Create the identity helper**

Create `addin/src/config/identity.ts`:

```typescript
/* global Office */

/**
 * Read the current Outlook user's identity from Office.js.
 *
 * Falls back to localStorage cache when Office.context is unavailable
 * (commands.html context, early init). Returns null when no identity
 * is available — callers should treat that as a fatal error since the
 * backend requires X-User-Email.
 */
export interface UserIdentity {
  email: string;
  displayName: string;
}

const CACHE_KEY = "outlook-king.user_identity";

export function getCurrentUser(): UserIdentity | null {
  try {
    const profile = (Office as any)?.context?.mailbox?.userProfile;
    if (profile?.emailAddress) {
      const identity: UserIdentity = {
        email: profile.emailAddress,
        displayName: profile.displayName || "",
      };
      try {
        localStorage.setItem(CACHE_KEY, JSON.stringify(identity));
      } catch {
        // Storage unavailable — non-fatal.
      }
      return identity;
    }
  } catch {
    // Office.context not ready yet.
  }

  try {
    const raw = localStorage.getItem(CACHE_KEY);
    if (raw) return JSON.parse(raw) as UserIdentity;
  } catch {
    // Ignore.
  }
  return null;
}
```

- [ ] **Step 2: Wire the headers into `apiHeaders`**

Open `addin/src/config/api.ts`. At the top, import the identity helper:

```typescript
import { getCurrentUser } from "./identity";
```

Replace the existing `apiHeaders` function with:

```typescript
/** Standard headers sent with every JSON API request. */
export function apiHeaders(extra: Record<string, string> = {}): Record<string, string> {
  const headers: Record<string, string> = { "Content-Type": "application/json", ...extra };
  if (BACKEND_API_KEY) {
    headers["X-API-Key"] = BACKEND_API_KEY;
  }
  const user = getCurrentUser();
  if (user) {
    headers["X-User-Email"] = user.email;
    if (user.displayName) {
      headers["X-User-Display-Name"] = user.displayName;
    }
  }
  return headers;
}
```

Replace `apiKeyHeader` (used for non-JSON requests) with:

```typescript
/** Headers for non-JSON requests (e.g. file uploads). */
export function apiKeyHeader(): Record<string, string> {
  const headers: Record<string, string> = {};
  if (BACKEND_API_KEY) headers["X-API-Key"] = BACKEND_API_KEY;
  const user = getCurrentUser();
  if (user) {
    headers["X-User-Email"] = user.email;
    if (user.displayName) headers["X-User-Display-Name"] = user.displayName;
  }
  return headers;
}
```

- [ ] **Step 3: Verify all fetch call sites already use `apiHeaders`**

Run: `grep -rn "fetch(" addin/src --include="*.ts" --include="*.tsx" | grep -v "apiHeaders\|waitForBackend"`
Expected: only `fetchWithRetry` definition / `waitForBackend` calls (health-check, no identity needed). If any other raw fetch surfaces request bodies, route them through `apiHeaders`.

- [ ] **Step 4: Build the add-in to confirm TypeScript compiles**

Run: `cd addin && npm run build`
Expected: build succeeds with no TS errors. If the import path for `Office` is wrong in your tsconfig, the existing `outlookContext.ts` shows the working pattern (`/* global Office */`).

- [ ] **Step 5: Commit**

```bash
cd ..  # back to outlook-king root
git add addin/src/config/api.ts addin/src/config/identity.ts
git commit -m "feat(memory): send X-User-Email header from the add-in"
```

---

## Task 13: Drop the markdown backend

**Files:**
- Delete: `outlook_king/state/memory.py`
- Delete: `memory/` (entire directory)
- Modify: `.env.example`

- [ ] **Step 1: Verify nothing imports `state.memory` anymore**

Run: `grep -rn "from .*state.memory[^_]" outlook_king/ tests/ backend/`
Expected: no matches (the new module is `state.memory_service` / `state.memory_singleton`; the old `state.memory` is unreferenced).

If any matches surface, fix them before deleting the file.

- [ ] **Step 2: Delete the old reader**

Run: `git rm outlook_king/state/memory.py`

- [ ] **Step 3: Delete the markdown seed directory**

Run: `git rm -r memory/`

- [ ] **Step 4: Update `.env.example`**

Open `.env.example`. Replace the existing "Long-Term Memory" section (lines 69-78) and the `MEMORY_DIR` reference in "Writable surfaces" (line 89, 95) as follows:

Replace:

```
# ── Long-Term Memory ────────────────────────────────────────────────

# Optional: Enable/disable long-term memory (default: true)
# MEMORY_ENABLED=true

# Optional: Max size for core memory file in characters (default: 10000)
# MEMORY_MAX_CORE_SIZE=10000

# Optional: Max size per topic file in characters (default: 5000)
# MEMORY_MAX_TOPIC_SIZE=5000
```

With:

```
# ── Long-Term Memory (Neon Postgres) ────────────────────────────────

# Required: Postgres connection string for the multi-tenant memory store.
# Use the Neon dashboard "Connection string" for the prod branch.
# Apply db/001_init_memory.sql to this database before first boot.
DATABASE_URL=postgresql://user:password@ep-xxx.us-east-1.aws.neon.tech/neondb?sslmode=require
```

Then remove the line `# MEMORY_DIR=/data/memory` (line 89) and the line `# MEMORY_DIR=./.runtime/memory` (line 95).

- [ ] **Step 5: Sanity check no stale env references**

Run: `grep -rn "MEMORY_ENABLED\|MEMORY_MAX_CORE_SIZE\|MEMORY_MAX_TOPIC_SIZE\|MEMORY_DIR" outlook_king/ backend/ .env.example`
Expected: no matches.

- [ ] **Step 6: Run the full test suite**

Run: `pytest tests/ -v`
Expected: all tests pass. The new `test_memory_service.py` suite runs against the container; pre-existing tests (test_agent.py, test_outlook_actions.py, etc.) must still pass — if any imported the old `state.memory`, fix them.

- [ ] **Step 7: Commit**

```bash
git add .env.example
git commit -m "feat(memory): drop markdown backend; document DATABASE_URL"
```

---

## Task 14: End-to-end smoke

**Files:** none (manual verification).

- [ ] **Step 1: Apply the schema to a local Postgres**

```bash
# Use a local Docker Postgres or any Postgres 16 you have.
psql "$DATABASE_URL" -f db/001_init_memory.sql
```

- [ ] **Step 2: Start the backend**

```bash
uvicorn backend.main:app --port 8000
```

Expected: "Neon pool open" log line; no traceback.

- [ ] **Step 3: Issue a chat request with the identity header**

```bash
curl -s http://localhost:8000/api/outlook/chat \
  -H "Content-Type: application/json" \
  -H "X-User-Email: smoke@example.com" \
  -d '{"session_id":"smoke-1","prompt":"Remember that I prefer concise replies"}' | python -m json.tool
```

Expected: a JSON response with `status: "ok"`. The agent should have called `save_memory` (visible in backend logs).

- [ ] **Step 4: Verify the row landed**

```bash
psql "$DATABASE_URL" -c "
  SELECT u.email, m.topic, m.content
    FROM nuvel_memory.memories m
    JOIN nuvel_memory.users u USING (user_id)
   WHERE u.email = 'smoke@example.com';
"
```

Expected: at least one row with `concise` (or similar) in the content.

- [ ] **Step 5: Verify the missing-header contract**

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/api/outlook/chat \
  -H "Content-Type: application/json" \
  -d '{"session_id":"smoke-2","prompt":"hi"}'
```

Expected: `422`.

- [ ] **Step 6: Verify governance query works**

```bash
psql "$DATABASE_URL" -c "
  SELECT u.email, COUNT(*) AS memories
    FROM nuvel_memory.memories m
    JOIN nuvel_memory.users u USING (user_id)
   GROUP BY u.email
   ORDER BY memories DESC
   LIMIT 10;
"
```

Expected: list of top users by memory count, with plaintext emails available for analytics.

- [ ] **Step 7: No commit — this is verification only**

If everything passes, the feature is shippable. Proceed to writing the PR.
