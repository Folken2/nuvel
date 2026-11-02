"""``nuvel-mcp clients label|list``: operator-entered client profiles.

Tests using ``db_env`` need ``NUVEL_MCP_TEST_DATABASE_URL``.
"""

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

import _pg
from _mcp_http_helpers import isolate_env
from nuvel_mcp.cli import main


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch, tmp_path):
    isolate_env(monkeypatch, tmp_path)


@pytest.fixture
def db_env(monkeypatch):
    if not _pg.DSN:
        pytest.skip("NUVEL_MCP_TEST_DATABASE_URL is not set")
    import psycopg

    schema = f"p_{uuid.uuid4().hex[:12]}"
    monkeypatch.setenv("NUVEL_MCP_DATABASE_URL", _pg.DSN)
    monkeypatch.setenv("NUVEL_MCP_DATABASE_SCHEMA", schema)
    yield schema
    with psycopg.connect(_pg.DSN, autocommit=True) as conn:
        conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')


def _db(schema):
    return _pg.open_db(schema)


def _events(db, rows):
    from nuvel_mcp.db.stores import PostgresUsageStore

    PostgresUsageStore(db).record_batch([
        {"at": at, "tenant": tenant, "transport": "http", "method": "tools/list",
         "tool": None, "outcome": "ok", "error_code": None, "duration_ms": 5}
        for tenant, at in rows])


@pytest.mark.parametrize("argv", [["clients", "list"], ["clients", "label", "x", "--person", "A"]])
def test_insight_commands_need_a_database(argv, capsys):
    assert main(argv) == 2
    assert "NUVEL_MCP_DATABASE_URL is not set" in capsys.readouterr().err


def test_label_then_update_then_clear(db_env, capsys):
    assert main(["clients", "label", "nvcl_abc", "--person", "Ana García",
                 "--company", "Acme SL", "--notes", "pilot, sales team"]) == 0
    assert "Labeled nvcl_abc" in capsys.readouterr().out
    assert main(["clients", "label", "nvcl_abc", "--company", "Acme Group"]) == 0
    capsys.readouterr()
    assert main(["clients", "list", "--json"]) == 0
    (row,) = json.loads(capsys.readouterr().out)
    assert (row["client_id"], row["person"], row["company"], row["notes"]) == (
        "nvcl_abc", "Ana García", "Acme Group", "pilot, sales team")
    assert row["events_30d"] == 0 and row["last_seen"] is None

    assert main(["clients", "label", "nvcl_abc", "--notes", ""]) == 0  # "" clears a field
    capsys.readouterr()
    assert main(["clients", "list", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["notes"] is None


def test_label_needs_a_field_and_bounds_its_input(db_env, capsys):
    assert main(["clients", "label", "nvcl_abc"]) == 2
    assert "--person, --company or --notes" in capsys.readouterr().err
    assert main(["clients", "label", "nvcl_abc", "--person", "x" * 201]) == 2
    assert "--person" in capsys.readouterr().err
    assert main(["clients", "label", "bad id\n", "--person", "A"]) == 2
    assert "client_id" in capsys.readouterr().err


def test_label_of_an_unknown_client_warns(db_env, capsys):
    assert main(["clients", "label", "nvcl_typo", "--person", "A"]) == 0
    assert "no OAuth client and no usage" in capsys.readouterr().err


def test_list_joins_profiles_clients_and_usage(db_env, capsys):
    db = _db(db_env)
    now = datetime.now(timezone.utc)
    _events(db, [("nvcl_a", now - timedelta(days=1)), ("nvcl_a", now - timedelta(hours=1)),
                 ("nvcl_a", now - timedelta(days=40)), ("nvcl_b", now - timedelta(days=2)),
                 (None, now)])
    assert main(["client", "create", "--name", "never-used", "--url-key"]) == 0
    unused = json.loads(capsys.readouterr().out)["client_id"]
    assert main(["clients", "label", "nvcl_a", "--person", "Ana", "--company", "Acme"]) == 0
    capsys.readouterr()

    assert main(["clients", "list", "--json"]) == 0
    rows = {r["client_id"]: r for r in json.loads(capsys.readouterr().out)}
    assert set(rows) == {"nvcl_a", "nvcl_b", unused}
    assert (rows["nvcl_a"]["person"], rows["nvcl_a"]["events_30d"]) == ("Ana", 2)
    assert rows["nvcl_b"]["events_30d"] == 1 and rows["nvcl_b"]["person"] is None
    assert rows[unused]["events_30d"] == 0 and rows[unused]["last_seen"] is None
    assert rows[unused]["name"] == "never-used"

    assert main(["clients", "list"]) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0].split()[:5] == ["CLIENT_ID", "NAME", "PERSON", "COMPANY", "LAST_SEEN"]
    assert "Acme" in out and "nvcl_b" in out


def test_db_status_counts_profiles(db_env, capsys):
    assert main(["clients", "label", "nvcl_abc", "--person", "A"]) == 0
    capsys.readouterr()
    assert main(["db", "status", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["rows"]["client_profiles"] == 1


def test_create_labels_the_new_client(db_env, capsys):
    assert main(["client", "create", "--name", "Oriol (System Action)", "--url-key",
                 "--person", "Oriol Mayor", "--company", "System Action"]) == 0
    client_id = json.loads(capsys.readouterr().out)["client_id"]
    assert main(["clients", "list", "--json"]) == 0
    (row,) = json.loads(capsys.readouterr().out)
    assert (row["client_id"], row["person"], row["company"]) == (
        client_id, "Oriol Mayor", "System Action")


def test_create_with_a_label_needs_a_database_and_creates_nothing(capsys):
    assert main(["client", "create", "--name", "x", "--url-key", "--company", "Acme"]) == 2
    assert "NUVEL_MCP_DATABASE_URL" in capsys.readouterr().err
    assert main(["client", "list"]) == 0
    assert "No clients registered" in capsys.readouterr().out


def test_create_rejects_a_bad_label_before_creating(db_env, capsys):
    assert main(["client", "create", "--name", "x", "--company", "x" * 201]) == 2
    assert "--company" in capsys.readouterr().err
    assert main(["clients", "list", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == []
