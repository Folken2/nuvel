"""Postgres storage for the Nuvel Skills MCP server.

Setting ``NUVEL_MCP_DATABASE_URL`` moves every store to Postgres: the OAuth
store (clients, codes, tokens, URL keys), run traces, recipe signals and
recipes, plus a usage record per request. Without it nothing here is imported
by the server, and ``nuvel-mcp`` keeps no runtime dependencies: the driver
(``psycopg`` 3 and ``psycopg_pool``) is imported only when a database is
configured.

Behind PgBouncer in transaction mode (Neon's ``-pooler`` endpoint) no session
state can be relied on, so every table is schema-qualified (no
``search_path``), statements are never server-side prepared, and the
migration lock is transaction-scoped.

Secrets: a connection string may carry a password. :func:`redact_dsn` strips
it from any text before it is logged or raised; settings and errors never
print the URL itself.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from importlib import resources

DEFAULT_SCHEMA = "nuvel_mcp"
DEFAULT_POOL_MAX = 10
INSTALL_HINT = 'pip install "psycopg[binary,pool]"'

SCHEMA_NAME_RE = re.compile(r"\A[a-z_][a-z0-9_]{0,62}\Z")
_MIGRATION_FILE = re.compile(r"\A(\d{4})_([a-z0-9_]+)\.sql\Z")
# scheme://user:password@host -> scheme://user:***@host
_URL_PASSWORD = re.compile(r"(\b[a-z][a-z0-9+.-]*://[^:/?#@\s]*:)[^@\s]*(@)", re.IGNORECASE)
# password=secret (keyword DSNs and query strings)
_KW_PASSWORD = re.compile(r"(\bpassword\s*=\s*)('[^']*'|[^\s&]+)", re.IGNORECASE)

#: Every table the schema owns, in the order ``db status`` reports them.
TABLES = (
    "oauth_clients", "oauth_codes", "oauth_access_tokens", "oauth_refresh_tokens",
    "oauth_url_keys", "skill_runs", "recipe_signals", "recipes", "usage_events",
)


class DatabaseError(Exception):
    """A configuration or connection problem. The message is always redacted."""

    def __init__(self, message: str):
        super().__init__(redact_dsn(message))


def redact_dsn(text: str) -> str:
    """Remove passwords from connection strings anywhere in *text*."""
    text = _URL_PASSWORD.sub(r"\1***\2", text)
    return _KW_PASSWORD.sub(r"\1***", text)


def _env_flag(environ, name: str, default: bool) -> bool:
    raw = environ.get(name, "").strip().lower()
    if not raw:
        return default
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    raise ValueError(f"{name} must be 0 or 1")


@dataclass(frozen=True)
class DatabaseSettings:
    url: str
    schema: str = DEFAULT_SCHEMA
    pool_max: int = DEFAULT_POOL_MAX
    auto_migrate: bool = True
    usage: bool = True

    def __repr__(self) -> str:  # never the URL: it may carry a password
        return (f"DatabaseSettings(schema={self.schema!r}, pool_max={self.pool_max}, "
                f"auto_migrate={self.auto_migrate}, usage={self.usage})")

    @classmethod
    def from_env(cls, environ=None) -> "DatabaseSettings | None":
        """The settings, or ``None`` when ``NUVEL_MCP_DATABASE_URL`` is unset.

        Raises ``ValueError`` (never quoting the URL) for an invalid value.
        """
        environ = os.environ if environ is None else environ
        url = environ.get("NUVEL_MCP_DATABASE_URL", "").strip()
        if not url:
            return None
        schema = environ.get("NUVEL_MCP_DATABASE_SCHEMA", "").strip() or DEFAULT_SCHEMA
        if not SCHEMA_NAME_RE.fullmatch(schema):
            raise ValueError("NUVEL_MCP_DATABASE_SCHEMA must match [a-z_][a-z0-9_]{0,62}")
        raw_pool = environ.get("NUVEL_MCP_DATABASE_POOL_MAX", "").strip()
        try:
            pool_max = int(raw_pool) if raw_pool else DEFAULT_POOL_MAX
        except ValueError:
            raise ValueError("NUVEL_MCP_DATABASE_POOL_MAX must be an integer") from None
        if pool_max < 1:
            raise ValueError("NUVEL_MCP_DATABASE_POOL_MAX must be at least 1")
        return cls(
            url=url,
            schema=schema,
            pool_max=pool_max,
            auto_migrate=_env_flag(environ, "NUVEL_MCP_DATABASE_AUTO_MIGRATE", True),
            usage=_env_flag(environ, "NUVEL_MCP_USAGE", True),
        )


def _driver():
    try:
        import psycopg
        from psycopg_pool import ConnectionPool
    except ImportError:
        raise DatabaseError(
            "NUVEL_MCP_DATABASE_URL is set but the Postgres driver is not installed: "
            + INSTALL_HINT) from None
    return psycopg, ConnectionPool


def migrations() -> list[tuple[int, str, str]]:
    """The packaged migrations as ``(version, name, sql)``, in order."""
    found = []
    for entry in resources.files(__package__).joinpath("migrations").iterdir():
        match = _MIGRATION_FILE.fullmatch(entry.name)
        if match:
            found.append((int(match.group(1)), match.group(2), entry.read_text(encoding="utf-8")))
    found.sort()
    return found


class Database:
    """A connection pool plus the schema it owns.

    ``connection()`` yields a pooled connection inside a transaction: it
    commits when the block succeeds and rolls back when it raises.
    """

    def __init__(self, settings: DatabaseSettings, *, connect_timeout: float = 10.0):
        if not SCHEMA_NAME_RE.fullmatch(settings.schema):
            raise DatabaseError("invalid schema name")
        self._psycopg, pool_class = _driver()
        #: Base class of every driver error, pool timeouts included.
        self.driver_error = self._psycopg.Error
        self.settings = settings
        self.schema = settings.schema
        self._q = f'"{settings.schema}"'  # validated above, so quoting is enough
        self.pool = pool_class(
            settings.url,
            min_size=1,
            max_size=settings.pool_max,
            kwargs={"prepare_threshold": None, "connect_timeout": int(connect_timeout)},
            timeout=connect_timeout,
            check=pool_class.check_connection,
            open=False,
            name="nuvel-mcp",
        )
        try:
            self.pool.open(wait=True, timeout=connect_timeout + 5)
        except Exception as exc:  # noqa: BLE001 - surface any driver error, redacted
            self.pool.close()
            raise DatabaseError(f"cannot connect to the database: {exc}") from None

    def describe(self) -> str:
        return f"postgres (schema {self.schema})"

    def table(self, name: str) -> str:
        return f"{self._q}.{name}"

    def jsonb(self, value):
        from psycopg.types.json import Jsonb

        return Jsonb(value)

    def connection(self, timeout: float | None = None):
        return self.pool.connection(timeout=timeout)

    def close(self) -> None:
        self.pool.close()

    def ping(self, timeout: float = 2.0) -> bool:
        """``select 1`` within *timeout* seconds; ``False`` on any failure."""
        try:
            with self.connection(timeout=timeout) as conn:
                conn.execute(f"SET LOCAL statement_timeout = {int(timeout * 1000)}")
                conn.execute("SELECT 1").fetchone()
            return True
        except Exception:  # noqa: BLE001 - health check: any failure is "down"
            return False

    def _lock(self, conn) -> None:
        # Transaction-scoped, so it works behind PgBouncer and is released by
        # the commit; concurrent replicas queue here and re-check.
        conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))",
                     (f"nuvel_mcp:migrate:{self.schema}",))

    def migrate(self) -> list[tuple[int, str]]:
        """Apply pending migrations; return the ``(version, name)`` applied now."""
        with self.connection() as conn:
            self._lock(conn)
            conn.execute(f"CREATE SCHEMA IF NOT EXISTS {self._q}")
            conn.execute(
                f"CREATE TABLE IF NOT EXISTS {self.table('schema_migrations')} ("
                "version integer PRIMARY KEY, name text NOT NULL, "
                "applied_at timestamptz NOT NULL DEFAULT now())")
        applied = []
        for version, name, sql in migrations():
            with self.connection() as conn:
                self._lock(conn)
                done = conn.execute(
                    f"SELECT 1 FROM {self.table('schema_migrations')} WHERE version = %s",
                    (version,)).fetchone()
                if done:
                    continue
                conn.execute(sql.replace("{schema}", self._q))
                conn.execute(
                    f"INSERT INTO {self.table('schema_migrations')} (version, name) "
                    "VALUES (%s, %s)", (version, name))
                applied.append((version, name))
        return applied

    def status(self) -> dict:
        """Applied migrations, pending ones, and row counts per table."""
        with self.connection() as conn:
            exists = conn.execute(
                "SELECT to_regclass(%s) IS NOT NULL",
                (f"{self._q}.schema_migrations",)).fetchone()[0]
            if not exists:
                return {"schema": self.schema, "applied": [],
                        "pending": [v for v, _, _ in migrations()], "rows": {}}
            applied = [row[0] for row in conn.execute(
                f"SELECT version FROM {self.table('schema_migrations')} ORDER BY version")]
            rows = {}
            for table in TABLES:
                if conn.execute("SELECT to_regclass(%s) IS NOT NULL",
                                (f"{self._q}.{table}",)).fetchone()[0]:
                    rows[table] = conn.execute(
                        f"SELECT count(*) FROM {self.table(table)}").fetchone()[0]
        pending = [v for v, _, _ in migrations() if v not in applied]
        return {"schema": self.schema, "applied": applied, "pending": pending, "rows": rows}
