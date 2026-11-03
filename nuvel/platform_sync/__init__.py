"""Structured run data in Postgres (Neon), for the internal console to read.

``PlatformSyncPlugin`` (``nuvel/plugins/platform_sync_plugin.py``) writes a run
when it completes, ``nuvel evalv2 run`` writes eval results and ``nuvel pricing``
writes prices. All of them land here. Row shapes:
``docs/reference/platform-sync.md``.

Disabled unless ``NUVEL_PLATFORM_SYNC=1``. Telemetry never fails what it
observes: with the database unconfigured or unreachable the JSONL traces stay
the only record, after a single warning.

Every write is an upsert on a natural key inside one transaction, so re-syncing
changes nothing. Behind PgBouncer (Neon's ``-pooler`` endpoint) no session state
survives, so tables are schema-qualified, statements are not cached and the
migration lock is transaction-scoped.

No ADK imports here: the CLI paths must stay light. Warnings name the exception
type only, because a driver's message can quote the connection string.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, Optional, Sequence
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

logger = logging.getLogger(__name__)

ENV_ENABLED = "NUVEL_PLATFORM_SYNC"
ENV_DATABASE_URL = "NUVEL_PLATFORM_DATABASE_URL"
DEFAULT_SCHEMA = "nuvel_platform"
DEGRADED_MESSAGE = "[platform_sync] database write failed (%s) — continuing file-only"

_CONNECT_TIMEOUT = 5.0
_MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_MIGRATION_FILE = re.compile(r"\A(\d{4})_([a-z0-9_]+)\.sql\Z")
_SCHEMA_NAME = re.compile(r"\A[a-z_][a-z0-9_]{0,62}\Z")


# ── Row shapes ───────────────────────────────────────────────────────


@dataclass
class RunRow:
    run_id: str
    kind: str  # "agent" | "eval"
    agent: str
    started_at: datetime
    session_id: Optional[str] = None
    user_id: Optional[str] = None
    app_name: Optional[str] = None
    model: Optional[str] = None
    duration_ms: Optional[int] = None
    llm_calls: int = 0
    tool_calls: int = 0
    error_count: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: Optional[float] = None


@dataclass
class TraceEventRow:
    seq: int
    event: str
    occurred_at: datetime
    agent: Optional[str] = None
    data: dict = field(default_factory=dict)


@dataclass
class EvalResultRow:
    example_id: str
    skill: str
    suite: str
    input: str
    score: Optional[float] = None
    passed: Optional[bool] = None
    cache_hit: bool = False
    cost_usd: float = 0.0
    evaluator_results: list = field(default_factory=list)
    notes: list = field(default_factory=list)


# ── Values Postgres would reject ─────────────────────────────────────


def _clean_text(value: Optional[str]) -> Optional[str]:
    """Drop NUL and lone surrogates: one such character aborts the transaction."""
    if value is None:
        return None
    return value.encode("utf-8", "replace").decode("utf-8").replace("\x00", "")


def _clean_json(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None  # jsonb has no NaN
    if isinstance(value, dict):
        return {_clean_text(str(k)): _clean_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean_json(v) for v in value]
    return _clean_text(value if isinstance(value, str) else str(value))


def _jsonb(value: Any) -> str:
    return json.dumps(_clean_json(value), ensure_ascii=False)


def normalize_dsn(dsn: str) -> str:
    """Make a connection string asyncpg accepts.

    Neon's default strings carry ``channel_binding``, which asyncpg does not
    implement and would forward to the server as a setting (rejected);
    ``sslmode`` is kept and still enforced.
    """
    parsed = urlsplit(dsn.strip())
    query = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
             if k.lower() != "channel_binding"]
    return urlunsplit(parsed._replace(query=urlencode(query)))


def migrations() -> list[tuple[int, str, str]]:
    """The packaged migrations as ``(version, name, sql)``, in order."""
    found = []
    for path in _MIGRATIONS_DIR.iterdir():
        match = _MIGRATION_FILE.fullmatch(path.name)
        if match:
            found.append((int(match.group(1)), match.group(2), path.read_text(encoding="utf-8")))
    return sorted(found)


# ── Store ────────────────────────────────────────────────────────────


class PlatformStore:
    """Idempotent writes to the platform schema, migrating it on first use.

    A connection per write rather than a pool: writes happen once per run, a
    pool binds to one event loop, and Neon suspends idle computes anyway.
    """

    def __init__(self, dsn: str, *, schema: str = DEFAULT_SCHEMA) -> None:
        if not _SCHEMA_NAME.fullmatch(schema):
            raise ValueError("invalid schema name")
        self._dsn = normalize_dsn(dsn)
        self.schema = schema
        self._q = f'"{schema}"'  # validated above, so quoting is enough
        self._migrated = False

    def __repr__(self) -> str:  # never the DSN: it may carry a password
        return f"PlatformStore(schema={self.schema!r})"

    async def _connect(self):
        import asyncpg

        return await asyncpg.connect(
            self._dsn,
            timeout=_CONNECT_TIMEOUT,
            command_timeout=15,
            statement_cache_size=0,
        )

    async def _lock(self, conn) -> None:
        # Concurrent processes queue here and re-check; released by the commit.
        await conn.execute(
            "SELECT pg_advisory_xact_lock(hashtext($1))", f"nuvel_platform:migrate:{self.schema}"
        )

    async def migrate(self) -> list[int]:
        """Apply pending migrations; return the versions applied now."""
        applied: list[int] = []
        conn = await self._connect()
        try:
            async with conn.transaction():
                await self._lock(conn)
                await conn.execute(f"CREATE SCHEMA IF NOT EXISTS {self._q}")
                await conn.execute(
                    f"CREATE TABLE IF NOT EXISTS {self._q}.schema_migrations ("
                    "version integer PRIMARY KEY, name text NOT NULL, "
                    "applied_at timestamptz NOT NULL DEFAULT now())"
                )
            for version, name, sql in migrations():
                async with conn.transaction():
                    await self._lock(conn)
                    done = await conn.fetchval(
                        f"SELECT 1 FROM {self._q}.schema_migrations WHERE version = $1", version
                    )
                    if done:
                        continue
                    await conn.execute(sql.replace("{schema}", self._q))
                    await conn.execute(
                        f"INSERT INTO {self._q}.schema_migrations (version, name) "
                        "VALUES ($1, $2)",
                        version, name,
                    )
                    applied.append(version)
        finally:
            await conn.close()
        self._migrated = True
        return applied

    async def _ready_connection(self):
        if not self._migrated:
            await self.migrate()
        return await self._connect()

    async def write_run(
        self,
        run: RunRow,
        events: Sequence[TraceEventRow] = (),
        eval_results: Sequence[EvalResultRow] = (),
    ) -> None:
        """Write one run and its children in one transaction. Safe to repeat."""
        conn = await self._ready_connection()
        try:
            async with conn.transaction():
                await conn.execute(
                    f"""
                    INSERT INTO {self._q}.runs
                      (run_id, kind, agent, session_id, user_id, app_name, model, started_at,
                       duration_ms, llm_calls, tool_calls, error_count, prompt_tokens,
                       completion_tokens, cost_usd)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)
                    ON CONFLICT (run_id) DO UPDATE SET
                      kind = EXCLUDED.kind, agent = EXCLUDED.agent,
                      session_id = EXCLUDED.session_id, user_id = EXCLUDED.user_id,
                      app_name = EXCLUDED.app_name, model = EXCLUDED.model,
                      started_at = EXCLUDED.started_at, duration_ms = EXCLUDED.duration_ms,
                      llm_calls = EXCLUDED.llm_calls, tool_calls = EXCLUDED.tool_calls,
                      error_count = EXCLUDED.error_count,
                      prompt_tokens = EXCLUDED.prompt_tokens,
                      completion_tokens = EXCLUDED.completion_tokens,
                      cost_usd = EXCLUDED.cost_usd, synced_at = now()
                    """,
                    _clean_text(run.run_id), run.kind, _clean_text(run.agent),
                    _clean_text(run.session_id), _clean_text(run.user_id),
                    _clean_text(run.app_name), _clean_text(run.model), run.started_at,
                    run.duration_ms, run.llm_calls, run.tool_calls, run.error_count,
                    run.prompt_tokens, run.completion_tokens, run.cost_usd,
                )
                if events:
                    await conn.executemany(
                        f"""
                        INSERT INTO {self._q}.trace_events
                          (run_id, seq, event, agent, occurred_at, data)
                        VALUES ($1,$2,$3,$4,$5,$6::jsonb)
                        ON CONFLICT (run_id, seq) DO NOTHING
                        """,
                        [(_clean_text(run.run_id), e.seq, e.event, _clean_text(e.agent),
                          e.occurred_at, _jsonb(e.data)) for e in events],
                    )
                if eval_results:
                    await conn.executemany(
                        f"""
                        INSERT INTO {self._q}.eval_results
                          (run_id, example_id, skill, suite, input, score, passed, cache_hit,
                           cost_usd, evaluator_results, notes)
                        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::jsonb,$11::jsonb)
                        ON CONFLICT (run_id, example_id) DO UPDATE SET
                          skill = EXCLUDED.skill, suite = EXCLUDED.suite,
                          input = EXCLUDED.input, score = EXCLUDED.score,
                          passed = EXCLUDED.passed, cache_hit = EXCLUDED.cache_hit,
                          cost_usd = EXCLUDED.cost_usd,
                          evaluator_results = EXCLUDED.evaluator_results,
                          notes = EXCLUDED.notes
                        """,
                        [(_clean_text(run.run_id), _clean_text(r.example_id),
                          _clean_text(r.skill), _clean_text(r.suite), _clean_text(r.input),
                          r.score, r.passed, r.cache_hit, r.cost_usd,
                          _jsonb(r.evaluator_results), _jsonb(r.notes)) for r in eval_results],
                    )
        finally:
            await conn.close()

    async def write_prices(self, entries: Mapping[str, Mapping[str, float]]) -> None:
        """Upsert pricing.json entries. ``updated_at`` moves only on a real change."""
        rows = [(_clean_text(model), float(entry["input"]), float(entry["output"]))
                for model, entry in entries.items()
                if not model.startswith("_") and "input" in entry and "output" in entry]
        conn = await self._ready_connection()
        try:
            async with conn.transaction():
                await conn.executemany(
                    f"""
                    INSERT INTO {self._q}.model_prices (model, input_per_token, output_per_token)
                    VALUES ($1,$2,$3)
                    ON CONFLICT (model) DO UPDATE SET
                      input_per_token = EXCLUDED.input_per_token,
                      output_per_token = EXCLUDED.output_per_token,
                      updated_at = now()
                    WHERE ({self._q}.model_prices.input_per_token,
                           {self._q}.model_prices.output_per_token)
                      IS DISTINCT FROM (EXCLUDED.input_per_token, EXCLUDED.output_per_token)
                    """,
                    rows,
                )
        finally:
            await conn.close()


# ── Configuration ────────────────────────────────────────────────────


def store_from_env(environ: Optional[Mapping[str, str]] = None) -> Optional[PlatformStore]:
    """The configured store, or ``None`` when disabled or without a database.

    A missing or malformed URL is the one misconfiguration worth a warning, so
    each caller should resolve this once.
    """
    environ = _environ() if environ is None else environ
    if environ.get(ENV_ENABLED, "").strip().lower() not in ("1", "true", "yes", "on"):
        return None
    url = environ.get(ENV_DATABASE_URL, "").strip()
    if not url:
        logger.warning(
            "[platform_sync] %s is on but %s is unset — continuing file-only",
            ENV_ENABLED, ENV_DATABASE_URL,
        )
        return None
    try:
        return PlatformStore(url)
    except ValueError:  # names no part of the URL: it may carry a password
        logger.warning(
            "[platform_sync] %s is not a valid connection string — continuing file-only",
            ENV_DATABASE_URL,
        )
        return None


def _environ() -> Mapping[str, str]:
    """``os.environ`` over the cwd's ``.env``, so every entry point reads the same config."""
    try:
        from dotenv import dotenv_values, find_dotenv
    except ImportError:
        return os.environ
    path = find_dotenv(usecwd=True)
    if not path:
        return os.environ
    from_file = {k: v for k, v in dotenv_values(path).items() if v is not None}
    return {**from_file, **os.environ}


