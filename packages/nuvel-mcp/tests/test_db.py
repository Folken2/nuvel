"""Postgres storage: settings, migrations, the four stores and the usage recorder.

Tests marked ``requires_pg`` (or using ``pg_db``) need
``NUVEL_MCP_TEST_DATABASE_URL``; the rest run everywhere.
"""

import copy
import json
import sys
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import _pg
from nuvel_mcp import db as db_mod
from nuvel_mcp.db import DatabaseError, DatabaseSettings, migrations, redact_dsn
from nuvel_mcp.oauth import OAuthConfig, OAuthError, OAuthProvider, OAuthStoreError
from nuvel_mcp.traces import LocalTraceStore, normalize
from nuvel_mcp.usage import UsageRecorder

FIXTURE = Path(__file__).parent / "fixtures" / "skill_run_example.json"
PLUGIN = {"name": "chief-of-staff", "version": "0.3.0"}
URL = "postgresql://svc:s3cr3t@db.example.test:5432/app?sslmode=require"


def _trace(tenant=None, run_id=None, started_at="2026-10-20T09:30:12Z", **changes):
    doc = json.loads(FIXTURE.read_text())
    doc.update({"run_id": run_id or str(uuid.uuid4()), "started_at": started_at, **changes})
    return normalize(doc, tenant=tenant)


def _signal(tenant=None, signal_id=None):
    s = {"schema": "nuvel.recipe_signal/1", "signal_id": signal_id or str(uuid.uuid4()),
         "recipe_id": str(uuid.uuid4()), "install_id": str(uuid.uuid4()), "plugin": PLUGIN,
         "category": "client-followup", "event": "used", "at": "2026-10-27T10:00:00Z",
         "uses": 3, "successes": 2, "corrections": 0}
    if tenant is not None:
        s["tenant"] = tenant
    return s


def _recipe(tenant=None, recipe_id=None, version=1):
    r = {"schema": "nuvel.recipe/2", "recipe_id": recipe_id or str(uuid.uuid4()),
         "install_id": str(uuid.uuid4()), "plugin": PLUGIN, "version": version,
         "category": "client-followup", "language": "es", "title": "t", "when_to_use": "w",
         "steps": []}
    if tenant is not None:
        r["tenant"] = tenant
    return r


# --- settings and redaction (no database) -------------------------------------------


def test_settings_absent_without_url():
    assert DatabaseSettings.from_env({}) is None
    assert DatabaseSettings.from_env({"NUVEL_MCP_DATABASE_URL": "  "}) is None


def test_settings_defaults_and_overrides():
    s = DatabaseSettings.from_env({"NUVEL_MCP_DATABASE_URL": URL})
    assert (s.schema, s.pool_max, s.auto_migrate, s.usage) == ("nuvel_mcp", 10, True, True)
    s = DatabaseSettings.from_env({
        "NUVEL_MCP_DATABASE_URL": URL, "NUVEL_MCP_DATABASE_SCHEMA": "beta_1",
        "NUVEL_MCP_DATABASE_POOL_MAX": "3", "NUVEL_MCP_DATABASE_AUTO_MIGRATE": "0",
        "NUVEL_MCP_USAGE": "off"})
    assert (s.schema, s.pool_max, s.auto_migrate, s.usage) == ("beta_1", 3, False, False)


@pytest.mark.parametrize("env, needle", [
    ({"NUVEL_MCP_DATABASE_SCHEMA": "Bad-Name"}, "SCHEMA"),
    ({"NUVEL_MCP_DATABASE_SCHEMA": 'x"; drop'}, "SCHEMA"),
    ({"NUVEL_MCP_DATABASE_POOL_MAX": "zero"}, "POOL_MAX"),
    ({"NUVEL_MCP_DATABASE_POOL_MAX": "0"}, "POOL_MAX"),
    ({"NUVEL_MCP_USAGE": "maybe"}, "NUVEL_MCP_USAGE"),
])
def test_invalid_settings_never_quote_the_url(env, needle):
    with pytest.raises(ValueError) as exc:
        DatabaseSettings.from_env({"NUVEL_MCP_DATABASE_URL": URL, **env})
    assert needle in str(exc.value) and "s3cr3t" not in str(exc.value)


def test_settings_repr_hides_the_url():
    s = DatabaseSettings.from_env({"NUVEL_MCP_DATABASE_URL": URL})
    assert "s3cr3t" not in repr(s) and "example" not in repr(s)


@pytest.mark.parametrize("text, secret", [
    (URL, "s3cr3t"),
    ("failed for postgres://u:p%40ss@h/db", "p%40ss"),
    ("host=h user=u password=hunter2 dbname=d", "hunter2"),
    ("host=h password='two words' dbname=d", "two words"),
    ("postgresql://h/db?user=u&password=pw9&sslmode=require", "pw9"),
])
def test_redact_dsn(text, secret):
    out = redact_dsn(text)
    assert secret not in out and "***" in out


