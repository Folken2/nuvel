"""Usage insights: the client app per session (``initialize`` clientInfo) and
the caller's country, recorded on every usage event -- never an IP address,
never tool arguments.

Tests using ``pg_db`` need ``NUVEL_MCP_TEST_DATABASE_URL``.
"""

import json
import threading
from datetime import datetime, timezone

import pytest

from _mcp_http_helpers import INIT, TIMEOUT, Client, call, isolate_env, make_hub
from nuvel_mcp.http_transport import HttpConfig, make_server
from nuvel_mcp.server import SkillsMCPServer
from nuvel_mcp.skills_loader import SkillsLoader
from nuvel_mcp.usage import client_info, sanitize_label


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch, tmp_path):
    isolate_env(monkeypatch, tmp_path)


class _Recorder:
    def __init__(self):
        self.events = []

    def record(self, event):
        self.events.append(event)


def _init(name="Claude", version="1.2.3", protocol="2025-06-18", msg_id=1):
    params = {"protocolVersion": protocol, "capabilities": {}}
    if name is not None:
        params["clientInfo"] = {"name": name, "version": version}
    return {"jsonrpc": "2.0", "id": msg_id, "method": "initialize", "params": params}


# --- sanitizing -------------------------------------------------------------------


@pytest.mark.parametrize("raw, expected", [
    ("claude-ai", "claude-ai"),
    ("  Claude Desktop (macOS) ", "Claude Desktop (macOS)"),
    ("@scope/pkg+x:1.0_beta", "@scope/pkg+x:1.0_beta"),
    ("x" * 80, "x" * 64),
    ("Robert'); DROP TABLE--", "unknown"),
    ("café", "unknown"),
    ("line\nbreak", "unknown"),
    ("<script>", "unknown"),
    ("   ", "unknown"),
    ("", "unknown"),
    (None, None),
    (42, "unknown"),
    ({"a": 1}, "unknown"),
])
def test_sanitize_label(raw, expected):
    assert sanitize_label(raw) == expected


def test_client_info_from_initialize_params():
    assert client_info(_init()["params"]) == {
        "client_name": "Claude", "client_version": "1.2.3", "protocol_version": "2025-06-18"}
    assert client_info({}) == {
        "client_name": None, "client_version": None, "protocol_version": None}
    assert client_info({"clientInfo": "nope", "protocolVersion": "1;DROP"}) == {
        "client_name": "unknown", "client_version": None, "protocol_version": "unknown"}
    assert client_info("garbage") == {
        "client_name": None, "client_version": None, "protocol_version": None}


# --- stdio: one session per process -------------------------------------------------


def test_stdio_events_carry_the_client_app(tmp_path):
    usage = _Recorder()
    server = SkillsMCPServer(None, feedback_dir=tmp_path, usage=usage)
    server.dispatch(call("check_skill_health", {}, msg_id=0))  # before initialize
    server.dispatch(_init("Cursor", "0.48.1", "2025-03-26"))
    server.dispatch(call("check_skill_health", {}, msg_id=2))
    server.dispatch({"jsonrpc": "2.0", "id": 3, "method": "tools/list"})

    first, *rest = usage.events
    assert (first["client_name"], first["client_version"], first["protocol_version"]) == (
        None, None, None)
    assert [(e["client_name"], e["client_version"], e["protocol_version"]) for e in rest] == [
        ("Cursor", "0.48.1", "2025-03-26")] * 3
    assert all(e["country"] is None for e in usage.events)


def test_hostile_client_info_is_stored_as_unknown(tmp_path):
    usage = _Recorder()
    server = SkillsMCPServer(None, feedback_dir=tmp_path, usage=usage)
    server.dispatch(_init("rm -rf / ; 'x'", "‮1.0", "2025-03-26"))
    event = usage.events[0]
    assert (event["client_name"], event["client_version"]) == ("unknown", "unknown")
    assert "rm -rf" not in json.dumps(usage.events, default=str)


def test_no_usage_recorder_means_no_capture(tmp_path):
    server = SkillsMCPServer(None, feedback_dir=tmp_path)
    assert server.dispatch(_init())["result"]["serverInfo"]


# --- HTTP: per session ---------------------------------------------------------------


@pytest.fixture
def serve():
    started = []

    def factory(mcp, **config):
        httpd = make_server(mcp, "127.0.0.1", 0, HttpConfig(ping_interval=0.2), None,
                            no_auth=True)
        for name, value in config.items():
            setattr(httpd, name, value)
        thread = threading.Thread(target=httpd.serve_forever, args=(0.05,), daemon=True)
        thread.start()
        started.append((httpd, thread))
        return httpd

    yield factory
    for httpd, thread in started:
        httpd.stop()
        httpd.server_close()
        thread.join(TIMEOUT)


