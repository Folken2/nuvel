"""Postgres implementations of the server's stores.

- :class:`PostgresTraceStore` — :class:`nuvel_mcp.traces.TraceStore`
- :class:`PostgresRecipeStore` — :class:`nuvel_mcp.recipes.RecipeStore`
- :class:`PostgresOAuthStore` — the ``reading()`` / ``transaction()`` contract of
  :class:`nuvel_mcp.oauth.OAuthStore`, so :class:`~nuvel_mcp.oauth.OAuthProvider`
  runs on it unchanged
- :class:`PostgresUsageStore` — :class:`nuvel_mcp.usage.UsageStore`

Callers validate and normalise records before they get here, as with the file
stores; these only map them onto the tables of ``migrations/0001_initial.sql``.
"""

from __future__ import annotations

import contextlib
import copy
import threading
from datetime import datetime, timezone

from nuvel_mcp import traces
from nuvel_mcp.db import Database, redact_dsn
from nuvel_mcp.oauth import OAuthStoreError
from nuvel_mcp.recipes import _PATH_ID_RE

#: OAuth store table (as the provider names it) -> SQL table.
OAUTH_TABLES = {
    "clients": "oauth_clients",
    "codes": "oauth_codes",
    "access_tokens": "oauth_access_tokens",
    "refresh_tokens": "oauth_refresh_tokens",
    "url_keys": "oauth_url_keys",
}


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class PostgresTraceStore:
    """``skill_runs``: one row per run, unique per ``(tenant, run_id)``."""

    def __init__(self, db: Database):
        self.db = db
        self._table = db.table("skill_runs")

    def append(self, trace: dict) -> bool:
        plugin, skill = trace["plugin"], trace["skill"]
        if not (traces.IDENTIFIER_RE.fullmatch(plugin["name"])
                and traces.IDENTIFIER_RE.fullmatch(skill["name"])):
            raise ValueError("plugin and skill names must be identifiers")
        row = (
            trace.get("tenant"), trace["run_id"], trace["install_id"],
            plugin["name"], plugin.get("version"), skill["name"], skill.get("version"),
            trace["host"], trace["mode"], traces._parse_started_at(trace["started_at"]),
            trace["duration_s"], trace["outcome"], trace.get("user_corrected"),
            self.db.jsonb(trace),
        )
        with self.db.connection() as conn:
            inserted = conn.execute(
                f"INSERT INTO {self._table} (tenant, run_id, install_id, plugin_name, "
                "plugin_version, skill_name, skill_version, host, mode, started_at, "
                "duration_s, outcome, user_corrected, record) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT DO NOTHING RETURNING id", row).fetchone()
        return inserted is not None

    def query(self, plugin: str, skill: str, since: datetime, tenant: str | None) -> list[dict]:
        if not (isinstance(plugin, str) and isinstance(skill, str)
                and traces.IDENTIFIER_RE.fullmatch(plugin)
                and traces.IDENTIFIER_RE.fullmatch(skill)):
            return []
        sql = (f"SELECT record FROM {self._table} "
               "WHERE plugin_name = %s AND skill_name = %s AND started_at >= %s")
        params: list = [plugin, skill, _utc(since)]
        if tenant is not None:
            sql += " AND tenant = %s"
            params.append(tenant)
        sql += " ORDER BY started_at, id"
        with self.db.connection() as conn:
            return [row[0] for row in conn.execute(sql, params)]


class PostgresRecipeStore:
    """``recipe_signals`` (unique per ``(tenant, signal_id)``) and ``recipes``
    (unique per ``(tenant, recipe_id, version)``)."""

    def __init__(self, db: Database):
        self.db = db
        self._signals = db.table("recipe_signals")
        self._recipes = db.table("recipes")

    def append_signal(self, signal: dict) -> bool:
        row = (
            signal.get("tenant"), signal["signal_id"], signal["recipe_id"],
            signal["install_id"], signal["plugin"]["name"], signal["category"],
            signal["event"], traces._parse_started_at(signal["at"]), signal["uses"],
            signal["successes"], signal["corrections"], self.db.jsonb(signal),
        )
        with self.db.connection() as conn:
            inserted = conn.execute(
                f"INSERT INTO {self._signals} (tenant, signal_id, recipe_id, install_id, "
                "plugin_name, category, event, at, uses, successes, corrections, record) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT DO NOTHING RETURNING id", row).fetchone()
        return inserted is not None

    def put_recipe(self, recipe: dict) -> bool:
        recipe_id, version = recipe["recipe_id"], recipe["version"]
        if not (isinstance(recipe_id, str) and _PATH_ID_RE.fullmatch(recipe_id)
                and isinstance(version, int) and not isinstance(version, bool)):
            raise ValueError("recipe_id and version are not storable")
        row = (
            recipe.get("tenant"), recipe_id, version, recipe["install_id"],
            recipe["plugin"]["name"], recipe["category"], recipe["language"],
            self.db.jsonb(recipe),
        )
        with self.db.connection() as conn:
            inserted = conn.execute(
                f"INSERT INTO {self._recipes} (tenant, recipe_id, version, install_id, "
                "plugin_name, category, language, record) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT DO NOTHING RETURNING id", row).fetchone()
        return inserted is not None


