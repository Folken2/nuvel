"""Shared pytest fixtures for outlook-king."""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import AsyncIterator

import pytest
import pytest_asyncio
from dotenv import load_dotenv
from psycopg_pool import AsyncConnectionPool

# psycopg's async pool is incompatible with Windows' default ProactorEventLoop.
# Force the SelectorEventLoop policy so async DB connections work on Windows.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

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
