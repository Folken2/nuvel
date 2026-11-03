"""platform_sync: completed runs become Postgres rows, and telemetry never hurts a run.

Plugin behaviour is tested through a real ADK Runner. Database tests need
Postgres: ``NUVEL_PLATFORM_TEST_DSN`` if set, otherwise a throwaway cluster
booted from the local ``initdb``; skipped when neither exists.
"""

from __future__ import annotations

import argparse
import asyncio
import glob
import json
import logging
import os
import shutil
import socket
import subprocess
import tomllib
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncGenerator

import pytest
from google.adk.agents.llm_agent import LlmAgent
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.plugins.base_plugin import BasePlugin
from google.adk.runners import InMemoryRunner
from google.genai import types

import nuvel.platform_sync as platform_sync
import nuvel.pricing as pricing
from nuvel.evalv2.schema import EvalSuiteResult, EvaluatorResult, ScoredExample
from nuvel.platform_sync import (
    EvalResultRow,
    PlatformStore,
    RunRow,
    TraceEventRow,
    eval_rows,
    migrations,
    normalize_dsn,
    store_from_env,
    sync_eval_result,
    sync_prices,
)
from nuvel.plugins.platform_sync_plugin import PlatformSyncPlugin
from nuvel.plugins.trace_plugin import TracePlugin, _FileWriter

ROOT = Path(__file__).resolve().parent.parent
PRICED_MODEL = "moonshotai/kimi-k2.5"


# ── An ADK run with a tool call, no network ──────────────────────────


class _ScriptedLlm(BaseLlm):
    """Calls the tool once, then answers."""

    model: str = "scripted"

    async def generate_content_async(self, llm_request, stream=False) -> AsyncGenerator:
        answered = any(
            p.function_response
            for c in llm_request.contents or []
            for p in c.parts or []
        )
        part = (
            types.Part(text="done")
            if answered
            else types.Part(function_call=types.FunctionCall(name="lookup", args={"q": "x"}))
        )
        yield LlmResponse(
            content=types.Content(role="model", parts=[part]),
            model_version=PRICED_MODEL,
            usage_metadata=types.GenerateContentResponseUsageMetadata(
                prompt_token_count=100, candidates_token_count=20, total_token_count=120,
            ),
        )


def lookup(q: str) -> dict:
    """Look something up."""
    return {"status": "ok", "q": q}


async def _run_agent(plugins: list[BasePlugin], text: str = "hello") -> str:
    agent = LlmAgent(name="root", model=_ScriptedLlm(), tools=[lookup])
    runner = InMemoryRunner(agent=agent, app_name="app", plugins=plugins)
    session = await runner.session_service.create_session(app_name="app", user_id="u1")
    reply = ""
    async for event in runner.run_async(
        user_id="u1",
        session_id=session.id,
        new_message=types.Content(role="user", parts=[types.Part(text=text)]),
    ):
        if event.content and event.content.parts:
            reply = event.content.parts[0].text or reply
    return reply


class _RecordingStore:
    def __init__(self) -> None:
        self.writes: list[tuple[RunRow, list[TraceEventRow]]] = []

    async def write_run(self, run, events=(), eval_results=()) -> None:
        self.writes.append((run, list(events)))


class _FailingStore:
    def __init__(self) -> None:
        self.fail = True
        self.attempts = 0

    async def write_run(self, run, events=(), eval_results=()) -> None:
        self.attempts += 1
        if self.fail:
            raise OSError("postgresql://user:hunter2@db/x is down")


def _warnings(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records
            if r.levelno >= logging.WARNING and "platform_sync" in r.getMessage()]


def _closed_port_dsn() -> str:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    return f"postgresql://user:hunter2@127.0.0.1:{port}/db"


# ── Plugin behaviour ─────────────────────────────────────────────────


async def test_happy_path_captures_a_complete_run():
    store = _RecordingStore()
    plugin = PlatformSyncPlugin(store=store)

    assert await _run_agent([plugin]) == "done"
    await plugin.close()

    assert len(store.writes) == 1
    run, events = store.writes[0]
    assert run.kind == "agent"
    assert run.agent == "root"
    assert run.user_id == "u1"
    assert run.app_name == "app"
    assert run.model == PRICED_MODEL
    assert (run.llm_calls, run.tool_calls, run.error_count) == (2, 1, 0)
    assert (run.prompt_tokens, run.completion_tokens) == (200, 40)
    assert run.cost_usd and run.cost_usd > 0
    assert run.duration_ms is not None
    assert [e.event for e in events] == [
        "run_start",
        "llm_request", "llm_response",
        "tool_start", "tool_end",
        "llm_request", "llm_response",
        "run_end",
    ]
    assert [e.seq for e in events] == list(range(8))
    assert events[0].data["user_input"] == "hello"
    assert events[3].data == {"tool": "lookup", "args": {"q": "x"}}


async def test_concurrent_runs_do_not_mix():
    store = _RecordingStore()
    plugin = PlatformSyncPlugin(store=store)

    await asyncio.gather(*(_run_agent([plugin], f"msg-{i}") for i in range(5)))
    await plugin.close()

    assert len({run.run_id for run, _ in store.writes}) == 5
    assert {events[0].data["user_input"] for _, events in store.writes} == {
        f"msg-{i}" for i in range(5)
    }
    assert all(run.llm_calls == 2 for run, _ in store.writes)


async def test_disabled_by_default():
    assert store_from_env({}) is None
    plugin = PlatformSyncPlugin()

    assert await _run_agent([plugin]) == "done"

    assert plugin._runs == {} and not plugin._pending


async def test_plugin_is_in_the_chain_and_inert_until_enabled():
    from nuvel import plugins

    assert "nuvel.plugins.platform_sync" in plugins.PLUGIN_PATHS
    assert isinstance(plugins.platform_sync, PlatformSyncPlugin)
    assert plugins.platform_sync._store is None


@pytest.mark.parametrize("value", ["1", "true", "YES", "on"])
def test_enabled_by_config(value):
    store = store_from_env({
        platform_sync.ENV_ENABLED: value,
        platform_sync.ENV_DATABASE_URL: "postgresql://h/db",
    })
    assert isinstance(store, PlatformStore)


async def test_enabled_without_url_warns_once_and_runs_complete(monkeypatch, caplog):
    monkeypatch.setenv(platform_sync.ENV_ENABLED, "1")
    plugin = PlatformSyncPlugin()

    for _ in range(3):
        assert await _run_agent([plugin]) == "done"
    await plugin.close()

    warnings = _warnings(caplog)
    assert len(warnings) == 1
    assert platform_sync.ENV_DATABASE_URL in warnings[0]


async def test_malformed_url_warns_once_and_runs_complete(monkeypatch, caplog):
    monkeypatch.setenv(platform_sync.ENV_ENABLED, "1")
    monkeypatch.setenv(platform_sync.ENV_DATABASE_URL, "postgresql://u:hunter2[x@h/db")
    plugin = PlatformSyncPlugin()

    for _ in range(3):
        assert await _run_agent([plugin]) == "done"
    await plugin.close()

    warnings = _warnings(caplog)
    assert len(warnings) == 1
    assert platform_sync.ENV_DATABASE_URL in warnings[0]
    assert "hunter2" not in caplog.text


async def test_unreachable_database_degrades_to_file_only(tmp_path, caplog):
    trace = TracePlugin()
    trace._trace_dir = tmp_path
    trace._file_writer = _FileWriter(tmp_path)
    plugin = PlatformSyncPlugin(store=PlatformStore(_closed_port_dsn()))

    for _ in range(3):
        assert await _run_agent([trace, plugin]) == "done"
    await plugin.close()
    await trace._file_writer.flush()

    warnings = _warnings(caplog)
    assert len(warnings) == 1, warnings
    assert "hunter2" not in caplog.text
    assert list(tmp_path.glob("*.jsonl")), "JSONL traces must still be written"
    assert len(list((tmp_path / "conversations").glob("*.json"))) == 3


async def test_warning_rearms_after_recovery_and_redacts(caplog):
    store = _FailingStore()
    plugin = PlatformSyncPlugin(store=store)

    await _run_agent([plugin])
    await _run_agent([plugin])
    await plugin.close()
    store.fail = False
    await _run_agent([plugin])
    await plugin.close()
    store.fail = True
    await _run_agent([plugin])
    await plugin.close()

    assert store.attempts == 4, "later runs must keep trying"
    assert len(_warnings(caplog)) == 2
    assert "hunter2" not in caplog.text


