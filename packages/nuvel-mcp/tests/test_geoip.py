"""Country, never IP: the optional DB-IP country table and its wiring."""

import gzip
import json
import threading

import pytest

from _mcp_http_helpers import INIT, TIMEOUT, Client, call, isolate_env, make_hub
from nuvel_mcp.geoip import CountryTable, country_lookup_from_env
from nuvel_mcp.http_transport import HttpConfig, make_server
from nuvel_mcp.server import SkillsMCPServer
from nuvel_mcp.skills_loader import SkillsLoader

# The DB-IP "IP to Country Lite" shape: start_ip,end_ip,country_code.
CSV = """\
1.0.0.0,1.0.0.255,AU
1.0.1.0,1.0.3.255,CN
"5.0.0.0","5.255.255.255","DE"
10.0.0.0,10.255.255.255,ZZ
127.0.0.0,127.255.255.255,ES
2001:200::,2001:200:ffff:ffff:ffff:ffff:ffff:ffff,JP
2a00::,2a00:ffff:ffff:ffff:ffff:ffff:ffff:ffff,FR
not-an-ip,1.2.3.4,US
9.9.9.9,9.9.9.0,US
8.8.8.0,8.8.8.255,usa
"""


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch, tmp_path):
    isolate_env(monkeypatch, tmp_path)
    for name in ("NUVEL_MCP_GEOIP_CSV", "NUVEL_MCP_RECORD_COUNTRY",
                 "NUVEL_MCP_TRUST_FORWARDED_FOR"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def csv_path(tmp_path):
    path = tmp_path / "dbip-country-lite.csv"
    path.write_text(CSV)
    return path


@pytest.mark.parametrize("ip, country", [
    ("1.0.0.0", "AU"), ("1.0.0.255", "AU"), ("1.0.2.7", "CN"), ("1.0.4.0", None),
    ("5.6.7.8", "DE"), ("10.1.2.3", None),  # ZZ (unknown) is not a country
    ("127.0.0.1", "ES"), ("::ffff:5.1.1.1", "DE"),  # IPv4-mapped IPv6
    ("2001:200::1", "JP"), ("2a00:1450::1", "FR"), ("2a01::1", None),
    ("0.0.0.0", None), ("255.255.255.255", None), ("garbage", None), ("", None),
])
def test_lookup(csv_path, ip, country):
    assert CountryTable.load(csv_path).lookup(ip) == country


def test_bad_rows_are_skipped_and_counted(csv_path):
    table = CountryTable.load(csv_path)
    assert table.skipped == 3  # not-an-ip, start > end, 'usa'
    assert len(table) == 6  # ZZ is dropped, the rest load (1.0.0.0/1.0.1.0 are not merged)


def test_adjacent_ranges_of_one_country_merge(tmp_path):
    path = tmp_path / "c.csv"
    path.write_text("1.0.0.0,1.0.0.255,AU\n1.0.1.0,1.0.1.255,AU\n1.0.2.0,1.0.2.255,NZ\n")
    table = CountryTable.load(path)
    assert len(table) == 2 and table.lookup("1.0.1.9") == "AU"


def test_gzipped_csv_loads(tmp_path):
    path = tmp_path / "dbip-country-lite.csv.gz"
    path.write_bytes(gzip.compress(CSV.encode()))
    assert CountryTable.load(path).lookup("5.6.7.8") == "DE"


def test_a_file_with_no_valid_row_is_an_error(tmp_path):
    path = tmp_path / "empty.csv"
    path.write_text("hello,world\n")
    with pytest.raises(ValueError, match="no valid"):
        CountryTable.load(path)


def test_from_env(csv_path, tmp_path):
    assert country_lookup_from_env({}) is None
    table = country_lookup_from_env({"NUVEL_MCP_GEOIP_CSV": str(csv_path)})
    assert table("5.1.1.1") == "DE"
    for off in ("0", "false", "off", "no"):
        assert country_lookup_from_env({"NUVEL_MCP_GEOIP_CSV": str(csv_path),
                                        "NUVEL_MCP_RECORD_COUNTRY": off}) is None
    with pytest.raises(ValueError, match="NUVEL_MCP_GEOIP_CSV"):
        country_lookup_from_env({"NUVEL_MCP_GEOIP_CSV": str(tmp_path / "missing.csv")})
    with pytest.raises(ValueError, match="NUVEL_MCP_RECORD_COUNTRY"):
        country_lookup_from_env({"NUVEL_MCP_GEOIP_CSV": str(csv_path),
                                 "NUVEL_MCP_RECORD_COUNTRY": "maybe"})


# --- over HTTP: the country lands on the event, the address never does ---------------


class _Recorder:
    def __init__(self):
        self.events = []

    def record(self, event):
        self.events.append(event)


@pytest.fixture
def serve():
    started = []

    def factory(mcp):
        httpd = make_server(mcp, "127.0.0.1", 0, HttpConfig(ping_interval=0.2), None,
                            no_auth=True)
        thread = threading.Thread(target=httpd.serve_forever, args=(0.05,), daemon=True)
        thread.start()
        started.append((httpd, thread))
        return httpd

    yield factory
    for httpd, thread in started:
        httpd.stop()
        httpd.server_close()
        thread.join(TIMEOUT)


def _session_events(serve, tmp_path, csv_path, headers=None, with_usage=True):
    usage = _Recorder() if with_usage else None
    mcp = SkillsMCPServer(SkillsLoader.from_base(make_hub(tmp_path)), usage=usage)
    mcp.country_lookup = CountryTable.load(csv_path)
    client = Client(serve(mcp))
    _, h, _ = client.post(INIT, headers=headers)
    client.post(call("list_themes", {}), h["Mcp-Session-Id"], headers=headers)
    return usage.events if usage else []


def test_country_from_the_peer(serve, tmp_path, csv_path):
    events = _session_events(serve, tmp_path, csv_path)
    assert [e["country"] for e in events] == ["ES", "ES"]  # 127.0.0.0/8 is ES above
    assert "127.0.0.1" not in json.dumps(events, default=str)


def test_forwarded_for_only_when_trusted(serve, tmp_path, csv_path, monkeypatch):
    xff = {"X-Forwarded-For": "2001:200::7, 5.4.3.2"}
    assert {e["country"] for e in _session_events(serve, tmp_path / "a", csv_path, xff)} == {"ES"}
    monkeypatch.setenv("NUVEL_MCP_TRUST_FORWARDED_FOR", "1")
    events = _session_events(serve, tmp_path / "b", csv_path, xff)
    assert {e["country"] for e in events} == {"DE"}  # the right-most entry
    dumped = json.dumps(events, default=str)
    assert "5.4.3.2" not in dumped and "2001:200" not in dumped


# --- the CLI ------------------------------------------------------------------------


def test_serve_without_a_database_never_loads_the_table(monkeypatch, csv_path, capsys):
    from nuvel_mcp.cli import main

    seen = {}
    monkeypatch.setattr("nuvel_mcp.server.SkillsMCPServer.serve",
                        lambda self: seen.update(server=self))
    monkeypatch.setenv("NUVEL_MCP_GEOIP_CSV", str(csv_path))
    assert main(["serve"]) == 0
    assert seen["server"].country_lookup is None
    assert "NUVEL_MCP_GEOIP_CSV is ignored" in capsys.readouterr().err


def test_serve_with_a_database_loads_the_table(monkeypatch, csv_path, capsys, db_env):
    from nuvel_mcp.cli import main

    seen = {}
    monkeypatch.setattr("nuvel_mcp.server.SkillsMCPServer.serve",
                        lambda self: seen.update(server=self))
    monkeypatch.setenv("NUVEL_MCP_GEOIP_CSV", str(csv_path))
    assert main(["serve"]) == 0
    assert seen["server"].country_lookup("5.1.1.1") == "DE"
    assert "Country: on (6 ranges" in capsys.readouterr().err

    monkeypatch.setenv("NUVEL_MCP_RECORD_COUNTRY", "0")
    assert main(["serve"]) == 0
    assert seen["server"].country_lookup is None

    monkeypatch.setenv("NUVEL_MCP_RECORD_COUNTRY", "1")
    monkeypatch.setenv("NUVEL_MCP_GEOIP_CSV", str(csv_path) + ".missing")
    assert main(["serve"]) == 2
    assert "NUVEL_MCP_GEOIP_CSV" in capsys.readouterr().err


@pytest.fixture
def db_env(monkeypatch):
    import uuid

    import _pg

    if not _pg.DSN:
        pytest.skip("NUVEL_MCP_TEST_DATABASE_URL is not set")
    import psycopg

    schema = f"g_{uuid.uuid4().hex[:12]}"
    monkeypatch.setenv("NUVEL_MCP_DATABASE_URL", _pg.DSN)
    monkeypatch.setenv("NUVEL_MCP_DATABASE_SCHEMA", schema)
    yield schema
    with psycopg.connect(_pg.DSN, autocommit=True) as conn:
        conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
