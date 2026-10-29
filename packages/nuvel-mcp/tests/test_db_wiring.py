"""Postgres wiring: store selection, usage events, /healthz, the ``db`` CLI,
``db import`` and an end-to-end URL-key session landing in the database.

Tests using ``pg_db`` / ``db_env`` need ``NUVEL_MCP_TEST_DATABASE_URL``.
"""

import json
import threading
import uuid

import pytest

import _pg
from _mcp_http_helpers import INIT, TIMEOUT, Client, call, isolate_env, make_hub
from nuvel_mcp.cli import _select_store, main as cli_main
from nuvel_mcp.http_transport import HttpConfig, make_server
from nuvel_mcp.oauth import OAuthConfig, OAuthProvider
from nuvel_mcp.recipes import LocalRecipeStore
from nuvel_mcp.server import SkillsMCPServer
from nuvel_mcp.skills_loader import SkillsLoader
from nuvel_mcp.traces import LocalTraceStore
from test_db import _recipe, _signal, _trace


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch, tmp_path):
    isolate_env(monkeypatch, tmp_path)


@pytest.fixture
def db_env(monkeypatch):
    """Point NUVEL_MCP_DATABASE_* at a fresh schema; drop it afterwards."""
    if not _pg.DSN:
        pytest.skip("NUVEL_MCP_TEST_DATABASE_URL is not set")
    import psycopg

    schema = f"c_{uuid.uuid4().hex[:12]}"
    monkeypatch.setenv("NUVEL_MCP_DATABASE_URL", _pg.DSN)
    monkeypatch.setenv("NUVEL_MCP_DATABASE_SCHEMA", schema)
    yield schema
    with psycopg.connect(_pg.DSN, autocommit=True) as conn:
        conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')


def _count(schema, table, where="", params=()):
    import psycopg

    with psycopg.connect(_pg.DSN) as conn:
        return conn.execute(f'SELECT count(*) FROM "{schema}".{table} {where}',
                            params).fetchone()[0]


# --- store selection (no database) ------------------------------------------------


def _never(*_):
    raise AssertionError("must not be called")


def test_auto_and_local_mean_files_without_a_database():
    assert _select_store("trace", "auto", None, _never, None, _never) is None
    assert _select_store("trace", "local", None, _never, None, _never) is None


def test_postgres_without_a_database_is_an_error():
    with pytest.raises(ValueError, match="NUVEL_MCP_DATABASE_URL"):
        _select_store("recipe", "postgres", None, _never, None, _never)


def test_auto_and_postgres_use_the_database():
    db = object()
    made = lambda d: ("pg", d)  # noqa: E731
    assert _select_store("trace", "auto", db, _never, None, made) == ("pg", db)
    assert _select_store("trace", "postgres", db, _never, None, made) == ("pg", db)
    # local stays local even with a database
    assert _select_store("trace", "local", db, _never, None, made) is None


def test_factory_spec_still_loads(tmp_path):
    loaded = _select_store("trace", "mod:make", None,
                           lambda spec, d: (spec, d), tmp_path, _never)
    assert loaded == ("mod:make", tmp_path)


def test_cli_postgres_store_without_url_exits_1(monkeypatch, capsys):
    monkeypatch.setattr("nuvel_mcp.server.SkillsMCPServer.serve", _never)
    assert cli_main(["serve", "--trace-store", "postgres"]) == 1
    assert "NUVEL_MCP_DATABASE_URL" in capsys.readouterr().err


def test_db_commands_need_a_url(capsys):
    assert cli_main(["db", "status"]) == 2
    assert "NUVEL_MCP_DATABASE_URL is not set" in capsys.readouterr().err


# --- usage events (no database) ---------------------------------------------------


class _Recorder:
    def __init__(self):
        self.events = []

    def record(self, event):
        self.events.append(event)


def test_dispatch_records_known_names_only(tmp_path):
    usage = _Recorder()
    server = SkillsMCPServer(None, feedback_dir=tmp_path, usage=usage)
    server.dispatch(INIT)
    server.dispatch({"jsonrpc": "2.0", "method": "notifications/initialized"})
    server.dispatch(call("check_skill_health", {}, msg_id=2))
    server.dispatch(call("drop table users; --", {}, msg_id=3))
    server.dispatch({"jsonrpc": "2.0", "id": 4, "method": "secret/caller-text"})

    assert [e["method"] for e in usage.events] == [
        "initialize", "tools/call", "tools/call", "unknown"]  # notification skipped
    assert [e["tool"] for e in usage.events] == [None, "check_skill_health", "unknown", None]
    assert usage.events[0]["outcome"] == "ok" and usage.events[0]["transport"] == "stdio"
    assert usage.events[3]["outcome"] == "error" and usage.events[3]["error_code"] == -32601
    assert "caller-text" not in json.dumps(usage.events, default=str)
    assert all(e["duration_ms"] >= 0 and e["tenant"] is None for e in usage.events)


def test_a_failing_recorder_never_fails_the_request(tmp_path):
    class Broken:
        def record(self, event):
            raise RuntimeError("boom")

    server = SkillsMCPServer(None, feedback_dir=tmp_path, usage=Broken())
    assert server.dispatch(INIT)["result"]["serverInfo"]


# --- /healthz ---------------------------------------------------------------------


@pytest.fixture
def serve(tmp_path):
    started = []

    def factory(mcp, provider=None, no_auth=True):
        httpd = make_server(mcp, "127.0.0.1", 0, HttpConfig(ping_interval=0.2),
                            provider, no_auth=no_auth)
        thread = threading.Thread(target=httpd.serve_forever, args=(0.05,), daemon=True)
        thread.start()
        started.append((httpd, thread))
        return httpd

    yield factory
    for httpd, thread in started:
        httpd.stop()
        httpd.server_close()
        thread.join(TIMEOUT)


class _Database:
    def __init__(self, up):
        self.up = up

    def ping(self):
        return self.up


@pytest.mark.parametrize("up", [True, False])
def test_healthz_reports_the_database(serve, tmp_path, up):
    mcp = SkillsMCPServer(SkillsLoader.from_base(make_hub(tmp_path)), database=_Database(up))
    status, _, data = Client(serve(mcp)).request("GET", "/healthz")
    body = json.loads(data)
    assert body["storage"] == "postgres"
    if up:
        assert status == 200 and body["status"] == "ok"
    else:
        assert status == 503 and body["error"] == "database unavailable"


def test_healthz_with_a_closed_pool_is_503(serve, tmp_path, pg_db):
    mcp = SkillsMCPServer(SkillsLoader.from_base(make_hub(tmp_path)), database=pg_db)
    httpd = serve(mcp)
    assert Client(httpd).request("GET", "/healthz")[0] == 200
    pg_db.close()
    assert Client(httpd).request("GET", "/healthz")[0] == 503


# --- the CLI on Postgres ----------------------------------------------------------