class PostgresUsageStore:
    """``usage_events``: one batched insert per :meth:`record_batch`."""

    def __init__(self, db: Database):
        self.db = db
        self._table = db.table("usage_events")

    def record_batch(self, events: list[dict]) -> None:
        rows = [(e["at"], e.get("tenant"), e["transport"], e["method"], e.get("tool"),
                 e["outcome"], e.get("error_code"), e["duration_ms"]) for e in events]
        with self.db.connection() as conn:
            with conn.cursor() as cur:
                cur.executemany(
                    f"INSERT INTO {self._table} (at, tenant, transport, method, tool, "
                    "outcome, error_code, duration_ms) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    rows)


class PostgresOAuthStore:
    """The OAuth store in five tables plus the ``oauth_state`` change counter.

    The provider works on a dict of tables, as with the file store:

    - ``reading()`` costs one round trip (the counter); the in-process snapshot
      is reused while it is unchanged, so a write from another process or
      machine (a CLI ``client create``, another replica) is seen on the next
      request. A stale snapshot is reloaded in one REPEATABLE READ transaction,
      so it is consistent across the five tables.
    - ``transaction()`` locks the counter row ``FOR UPDATE`` (writers queue
      across replicas), yields a copy, then writes only the rows that changed,
      bumps the counter and commits. An exception rolls it all back.

    Database failures surface as :class:`OAuthStoreError`, so authentication
    fails closed exactly as with an unreadable file.
    """

    def __init__(self, db: Database):
        self.db = db
        self.path = db.describe()  # shown where the file store shows its path
        self._state = db.table("oauth_state")
        self._lock = threading.RLock()
        self._cache: dict | None = None
        self._version: int | None = None

    def _load(self, conn) -> dict:
        data: dict = {"version": 1}
        for key, table in OAUTH_TABLES.items():
            data[key] = {row[0]: row[1] for row in conn.execute(
                f"SELECT id, record FROM {self.db.table(table)}")}
        return data

    def _counter(self, conn, *, lock: bool = False) -> int:
        sql = f"SELECT version FROM {self._state} WHERE id = 1"
        row = conn.execute(sql + (" FOR UPDATE" if lock else "")).fetchone()
        if row is None:
            raise OAuthStoreError("OAuth store is not initialised (run nuvel-mcp db migrate)")
        return row[0]

    @contextlib.contextmanager
    def _errors(self):
        # Driver errors (pool timeouts included) only: the provider's own
        # errors raised inside a transaction must pass through unchanged.
        try:
            yield
        except self.db.driver_error as exc:
            raise OAuthStoreError(
                redact_dsn(f"OAuth store unavailable ({self.path}): {type(exc).__name__}: {exc}")
            ) from None

    def _snapshot(self) -> dict:
        with self._errors(), self.db.connection() as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            version = self._counter(conn)
            cache = self._cache
            if cache is not None and version == self._version:
                return cache
            data = self._load(conn)
        with self._lock:
            if self._version is None or version >= self._version:
                self._cache, self._version = data, version
        return data

    @contextlib.contextmanager
    def reading(self):
        """Yield the current data (read-only)."""
        yield self._snapshot()

    @contextlib.contextmanager
    def transaction(self):
        """Yield a mutable copy; only changed rows are written, and only if the
        block succeeds."""
        with self._lock, self._errors(), self.db.connection() as conn:
            version = self._counter(conn, lock=True)
            if self._cache is not None and version == self._version:
                base = self._cache
            else:
                base = self._load(conn)
            data = copy.deepcopy(base)
            yield data
            if self._write_changes(conn, base, data):
                version = conn.execute(
                    f"UPDATE {self._state} SET version = version + 1 WHERE id = 1 "
                    "RETURNING version").fetchone()[0]
            # The commit happens when the connection block exits.
            committed = (data, version)
        self._cache, self._version = committed

    def _write_changes(self, conn, base: dict, data: dict) -> bool:
        changed = False
        for key, table in OAUTH_TABLES.items():
            before, after = base.get(key, {}), data.get(key, {})
            gone = [k for k in before if k not in after]
            upserts = [(k, rec.get("client_id") if isinstance(rec, dict) else None,
                        self.db.jsonb(rec))
                       for k, rec in after.items() if before.get(k) != rec]
            if gone:
                conn.execute(f"DELETE FROM {self.db.table(table)} WHERE id = ANY(%s)", (gone,))
            if upserts:
                with conn.cursor() as cur:
                    cur.executemany(
                        f"INSERT INTO {self.db.table(table)} (id, client_id, record) "
                        "VALUES (%s, %s, %s) ON CONFLICT (id) DO UPDATE SET "
                        "client_id = EXCLUDED.client_id, record = EXCLUDED.record, "
                        "updated_at = now()", upserts)
            changed = changed or bool(gone or upserts)
        return changed