def _sync_now(write: Callable[[PlatformStore], Awaitable[None]]) -> bool:
    """Run one write from a CLI command; ``True`` only if it reached the database."""
    try:
        store = store_from_env()
        if store is None:
            return False
        asyncio.run(write(store))
        return True
    except Exception as exc:  # noqa: BLE001 - telemetry never fails the command
        logger.warning(DEGRADED_MESSAGE, type(exc).__name__)
        return False


def eval_rows(result: Any) -> tuple[RunRow, list[EvalResultRow]]:
    """An ``EvalSuiteResult`` as its ``runs`` row and ``eval_results`` rows.

    The run id derives from what identifies the suite run, so syncing the same
    result again lands on the same rows.
    """
    identity = "|".join((result.skill, result.suite, result.timestamp, result.model or ""))
    run = RunRow(
        run_id="eval-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32],
        kind="eval",
        agent=result.skill,
        started_at=datetime.fromisoformat(result.timestamp),
        model=result.model,
        cost_usd=sum(e.cost for e in result.examples),
    )
    rows = [
        EvalResultRow(
            example_id=e.id,
            skill=result.skill,
            suite=result.suite,
            input=e.input,
            score=e.score,
            passed=e.passed,
            cache_hit=e.cache_hit,
            cost_usd=e.cost,
            evaluator_results=[r.to_dict() for r in e.evaluator_results],
            notes=list(e.notes),
        )
        for e in result.examples
    ]
    return run, rows


def sync_eval_result(result: Any) -> bool:
    """Write a finished evalv2 suite run. No-op unless enabled; never raises."""
    async def write(store: PlatformStore) -> None:
        run, rows = eval_rows(result)
        await store.write_run(run, eval_results=rows)

    return _sync_now(write)


def sync_prices(entries: Mapping[str, Mapping[str, float]]) -> bool:
    """Write pricing.json entries. No-op unless enabled; never raises."""
    return _sync_now(lambda store: store.write_prices(entries))
