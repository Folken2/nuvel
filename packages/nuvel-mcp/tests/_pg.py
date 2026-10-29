"""Postgres test support.

Database tests run when ``NUVEL_MCP_TEST_DATABASE_URL`` points at a Postgres the
tests may create schemas in (CI starts one); otherwise they are skipped. Every
database gets its own throwaway schema, dropped afterwards.

``NUVEL_MCP_TEST_OAUTH_STORE=postgres`` also runs the OAuth HTTP suites
(``make_provider``) on :class:`PostgresOAuthStore`: one schema per store path,
so a test that builds two providers on one path shares one store, as it does
with the file.
"""

from __future__ import annotations

import hashlib
import os
import uuid

import pytest

DSN = os.environ.get("NUVEL_MCP_TEST_DATABASE_URL", "").strip()
requires_pg = pytest.mark.skipif(not DSN, reason="NUVEL_MCP_TEST_DATABASE_URL is not set")
OAUTH_ON_POSTGRES = bool(DSN) and os.environ.get("NUVEL_MCP_TEST_OAUTH_STORE") == "postgres"

_open: dict[str, object] = {}


def settings(schema: str | None = None, **overrides):
    from nuvel_mcp.db import DatabaseSettings

    schema = schema or f"t_{uuid.uuid4().hex[:12]}"
    return DatabaseSettings(url=DSN, schema=schema, **overrides)


def open_db(schema: str | None = None, migrate: bool = True, **overrides):
    from nuvel_mcp.db import Database

    db = Database(settings(schema, **overrides))
    if migrate:
        db.migrate()
    _open[db.schema] = db
    return db


def drop(db) -> None:
    # A fresh connection: the test may have closed the pool on purpose.
    import psycopg

    _open.pop(db.schema, None)
    db.close()
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(f'DROP SCHEMA IF EXISTS "{db.schema}" CASCADE')


def oauth_store_for(store_path) -> object:
    """A migrated Postgres OAuth store, the same one for the same *store_path*."""
    from nuvel_mcp.db.stores import PostgresOAuthStore

    schema = "o_" + hashlib.sha256(str(store_path).encode()).hexdigest()[:16]
    db = _open.get(schema) or open_db(schema)
    return PostgresOAuthStore(db)


def drop_all() -> None:
    for db in list(_open.values()):
        drop(db)