def test_db_migrate_and_status(db_env, capsys):
    assert cli_main(["db", "status"]) == 1  # pending migrations
    assert "Pending:  1" in capsys.readouterr().out
    assert cli_main(["db", "migrate"]) == 0
    assert "Applied 0001_initial" in capsys.readouterr().out
    assert cli_main(["db", "migrate"]) == 0
    assert "up to date" in capsys.readouterr().out
    assert cli_main(["db", "status", "--json"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["schema"] == db_env and status["applied"] == [1] and not status["pending"]
    assert status["rows"]["usage_events"] == 0


def test_serve_picks_postgres_for_everything(db_env, monkeypatch, capsys, tmp_path):
    from nuvel_mcp.db.stores import PostgresOAuthStore, PostgresRecipeStore, PostgresTraceStore

    seen = {}

    def fake_serve_http(server, host, port, config, provider, no_auth=False):
        seen.update(server=server, provider=provider)

    monkeypatch.setattr("nuvel_mcp.http_transport.serve_http", fake_serve_http)
    monkeypatch.setenv("NUVEL_OAUTH_LOGIN", "none")
    assert cli_main(["serve", "--transport", "http", "--port", "0"]) == 0
    server = seen["server"]
    assert isinstance(server.trace_store, PostgresTraceStore)
    assert isinstance(server.recipe_store, PostgresRecipeStore)
    assert isinstance(seen["provider"].store, PostgresOAuthStore)
    assert server.usage is not None and server.database is not None
    err = capsys.readouterr().err
    assert f"Storage: postgres (schema {db_env}); usage recording on." in err
    assert "NUVEL_MCP_OAUTH_STORE is ignored" in err  # isolate_env sets it
    assert _count(db_env, "schema_migrations") == 1  # auto-migrated
    assert not (tmp_path / "oauth" / "oauth.json").exists()


def test_serve_respects_local_and_usage_off(db_env, monkeypatch, capsys):
    seen = {}
    monkeypatch.setattr("nuvel_mcp.server.SkillsMCPServer.serve",
                        lambda self: seen.update(server=self))
    monkeypatch.setenv("NUVEL_MCP_USAGE", "0")
    assert cli_main(["serve", "--trace-store", "local"]) == 0
    assert seen["server"].trace_store is None and seen["server"].usage is None
    assert "usage recording off" in capsys.readouterr().err


def test_client_commands_use_the_database(db_env, capsys, tmp_path):
    assert cli_main(["client", "create", "--name", "alice", "--url-key"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert _count(db_env, "oauth_url_keys") == 1
    assert not (tmp_path / "oauth" / "oauth.json").exists()
    assert cli_main(["client", "delete", out["client_id"]]) == 0
    assert _count(db_env, "oauth_clients") == 0 and _count(db_env, "oauth_url_keys") == 0


# --- db import --------------------------------------------------------------------


def test_import_copies_the_file_stores_once(db_env, capsys, tmp_path):
    from nuvel_mcp.db import Database, DatabaseSettings
    from nuvel_mcp.db.stores import PostgresOAuthStore

    store_path = tmp_path / "legacy" / "oauth.json"
    file_provider = OAuthProvider(OAuthConfig(store_path=store_path, login="none"))
    record, key = file_provider.create_client("beta-tester", url_key=True)
    feedback = tmp_path / "legacy-feedback"
    traces, recipes = LocalTraceStore(feedback), LocalRecipeStore(feedback)
    for tenant in (None, record["client_id"]):
        traces.append(_trace(tenant=tenant))
        recipes.append_signal(_signal(tenant=tenant))
        recipes.put_recipe(_recipe(tenant=tenant))
    runs = next((feedback / "_runs").glob("*/*.jsonl"))
    runs.write_text(runs.read_text() + "{not json\n")

    args = ["db", "import", "--oauth-store", str(store_path), "--feedback-dir", str(feedback)]
    assert cli_main(args) == 0
    out = capsys.readouterr().out
    assert "skill_runs" in out and "1 unreadable record(s) skipped" in out
    assert _count(db_env, "skill_runs") == 2 and _count(db_env, "recipe_signals") == 2
    assert _count(db_env, "recipes") == 2 and _count(db_env, "oauth_url_keys") == 1

    assert cli_main(args) == 0  # idempotent
    assert "Imported 0 row(s)" in capsys.readouterr().out
    assert _count(db_env, "skill_runs") == 2

    # The imported key authenticates against the database store.
    db = Database(DatabaseSettings(url=_pg.DSN, schema=db_env))
    try:
        provider = OAuthProvider(OAuthConfig(store_path=store_path, login="none"),
                                 PostgresOAuthStore(db))
        assert provider.validate_url_key(key).client_id == record["client_id"]
    finally:
        db.close()


# --- end to end -------------------------------------------------------------------


def test_url_key_session_lands_usage_in_the_database(serve, tmp_path, pg_db):
    from nuvel_mcp.db.stores import PostgresOAuthStore, PostgresTraceStore, PostgresUsageStore
    from nuvel_mcp.usage import UsageRecorder

    provider = OAuthProvider(OAuthConfig(store_path=tmp_path / "unused.json", login="none"),
                             PostgresOAuthStore(pg_db))
    usage = UsageRecorder(PostgresUsageStore(pg_db))
    mcp = SkillsMCPServer(SkillsLoader.from_base(make_hub(tmp_path)), usage=usage,
                          database=pg_db, trace_store=PostgresTraceStore(pg_db))
    httpd = serve(mcp, provider, no_auth=False)
    record, key = provider.create_client("tester", url_key=True)

    client, path = Client(httpd), f"/mcp/k/{key}"
    status, headers, _ = client.post(INIT, path=path)
    assert status == 200
    session = headers["Mcp-Session-Id"]
    status, _, data = client.post(call("search_skills", {"query": "rollback"}), session,
                                  path=path)
    assert status == 200 and "rollback" in data.decode()
    usage.close()

    with pg_db.connection() as conn:
        rows = conn.execute(
            f"SELECT tenant, transport, method, tool, outcome FROM {pg_db.table('usage_events')} "
            "ORDER BY id").fetchall()
        daily = conn.execute(
            f"SELECT sum(calls) FROM {pg_db.table('usage_daily')} WHERE tenant = %s",
            (record["client_id"],)).fetchone()[0]
    assert rows == [
        (record["client_id"], "http", "initialize", None, "ok"),
        (record["client_id"], "http", "tools/call", "search_skills", "ok"),
    ]
    assert daily == 2