async def test_slow_database_does_not_delay_the_run():
    release = asyncio.Event()

    class Slow(_RecordingStore):
        async def write_run(self, run, events=(), eval_results=()):
            await release.wait()
            await super().write_run(run, events)

    store = Slow()
    plugin = PlatformSyncPlugin(store=store)

    assert await asyncio.wait_for(_run_agent([plugin]), timeout=10) == "done"
    assert store.writes == []

    release.set()
    await plugin.close()
    assert len(store.writes) == 1


async def test_a_bug_in_a_callback_does_not_fail_the_run(monkeypatch):
    store = _RecordingStore()
    plugin = PlatformSyncPlugin(store=store)

    def boom(*args, **kwargs):
        raise RuntimeError("bug")

    monkeypatch.setattr("nuvel.plugins.platform_sync_plugin._extract_usage", boom)
    monkeypatch.setattr("nuvel.plugins.platform_sync_plugin._safe_serialize", boom)

    assert await _run_agent([plugin]) == "done"


# ── Without a database ───────────────────────────────────────────────


@pytest.mark.parametrize("dsn,expected", [
    ("postgresql://u:p@h/db", "postgresql://u:p@h/db"),
    ("postgresql://u:p@h/db?sslmode=require&channel_binding=require",
     "postgresql://u:p@h/db?sslmode=require"),
])
def test_normalize_dsn(dsn, expected):
    assert normalize_dsn(dsn) == expected


def test_store_repr_hides_the_dsn():
    assert "hunter2" not in repr(PlatformStore("postgresql://u:hunter2@h/db"))


def test_invalid_schema_name_rejected():
    with pytest.raises(ValueError):
        PlatformStore("postgresql://h/db", schema='x"; DROP')


def test_migrations_are_numbered_and_packaged():
    found = migrations()
    assert [v for v, _, _ in found] == list(range(1, len(found) + 1))
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    packaged: set[str] = set()
    for pattern in config["tool"]["setuptools"]["package-data"]["nuvel"]:
        packaged.update(glob.glob(str(ROOT / "nuvel" / pattern), recursive=True))
    sql = {str(p) for p in (ROOT / "nuvel/platform_sync/migrations").glob("*.sql")}
    assert sql and sql <= packaged


def test_cli_sync_is_a_noop_when_disabled_and_never_raises(monkeypatch, caplog):
    assert sync_prices({"m": {"input": 1.0, "output": 2.0}}) is False
    assert caplog.text == ""

    monkeypatch.setenv(platform_sync.ENV_ENABLED, "1")
    monkeypatch.setenv(platform_sync.ENV_DATABASE_URL, _closed_port_dsn())
    assert sync_prices({"m": {"input": 1.0, "output": 2.0}}) is False
    assert sync_eval_result(_eval_result()) is False
    assert len(_warnings(caplog)) == 2
    assert "hunter2" not in caplog.text


