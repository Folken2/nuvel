"""``nuvel-mcp report``: the operator's usage report from Postgres.

Tests using ``db_env`` need ``NUVEL_MCP_TEST_DATABASE_URL``.
"""

import json
import unicodedata
import uuid
from datetime import datetime, timedelta, timezone

import pytest

import _pg
from _mcp_http_helpers import isolate_env
from nuvel_mcp.cli import main
from nuvel_mcp.server import FEEDBACK_TOOLS, HUB_TOOLS
from test_db import _signal, _trace


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch, tmp_path):
    isolate_env(monkeypatch, tmp_path)


@pytest.fixture
def db_env(monkeypatch):
    if not _pg.DSN:
        pytest.skip("NUVEL_MCP_TEST_DATABASE_URL is not set")
    import psycopg

    schema = f"r_{uuid.uuid4().hex[:12]}"
    monkeypatch.setenv("NUVEL_MCP_DATABASE_URL", _pg.DSN)
    monkeypatch.setenv("NUVEL_MCP_DATABASE_SCHEMA", schema)
    yield schema
    with psycopg.connect(_pg.DSN, autocommit=True) as conn:
        conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')


NOW = datetime.now(timezone.utc)


def _ev(tenant, method="tools/call", tool=None, outcome="ok", code=None, ms=10, app=None,
        country=None, ago=timedelta(hours=1)):
    name, version = app or (None, None)
    return {"at": NOW - ago, "tenant": tenant, "transport": "http", "method": method,
            "tool": tool, "outcome": outcome, "error_code": code, "duration_ms": ms,
            "client_name": name, "client_version": version,
            "protocol_version": "2025-06-18" if name else None, "country": country}