def test_redact_leaves_passwordless_urls_alone():
    assert redact_dsn("postgresql://nuvel@localhost:5432/db") == "postgresql://nuvel@localhost:5432/db"


def test_missing_driver_names_the_install_command(monkeypatch):
    monkeypatch.setitem(sys.modules, "psycopg", None)
    with pytest.raises(DatabaseError) as exc:
        db_mod.Database(DatabaseSettings(url=URL))
    assert db_mod.INSTALL_HINT in str(exc.value) and "s3cr3t" not in str(exc.value)


def test_migrations_are_packaged_in_order():
    found = migrations()
    assert [v for v, _, _ in found] == sorted(v for v, _, _ in found)
    assert found[0][:2] == (1, "initial")
    for table in db_mod.TABLES:
        assert f"{{schema}}.{table}" in found[0][2]


# --- migrations -------------------------------------------------------------------------


@_pg.requires_pg
def test_migrate_is_idempotent_and_reports_status():
    db = _pg.open_db(migrate=False)
    assert db.status()["applied"] == [] and db.status()["pending"] == [1]
    assert db.migrate() == [(1, "initial")]
    assert db.migrate() == []
    status = db.status()
    assert status["applied"] == [1] and status["pending"] == []
    assert set(status["rows"]) == set(db_mod.TABLES) and not any(status["rows"].values())


@_pg.requires_pg
def test_concurrent_migrations_apply_once():
    schema = f"t_{uuid.uuid4().hex[:12]}"
    dbs = [_pg.open_db(schema, migrate=False) for _ in range(4)]
    results, errors = [], []

    def run(d):
        try:
            results.append(d.migrate())
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(d,)) for d in dbs]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert sorted(results, key=len) == [[], [], [], [(1, "initial")]]


@_pg.requires_pg
def test_ping_and_driver_error_on_closed_pool():
    db = _pg.open_db()
    assert db.ping() is True
    db.close()
    assert db.ping() is False


@_pg.requires_pg
def test_bad_credentials_are_redacted():
    from urllib.parse import urlsplit, urlunsplit

    parts = urlsplit(_pg.DSN)
    host = parts.hostname or "localhost"
    netloc = f"nobody_{uuid.uuid4().hex[:6]}:wr0ngpw@{host}:{parts.port or 5432}"
    bad = urlunsplit(parts._replace(netloc=netloc, path="/does_not_exist"))
    with pytest.raises(DatabaseError) as exc:
        db_mod.Database(DatabaseSettings(url=bad), connect_timeout=3)
    assert "wr0ngpw" not in str(exc.value)


# --- trace store ------------------------------------------------------------------------


def test_trace_store_dedupes_per_tenant(pg_db):
    from nuvel_mcp.db.stores import PostgresTraceStore

    store = PostgresTraceStore(pg_db)
    run = _trace("acme")
    assert store.append(run) is True
    assert store.append(copy.deepcopy(run)) is False
    assert PostgresTraceStore(pg_db).append(copy.deepcopy(run)) is False
    assert store.append(_trace("other", run_id=run["run_id"])) is True
    assert store.append(_trace(None, run_id=run["run_id"])) is True
    # Stricter than the day files: the same run on another day is still one run.
    assert store.append(_trace("acme", run_id=run["run_id"], started_at="2026-10-22T09:00:00Z")) is False


def test_trace_store_query_matches_local_store(pg_db, tmp_path):
    from nuvel_mcp.db.stores import PostgresTraceStore

    pg, local = PostgresTraceStore(pg_db), LocalTraceStore(tmp_path)
    runs = [
        _trace("acme", started_at="2026-10-19T08:00:00Z"),
        _trace("acme", started_at="2026-10-20T09:30:00Z"),
        _trace("beta", started_at="2026-10-20T10:00:00Z"),
        _trace(None, started_at="2026-10-21T11:00:00Z"),
    ]
    for r in runs:
        assert pg.append(r) == local.append(r) is True
    plugin, skill = runs[0]["plugin"]["name"], runs[0]["skill"]["name"]
    since = datetime(2026, 10, 20, 9, 0, tzinfo=timezone.utc)

    def ids(rows):
        return sorted(r["run_id"] for r in rows)

    for tenant in (None, "acme", "beta", "nobody"):
        assert ids(pg.query(plugin, skill, since, tenant)) == ids(local.query(plugin, skill, since, tenant))
    assert pg.query(plugin, skill, since, None)[0] == runs[1]  # the stored record, unchanged
    naive = since.replace(tzinfo=None)
    assert ids(pg.query(plugin, skill, naive, None)) == ids(pg.query(plugin, skill, since, None))
    assert pg.query("Bad Name", skill, since, None) == []
    assert pg.query(plugin, "other-skill", since, None) == []