def _write_dotenv(directory, monkeypatch) -> None:
    (directory / ".env").write_text(
        f"{platform_sync.ENV_ENABLED}=1\n{platform_sync.ENV_DATABASE_URL}={_closed_port_dsn()}\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(directory)


def test_dotenv_in_the_working_directory_configures_the_store(tmp_path, monkeypatch):
    _write_dotenv(tmp_path, monkeypatch)
    monkeypatch.delenv(platform_sync.ENV_ENABLED)
    monkeypatch.delenv(platform_sync.ENV_DATABASE_URL)
    assert isinstance(store_from_env(), PlatformStore)


def test_suite_ignores_a_dotenv_that_enables_sync(tmp_path, monkeypatch, caplog):
    # conftest's isolation: an attempted write would warn about the closed port.
    _write_dotenv(tmp_path, monkeypatch)
    assert sync_prices({"m": {"input": 1.0, "output": 2.0}}) is False
    assert caplog.text == ""


def _eval_result() -> EvalSuiteResult:
    return EvalSuiteResult(
        schema_version="2.0",
        skill="s",
        suite="smoke",
        timestamp="2026-11-03T10:00:00+00:00",
        model="m",
        examples=[
            ScoredExample(
                id="a", input="in-a", score=0.9, passed=True, cost=0.25, notes=["n"],
                evaluator_results=[EvaluatorResult(evaluator="deterministic", name="d", score=0.9)],
            ),
            ScoredExample(id="b", input="in-b", cost=0.5),
        ],
    )


def test_eval_rows_are_deterministic():
    run, rows = eval_rows(_eval_result())
    again, _ = eval_rows(_eval_result())
    assert run.run_id == again.run_id and run.run_id.startswith("eval-")
    assert run.kind == "eval" and run.agent == "s" and run.cost_usd == 0.75
    assert [r.example_id for r in rows] == ["a", "b"]


# ── Against Postgres ─────────────────────────────────────────────────


def _pg_bindir() -> Path | None:
    candidates = [shutil.which("initdb")] + sorted(
        glob.glob("/usr/lib/postgresql/*/bin/initdb"), reverse=True
    )
    for found in candidates:
        if found:
            return Path(found).parent
    return None


@pytest.fixture(scope="session")
def pg_dsn(tmp_path_factory):
    dsn = os.environ.get("NUVEL_PLATFORM_TEST_DSN", "").strip()
    if dsn:
        yield dsn
        return
    bindir = _pg_bindir()
    if bindir is None or os.geteuid() == 0:
        pytest.skip("no NUVEL_PLATFORM_TEST_DSN and no usable local initdb")
    base = tmp_path_factory.mktemp("pg")
    data = base / "data"
    subprocess.run(
        [bindir / "initdb", "-D", data, "-U", "nuvel", "-A", "trust"],
        check=True, capture_output=True,
    )
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    subprocess.run(
        [bindir / "pg_ctl", "-D", data, "-w", "-l", base / "log", "-o",
         f"-p {port} -c listen_addresses=127.0.0.1 -c unix_socket_directories={base}",
         "start"],
        check=True, capture_output=True,
    )
    try:
        yield f"postgresql://nuvel@127.0.0.1:{port}/postgres"
    finally:
        subprocess.run([bindir / "pg_ctl", "-D", data, "-m", "immediate", "stop"],
                       capture_output=True)


@pytest.fixture
async def store(pg_dsn):
    import asyncpg

    schema = f"t_{uuid.uuid4().hex[:12]}"
    yield PlatformStore(pg_dsn, schema=schema)
    conn = await asyncpg.connect(pg_dsn)
    await conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
    await conn.close()


async def _fetch(store: PlatformStore, query: str, *args):
    conn = await store._connect()
    try:
        return await conn.fetch(query.replace("{s}", f'"{store.schema}"'), *args)
    finally:
        await conn.close()


async def _count(store: PlatformStore, table: str) -> int:
    return (await _fetch(store, f"SELECT count(*) AS n FROM {{s}}.{table}"))[0]["n"]


def _run_row(**overrides) -> RunRow:
    return RunRow(**{
        "run_id": "r1", "kind": "agent", "agent": "root",
        "started_at": datetime(2026, 11, 3, tzinfo=timezone.utc), **overrides,
    })


def _event(seq: int, **data) -> TraceEventRow:
    return TraceEventRow(
        seq=seq, event="tool_end", occurred_at=datetime(2026, 11, 3, tzinfo=timezone.utc),
        agent="root", data=data,
    )


async def test_pg_neon_style_dsn_connects_where_raw_asyncpg_refuses(pg_dsn):
    import asyncpg

    neon_style = f"{pg_dsn}?channel_binding=prefer"
    with pytest.raises(asyncpg.PostgresError):
        await asyncpg.connect(neon_style)
    conn = await PlatformStore(neon_style)._connect()
    assert await conn.fetchval("SELECT 1") == 1
    await conn.close()


async def test_pg_migration_is_versioned_and_repeatable(store):
    versions = [v for v, _, _ in migrations()]
    assert await store.migrate() == versions
    assert await store.migrate() == []
    rows = await _fetch(store, "SELECT version FROM {s}.schema_migrations ORDER BY version")
    assert [r["version"] for r in rows] == versions


async def test_pg_concurrent_first_writes_migrate_once(pg_dsn, store):
    other = PlatformStore(pg_dsn, schema=store.schema)
    await asyncio.gather(
        store.write_run(_run_row(run_id="a")), other.write_run(_run_row(run_id="b"))
    )
    assert await _count(store, "runs") == 2


async def test_pg_happy_path_through_a_real_run(store):
    plugin = PlatformSyncPlugin(store=store)

    assert await _run_agent([plugin]) == "done"
    await plugin.close()

    runs = await _fetch(store, "SELECT * FROM {s}.runs")
    assert len(runs) == 1
    run = runs[0]
    assert run["kind"] == "agent" and run["agent"] == "root" and run["model"] == PRICED_MODEL
    assert (run["llm_calls"], run["tool_calls"]) == (2, 1)
    assert (run["prompt_tokens"], run["completion_tokens"]) == (200, 40)
    assert run["cost_usd"] > 0

    events = await _fetch(store, "SELECT * FROM {s}.trace_events ORDER BY seq")
    assert [e["event"] for e in events][0] == "run_start"
    assert [e["event"] for e in events][-1] == "run_end"
    assert len(events) == 8
    assert all(e["run_id"] == run["run_id"] for e in events)
    tool_end = next(e for e in events if e["event"] == "tool_end")
    assert json.loads(tool_end["data"])["result"] == {"status": "ok", "q": "x"}


async def test_pg_duplicate_sync_adds_no_rows(store):
    run = _run_row(prompt_tokens=10)
    events = [_event(0, a=1), _event(1, a=2)]
    results = [EvalResultRow(example_id="e", skill="s", suite="x", input="i", score=0.5)]

    await store.write_run(run, events, results)
    await store.write_run(run, events, results)

    assert await _count(store, "runs") == 1
    assert await _count(store, "trace_events") == 2
    assert await _count(store, "eval_results") == 1


async def test_pg_resync_updates_the_run_row_and_keeps_events(store):
    await store.write_run(_run_row(duration_ms=1), [_event(0, v="first")])
    await store.write_run(_run_row(duration_ms=2), [_event(0, v="second")])

    assert (await _fetch(store, "SELECT duration_ms FROM {s}.runs"))[0]["duration_ms"] == 2
    data = (await _fetch(store, "SELECT data FROM {s}.trace_events"))[0]["data"]
    assert json.loads(data) == {"v": "first"}


async def test_pg_failed_write_leaves_nothing_behind(store):
    await store.migrate()
    bad = TraceEventRow(seq=0, event="x", occurred_at="not a datetime")
    with pytest.raises(Exception):
        await store.write_run(_run_row(), [bad])
    assert await _count(store, "runs") == 0


async def test_pg_values_postgres_would_reject_are_cleaned(store):
    await store.write_run(
        _run_row(agent="ro\x00ot"),
        [_event(0, text="nul\x00 and lone \ud800 surrogate", score=float("nan"),
                 obj=object, **{"k\x00": ["\x00"]})],
    )
    assert (await _fetch(store, "SELECT agent FROM {s}.runs"))[0]["agent"] == "root"
    data = json.loads((await _fetch(store, "SELECT data FROM {s}.trace_events"))[0]["data"])
    assert data["text"].startswith("nul and lone")
    assert data["score"] is None
    assert data["k"] == [""]


async def test_pg_eval_sync_is_idempotent(store):
    for _ in range(2):
        run, rows = eval_rows(_eval_result())
        await store.write_run(run, eval_results=rows)

    assert await _count(store, "runs") == 1
    fetched = await _fetch(store, "SELECT * FROM {s}.eval_results ORDER BY example_id")
    assert [r["example_id"] for r in fetched] == ["a", "b"]
    assert fetched[0]["passed"] is True and fetched[1]["score"] is None
    assert json.loads(fetched[0]["evaluator_results"])[0]["name"] == "d"
    assert json.loads(fetched[0]["notes"]) == ["n"]


async def test_pg_prices_are_idempotent_and_track_changes(store):
    entries = {
        "_comment": "ignored",
        "m1": {"input": 0.001, "output": 0.002},
        "m2": {"input": 0.003, "output": 0.004},
    }
    await store.write_prices(entries)
    first = {r["model"]: r for r in await _fetch(store, "SELECT * FROM {s}.model_prices")}
    assert set(first) == {"m1", "m2"}

    await store.write_prices(entries)
    same = {r["model"]: r for r in await _fetch(store, "SELECT * FROM {s}.model_prices")}
    assert same["m1"]["updated_at"] == first["m1"]["updated_at"]

    await store.write_prices({"m1": {"input": 0.01, "output": 0.002}})
    changed = {r["model"]: r for r in await _fetch(store, "SELECT * FROM {s}.model_prices")}
    assert len(changed) == 2
    assert changed["m1"]["input_per_token"] == 0.01
    assert changed["m1"]["updated_at"] > first["m1"]["updated_at"]
    assert changed["m2"]["updated_at"] == first["m2"]["updated_at"]


async def test_pg_bundled_pricing_file_syncs(store):
    entries = pricing.load(ROOT / "nuvel/plugins/pricing.json").entries
    await store.write_prices(entries)
    assert await _count(store, "model_prices") == len(entries)


def test_pg_cli_helpers_write_to_the_configured_database(pg_dsn, monkeypatch):
    # asyncio.run inside the helpers needs a sync test, so no `store` fixture.
    scoped = PlatformStore(pg_dsn, schema=f"t_{uuid.uuid4().hex[:12]}")
    monkeypatch.setattr(platform_sync, "store_from_env", lambda: scoped)

    async def counts():
        return [await _count(scoped, t) for t in ("runs", "eval_results", "model_prices")]

    async def drop():
        conn = await scoped._connect()
        await conn.execute(f'DROP SCHEMA IF EXISTS "{scoped.schema}" CASCADE')
        await conn.close()

    try:
        for _ in range(2):
            assert sync_eval_result(_eval_result()) is True
            assert sync_prices({"m1": {"input": 1.0, "output": 2.0}}) is True
        assert asyncio.run(counts()) == [1, 2, 1]
    finally:
        asyncio.run(drop())


# ── CLI hooks ────────────────────────────────────────────────────────


def _pricing_args(tmp_path, **overrides) -> argparse.Namespace:
    path = tmp_path / "pricing.json"
    path.write_text(json.dumps({"m1": {"input": 1.0, "output": 2.0}}), encoding="utf-8")
    return argparse.Namespace(target=str(path), dry_run=False, **overrides)


@pytest.fixture
def synced_prices(monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(pricing, "sync_prices", lambda entries: calls.append(dict(entries)))
    monkeypatch.setattr(
        pricing, "fetch_openrouter", lambda: {"m1": {"input": 1.0, "output": 2.0}}
    )
    return calls


def test_pricing_sync_writes_prices_even_when_the_file_is_unchanged(tmp_path, synced_prices):
    assert pricing._cmd_sync(_pricing_args(tmp_path)) == 0
    assert synced_prices == [{"m1": {"input": 1.0, "output": 2.0}}]


def test_pricing_dry_run_writes_nothing(tmp_path, synced_prices):
    args = _pricing_args(tmp_path)
    args.dry_run = True
    assert pricing._cmd_sync(args) == 0
    assert pricing._cmd_add(argparse.Namespace(target=args.target, dry_run=True, model="m1")) == 0
    assert synced_prices == []


def test_pricing_add_writes_prices_when_unchanged(tmp_path, synced_prices):
    args = _pricing_args(tmp_path, model="m1")
    assert pricing._cmd_add(args) == 0
    assert synced_prices == [{"m1": {"input": 1.0, "output": 2.0}}]


def test_pricing_add_writes_prices_when_added_or_updated(tmp_path, synced_prices, monkeypatch):
    monkeypatch.setattr(pricing, "fetch_openrouter", lambda: {
        "m1": {"input": 3.0, "output": 4.0},
        "m2": {"input": 5.0, "output": 6.0},
    })
    args = _pricing_args(tmp_path, model="m2")
    assert pricing._cmd_add(args) == 0
    args.model = "m1"
    assert pricing._cmd_add(args) == 0
    assert synced_prices == [
        {"m1": {"input": 1.0, "output": 2.0}, "m2": {"input": 5.0, "output": 6.0}},
        {"m1": {"input": 3.0, "output": 4.0}, "m2": {"input": 5.0, "output": 6.0}},
    ]