def test_http_sessions_keep_their_own_client_app(serve, tmp_path):
    usage = _Recorder()
    mcp = SkillsMCPServer(SkillsLoader.from_base(make_hub(tmp_path)), usage=usage)
    client = Client(serve(mcp))
    _, h1, _ = client.post(_init("claude-ai", "0.1.0", "2025-06-18"))
    _, h2, _ = client.post(_init("Cursor", "1.0", "2025-03-26"))
    s1, s2 = h1["Mcp-Session-Id"], h2["Mcp-Session-Id"]
    client.post(call("list_themes", {}, msg_id=5), s1)
    client.post(call("list_themes", {}, msg_id=6), s2)
    client.post(call("list_themes", {}, msg_id=7), s1)

    seen = [(e["transport"], e["method"], e["client_name"], e["client_version"],
             e["protocol_version"]) for e in usage.events]
    assert seen == [
        ("http", "initialize", "claude-ai", "0.1.0", "2025-06-18"),
        ("http", "initialize", "Cursor", "1.0", "2025-03-26"),
        ("http", "tools/call", "claude-ai", "0.1.0", "2025-06-18"),
        ("http", "tools/call", "Cursor", "1.0", "2025-03-26"),
        ("http", "tools/call", "claude-ai", "0.1.0", "2025-06-18"),
    ]
    # Nothing about the TCP peer is ever on an event.
    assert "127.0.0.1" not in json.dumps(usage.events, default=str)


def test_legacy_sse_session_records_its_client_app(serve, tmp_path):
    from _mcp_http_helpers import read_event

    usage = _Recorder()
    mcp = SkillsMCPServer(SkillsLoader.from_base(make_hub(tmp_path)), usage=usage)
    client = Client(serve(mcp))
    stream, resp = client.open_stream("/sse")
    try:
        endpoint = read_event(resp, "endpoint")
        assert client.post(_init("Windsurf", "2.0"), path=endpoint)[0] == 202
        read_event(resp)
        assert client.post(call("list_themes", {}), path=endpoint)[0] == 202
        read_event(resp)
    finally:
        stream.close()
    assert [(e["method"], e["client_name"]) for e in usage.events] == [
        ("initialize", "Windsurf"), ("tools/call", "Windsurf")]


# --- Postgres ---------------------------------------------------------------------------


def _event(**changes):
    return {"at": datetime.now(timezone.utc), "tenant": "acme", "transport": "http",
            "method": "tools/call", "tool": "search_skills", "outcome": "ok",
            "error_code": None, "duration_ms": 12, **changes}


def test_usage_store_writes_client_columns(pg_db):
    from nuvel_mcp.db.stores import PostgresUsageStore

    PostgresUsageStore(pg_db).record_batch([
        _event(client_name="claude-ai", client_version="0.1", protocol_version="2025-06-18",
               country="DE"),
        _event(client_name="claude-ai", client_version="0.2", protocol_version="2025-06-18"),
        _event(),  # an event shaped like before 0003 still lands
    ])
    with pg_db.connection() as conn:
        rows = conn.execute(
            f"SELECT client_name, client_version, protocol_version, country "
            f"FROM {pg_db.table('usage_events')} ORDER BY id").fetchall()
        daily = conn.execute(
            f"SELECT client_name, calls FROM {pg_db.table('usage_daily')} "
            "ORDER BY client_name NULLS LAST").fetchall()
    assert rows == [("claude-ai", "0.1", "2025-06-18", "DE"),
                    ("claude-ai", "0.2", "2025-06-18", None),
                    (None, None, None, None)]
    assert daily == [("claude-ai", 2), (None, 1)]


def test_country_column_only_takes_two_letter_codes(pg_db):
    from nuvel_mcp.db.stores import PostgresUsageStore

    with pytest.raises(pg_db.driver_error):
        PostgresUsageStore(pg_db).record_batch([_event(country="Germany")])


def test_usage_daily_view_stays_security_invoker(pg_db):
    with pg_db.connection() as conn:
        version = conn.execute("SHOW server_version_num").fetchone()[0]
        options = conn.execute(
            "SELECT reloptions FROM pg_class WHERE oid = %s::regclass",
            (f'"{pg_db.schema}".usage_daily',)).fetchone()[0]
    if int(version) < 150000:
        pytest.skip("security_invoker needs Postgres 15+")
    assert "security_invoker=on" in (options or []) or "security_invoker=true" in (options or [])