def test_trace_store_rejects_non_identifier_names(pg_db):
    from nuvel_mcp.db.stores import PostgresTraceStore

    run = _trace()
    run["plugin"] = {**run["plugin"], "name": "Not An Id"}
    with pytest.raises(ValueError):
        PostgresTraceStore(pg_db).append(run)


def test_trace_columns_are_populated(pg_db):
    from nuvel_mcp.db.stores import PostgresTraceStore

    run = _trace("acme", user_corrected=True)
    PostgresTraceStore(pg_db).append(run)
    with pg_db.connection() as conn:
        row = conn.execute(
            f"SELECT tenant, plugin_name, skill_name, outcome, duration_s, user_corrected, "
            f"started_at FROM {pg_db.table('skill_runs')}").fetchone()
        daily = conn.execute(f"SELECT runs, corrected FROM {pg_db.table('skill_runs_daily')}").fetchone()
    assert row[:6] == ("acme", run["plugin"]["name"], run["skill"]["name"], run["outcome"],
                       run["duration_s"], True)
    assert row[6] == datetime(2026, 10, 20, 9, 30, tzinfo=timezone.utc)
    assert daily == (1, 1)


# --- recipe store -----------------------------------------------------------------------


def test_recipe_store_signals_and_versions(pg_db):
    from nuvel_mcp.db.stores import PostgresRecipeStore
    from nuvel_mcp.recipes import RecipeStore

    store = PostgresRecipeStore(pg_db)
    assert isinstance(store, RecipeStore)
    sig = _signal("acme")
    assert store.append_signal(sig) is True
    assert store.append_signal(dict(sig)) is False
    assert store.append_signal(_signal("beta", sig["signal_id"])) is True
    rec = _recipe("acme")
    assert store.put_recipe(rec) is True
    assert store.put_recipe(dict(rec)) is False
    assert store.put_recipe(_recipe("acme", rec["recipe_id"], version=2)) is True
    assert store.put_recipe(_recipe(None, rec["recipe_id"])) is True
    with pytest.raises(ValueError):
        store.put_recipe(_recipe(recipe_id="../escape"))
    with pytest.raises(ValueError):
        store.put_recipe(_recipe(version=True))


# --- OAuth store ------------------------------------------------------------------------


def _provider(db):
    from nuvel_mcp.db.stores import PostgresOAuthStore

    return OAuthProvider(OAuthConfig(), PostgresOAuthStore(db))


def test_oauth_writes_are_seen_by_another_instance(pg_db):
    a, b = _provider(pg_db), _provider(pg_db)
    assert b.list_clients() == []
    record, key = a.create_client("tester", url_key=True)
    assert [c["client_id"] for c in b.list_clients()] == [record["client_id"]]
    assert b.validate_url_key(key).client_id == record["client_id"]
    assert b.revoke(record["client_id"])["url_keys"] == 1
    assert a.validate_url_key(key) is None


def test_oauth_store_keeps_hashes_only(pg_db):
    _, key = _provider(pg_db).create_client("tester", url_key=True)
    _, secret = _provider(pg_db).create_client("agent")
    with pg_db.connection() as conn:
        dump = json.dumps([r[0] for t in ("oauth_clients", "oauth_url_keys")
                           for r in conn.execute(f"SELECT record FROM {pg_db.table(t)}")])
    assert key not in dump and secret not in dump


def test_oauth_transaction_rolls_back_and_passes_provider_errors(pg_db):
    from nuvel_mcp.db.stores import PostgresOAuthStore

    store = PostgresOAuthStore(pg_db)
    with pytest.raises(OAuthError):
        with store.transaction() as data:
            data["clients"]["c1"] = {"client_id": "c1"}
            raise OAuthError("invalid_grant", "nope")
    with pytest.raises(KeyError):
        with store.transaction() as data:
            data["clients"]["c2"] = {"client_id": "c2"}
            raise KeyError("boom")
    with store.reading() as data:
        assert data["clients"] == {}


def test_oauth_unchanged_transaction_writes_nothing(pg_db):
    from nuvel_mcp.db.stores import PostgresOAuthStore

    store = PostgresOAuthStore(pg_db)

    def version():
        with pg_db.connection() as conn:
            return conn.execute(f"SELECT version FROM {pg_db.table('oauth_state')}").fetchone()[0]

    with store.transaction() as data:
        data["clients"]["c1"] = {"client_id": "c1", "n": 1}
    v1 = version()
    with store.transaction():
        pass
    assert version() == v1
    with store.transaction() as data:
        data["clients"]["c1"]["n"] = 2
        data["url_keys"]["h"] = {"client_id": "c1"}
    with store.transaction() as data:
        del data["url_keys"]["h"]
    assert version() == v1 + 2
    with pg_db.connection() as conn:
        assert conn.execute(f"SELECT record->>'n', client_id FROM {pg_db.table('oauth_clients')}").fetchone() == ("2", "c1")
        assert conn.execute(f"SELECT count(*) FROM {pg_db.table('oauth_url_keys')}").fetchone()[0] == 0