def seed(schema):
    from nuvel_mcp.db.stores import PostgresRecipeStore, PostgresTraceStore, PostgresUsageStore

    db = _pg.open_db(schema)
    claude, cursor = ("claude-ai", "0.1.0"), ("Cursor", "1.0")
    PostgresUsageStore(db).record_batch([
        _ev("nvcl_a", "initialize", app=claude, country="DE"),
        _ev("nvcl_a", tool="search_skills", ms=10, app=claude, country="DE"),
        _ev("nvcl_a", tool="search_skills", ms=20, app=claude, country="DE"),
        _ev("nvcl_a", tool="search_skills", ms=30, app=claude, country="DE",
            ago=timedelta(days=1, hours=1)),
        _ev("nvcl_a", tool="search_skills", outcome="error", code=-32602, ms=5, app=claude),
        _ev("nvcl_b", tool="get_skill", outcome="tool_error", ms=50, app=cursor, country="ES"),
        _ev("nvcl_b", "tools/list", app=cursor, country="ES"),
        _ev(None, "tools/list"),
        _ev("nvcl_old", tool="list_themes", ago=timedelta(days=10)),  # outside 7 days
    ])
    traces = PostgresTraceStore(db)
    started = (NOW - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    traces.append(_trace("nvcl_a", started_at=started))
    traces.append(_trace("nvcl_a", started_at=started, outcome="failure", user_corrected=True))
    traces.append(_trace("nvcl_b", started_at=started, skill={"name": "brief",
                                                              "version": "0.2.0"}))
    signal = _signal("nvcl_a")
    signal["at"] = started
    PostgresRecipeStore(db).append_signal(signal)
    return db


def test_report_needs_a_database(capsys):
    assert main(["report"]) == 2
    assert "NUVEL_MCP_DATABASE_URL is not set" in capsys.readouterr().err


def test_report_rejects_a_silly_window(db_env, capsys):
    assert main(["report", "--days", "0"]) == 2
    assert "--days" in capsys.readouterr().err


def test_report_json(db_env, capsys):
    seed(db_env)
    assert main(["clients", "label", "nvcl_a", "--person", "Ana", "--company", "Acme"]) == 0
    capsys.readouterr()
    assert main(["report", "--days", "7", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)

    assert report["window"]["days"] == 7
    assert report["totals"] == {"calls": 8, "errors": 2, "error_rate": 0.25,
                                "active_clients": 2}
    clients = {c["client_id"]: c for c in report["active_clients"]}
    assert set(clients) == {"nvcl_a", "nvcl_b", None}  # None: unauthenticated / stdio
    assert (clients["nvcl_a"]["person"], clients["nvcl_a"]["company"],
            clients["nvcl_a"]["calls"], clients["nvcl_a"]["errors"]) == ("Ana", "Acme", 5, 1)
    assert sum(d["calls"] for d in report["calls_per_day"]) == 8

    assert report["methods"][0] == {"method": "tools/call", "calls": 5, "errors": 2}
    tools = {t["tool"]: t for t in report["tools"]}
    search = tools["search_skills"]
    assert (search["calls"], search["errors"], search["error_rate"]) == (4, 1, 0.25)
    assert search["p50_ms"] == 15.0 and search["top_errors"] == [{"error": "-32602", "count": 1}]
    assert tools["get_skill"]["top_errors"] == [{"error": "tool_error", "count": 1}]

    apps = {(a["client_name"], a["client_version"]): a for a in report["client_apps"]}
    assert apps[("claude-ai", "0.1.0")]["calls"] == 5 and apps[("Cursor", "1.0")]["clients"] == 1
    assert {c["country"]: c["calls"] for c in report["countries"]} == {"DE": 4, "ES": 2}

    runs = {r["skill"]: r for r in report["skill_runs"]}
    assert (runs["radar"]["runs"], runs["radar"]["success"], runs["radar"]["failure"],
            runs["radar"]["corrected"], runs["radar"]["correction_rate"]) == (2, 1, 1, 1, 0.5)
    assert runs["brief"]["runs"] == 1

    never = set(report["tools_never_called"])
    assert never == (HUB_TOOLS | FEEDBACK_TOOLS) - {"search_skills", "get_skill"}
    assert "list_themes" in never  # only called before the window

    (recipe,) = report["recipes"]
    assert (recipe["category"], recipe["recipes"], recipe["installs"], recipe["signals"]) == (
        "client-followup", 1, 1, 1)


def test_report_text(db_env, capsys):
    seed(db_env)
    assert main(["report"]) == 0
    out = capsys.readouterr().out
    for heading in ("Usage report", "Active clients", "Calls per day", "Methods",
                    "Tools", "Client apps", "Countries", "Skill runs",
                    "Tools never called", "Recipe adoption"):
        assert heading in out, heading
    assert "search_skills" in out and "claude-ai" in out and "DE" in out


def test_report_on_an_empty_database(db_env, capsys):
    assert main(["report", "--days", "30"]) == 0
    out = capsys.readouterr().out
    assert "No usage events" in out
    assert main(["report", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["totals"]["calls"] == 0


EVIL = "evil\x1b]0;pwn\x07\x1b[2J\x9b31m\x7f\u202e\u200b"


def _controls(text):
    # C0, DEL, C1, and Unicode format characters (bidi overrides, zero-width).
    return [ch for ch in text if ch != "\n" and (ord(ch) < 32 or 0x7f <= ord(ch) <= 0x9f
                                                 or unicodedata.category(ch) == "Cf")]


def test_text_output_never_carries_control_characters(db_env, capsys):
    # A client_name from dynamic client registration (or any database string)
    # must not reach the operator's terminal as ANSI / OSC escape sequences.
    db = seed(db_env)
    with db.connection() as conn:
        conn.execute(
            f"INSERT INTO {db.table('oauth_clients')} (id, client_id, record) "
            "VALUES ('nvcl_a', 'nvcl_a', %s)",
            (db.jsonb({"client_id": "nvcl_a", "client_name": EVIL}),))
        conn.execute(f"INSERT INTO {db.table('client_profiles')} (client_id, company) "
                     "VALUES ('nvcl_b', %s)", (EVIL,))
        conn.execute(f"UPDATE {db.table('usage_events')} SET client_version = %s "
                     "WHERE tenant = 'nvcl_b'", (EVIL,))
    for argv in (["clients", "list"], ["report"]):
        assert main(argv) == 0
        out = capsys.readouterr().out
        assert "evil]0;pwn" in out, argv  # the text stays, the controls go
        assert _controls(out) == [], argv
    assert main(["clients", "list", "--json"]) == 0
    names = [r["name"] for r in json.loads(capsys.readouterr().out)]
    assert EVIL in names  # JSON output is escaped by json.dumps, left as stored