def test_oauth_concurrent_writers_lose_nothing(pg_db):
    from nuvel_mcp.db.stores import PostgresOAuthStore

    stores = [PostgresOAuthStore(pg_db) for _ in range(4)]

    def write(store, n):
        for i in range(10):
            with store.transaction() as data:
                data["clients"][f"c{n}-{i}"] = {"client_id": f"c{n}-{i}"}

    threads = [threading.Thread(target=write, args=(s, n)) for n, s in enumerate(stores)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    with PostgresOAuthStore(pg_db).reading() as data:
        assert len(data["clients"]) == 40


def test_oauth_database_failure_fails_closed():
    from nuvel_mcp.db.stores import PostgresOAuthStore

    if not _pg.DSN:
        pytest.skip("NUVEL_MCP_TEST_DATABASE_URL is not set")
    db = _pg.open_db()
    provider = _provider(db)
    _, key = provider.create_client("tester", url_key=True)
    db.pool.close()
    with pytest.raises(OAuthStoreError) as exc:
        with PostgresOAuthStore(db).reading():
            pass
    assert "OAuth store unavailable" in str(exc.value)
    with pytest.raises(OAuthStoreError):
        with provider.store.transaction():
            pass
    # The HTTP layer turns this into a 401 (fails closed), as for an unreadable file.
    with pytest.raises(OAuthStoreError):
        provider.validate_url_key(key)


# --- usage --------------------------------------------------------------------------------


def _event(**changes):
    return {"at": datetime.now(timezone.utc), "tenant": "acme", "transport": "http",
            "method": "tools/call", "tool": "search_skills", "outcome": "ok",
            "error_code": None, "duration_ms": 12, **changes}


def test_usage_store_and_daily_view(pg_db):
    from nuvel_mcp.db.stores import PostgresUsageStore

    PostgresUsageStore(pg_db).record_batch([
        _event(), _event(duration_ms=40), _event(outcome="error", error_code=-32602),
        _event(method="tools/list", tool=None, tenant=None, transport="stdio"),
    ])
    with pg_db.connection() as conn:
        rows = conn.execute(
            f"SELECT tenant, method, tool, calls, errors FROM {pg_db.table('usage_daily')} "
            "ORDER BY method, tenant NULLS LAST").fetchall()
    assert rows == [("acme", "tools/call", "search_skills", 3, 1), (None, "tools/list", None, 1, 0)]


def test_usage_store_rejects_unknown_outcome(pg_db):
    from nuvel_mcp.db.stores import PostgresUsageStore

    with pytest.raises(pg_db.driver_error):
        PostgresUsageStore(pg_db).record_batch([_event(outcome="weird")])


class _FakeUsage:
    def __init__(self, fail=False):
        self.batches, self.fail = [], fail
        self.ready = threading.Event()

    def record_batch(self, events):
        if self.fail:
            raise RuntimeError("down")
        self.batches.append(list(events))
        self.ready.set()


def test_recorder_writes_in_the_background():
    store = _FakeUsage()
    recorder = UsageRecorder(store)
    recorder.record(_event())
    assert store.ready.wait(5)
    recorder.close()
    assert sum(len(b) for b in store.batches) == 1 and recorder.dropped == 0


def test_recorder_batches_and_flushes_on_close():
    store = _FakeUsage()
    recorder = UsageRecorder(store, start=False)
    for i in range(450):
        recorder.record(_event(duration_ms=i))
    recorder.close()
    assert [len(b) for b in store.batches] == [200, 200, 50]


def test_recorder_drops_on_overflow_and_failure(capsys):
    recorder = UsageRecorder(_FakeUsage(), start=False)
    recorder.QUEUE_MAX = 3
    recorder._queue = type(recorder._queue)(maxsize=3)
    for _ in range(5):
        recorder.record(_event())
    assert recorder.dropped == 2
    recorder.store = _FakeUsage(fail=True)
    recorder.flush()
    assert recorder.dropped == 5
    err = capsys.readouterr().err
    assert err.count("usage: dropped") == 1  # rate-limited
    clock = [0.0]
    recorder.clock = lambda: clock[0]
    recorder._last_log = 0.0
    clock[0] = recorder.LOG_INTERVAL + 1
    recorder.record(_event())
    recorder.flush()
    assert "usage: dropped" in capsys.readouterr().err
