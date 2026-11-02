"""Abuse limits and hardening on the HTTP transport and the OAuth server.

Per-principal session and stream caps, the per-IP connection cap (behind a
trusted proxy too), URL-key expiry, client authentication before the /token
write lock, security headers, per-subject tenants for interactive logins, the
pending-login caps and the GitHub numeric-id allow-list.

Real servers on 127.0.0.1:0, driven with http.client -- no HTTP mocks.
"""

from __future__ import annotations

import json
import time

import pytest

from nuvel_mcp.http_transport import HttpConfig
from _mcp_http_helpers import (
    INIT,
    TIMEOUT,
    Client,
    call,
    isolate_env,
    make_hub,
    make_provider,
    read_frame,
    service_token,
    start,
)


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch, tmp_path):
    isolate_env(monkeypatch, tmp_path)
    for name in ("NUVEL_MCP_MAX_SESSIONS_PER_PRINCIPAL", "NUVEL_MCP_MAX_STREAMS_PER_PRINCIPAL",
                 "NUVEL_MCP_MAX_CONNECTIONS_PER_IP", "NUVEL_MCP_TRUST_FORWARDED_FOR"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def hub(tmp_path):
    return make_hub(tmp_path)


@pytest.fixture
def store_path(tmp_path):
    return tmp_path / "oauth" / "oauth.json"


@pytest.fixture
def launch(hub, store_path):
    started = []

    def factory(provider=None, **config):
        httpd, thread = start(hub, provider or make_provider(store_path), **config)
        started.append((httpd, thread))
        return httpd

    yield factory
    for httpd, thread in started:
        httpd.stop()
        httpd.server_close()
        thread.join(TIMEOUT)


def authed(httpd):
    return Client(httpd, service_token(httpd))


def eventually(probe, timeout=TIMEOUT):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if probe():
            return True
        time.sleep(0.05)
    return False


# --- (1) per-principal session and stream caps ---------------------------------------


def test_per_principal_session_cap_is_429_and_other_principals_are_unaffected(launch):
    httpd = launch(max_sessions_per_principal=2)
    alice = authed(httpd)
    alice.initialize()
    alice.initialize()
    status, headers, data = alice.post(INIT)
    assert status == 429
    assert int(headers["Retry-After"]) >= 1
    assert "Mcp-Session-Id" not in headers and b"Mcp-Session-Id" not in data
    assert json.loads(data)["error"]["code"] == -32003
    bob = authed(httpd)  # another client: its own allowance
    bob.initialize()
    # Ending a session frees a slot for its principal.
    httpd.sessions.close_all()
    alice.initialize()


def test_global_session_cap_still_applies_across_principals(launch):
    httpd = launch(max_sessions=2, max_sessions_per_principal=8)
    authed(httpd).initialize()
    authed(httpd).initialize()
    assert authed(httpd).post(INIT)[0] == 503


def test_per_principal_stream_cap_covers_both_transports(launch):
    httpd = launch(max_streams_per_principal=1)
    alice = authed(httpd)
    session = alice.initialize()
    first, resp = alice.open_stream("/sse")
    try:
        assert resp.status == 200
        read_frame(resp)  # endpoint event: the stream holds alice's one slot
        second, resp2 = alice.open_stream("/sse")
        try:
            assert resp2.status == 429 and int(resp2.getheader("Retry-After")) >= 1
        finally:
            second.close()
        third, resp3 = alice.open_stream("/mcp", {"Mcp-Session-Id": session})
        try:
            assert resp3.status == 429
        finally:
            third.close()
        # Another principal still gets a stream.
        bob = authed(httpd)
        other, resp4 = bob.open_stream("/sse")
        try:
            assert resp4.status == 200
        finally:
            other.close()
        # POSTs are not streams: alice can still call tools.
        assert alice.post(call("list_themes", {}), session)[0] == 200
    finally:
        first.close()

    def reopened():
        stream, again = alice.open_stream("/mcp", {"Mcp-Session-Id": session})
        stream.close()
        return again.status == 200

    assert eventually(reopened)


def test_per_principal_caps_from_env(monkeypatch):
    monkeypatch.setenv("NUVEL_MCP_MAX_SESSIONS_PER_PRINCIPAL", "3")
    monkeypatch.setenv("NUVEL_MCP_MAX_STREAMS_PER_PRINCIPAL", "2")
    cfg = HttpConfig.from_env()
    assert (cfg.max_sessions_per_principal, cfg.max_streams_per_principal) == (3, 2)
    assert (HttpConfig().max_sessions_per_principal, HttpConfig().max_streams_per_principal) \
        == (8, 4)


@pytest.mark.parametrize("name", ["NUVEL_MCP_MAX_SESSIONS_PER_PRINCIPAL",
                                  "NUVEL_MCP_MAX_STREAMS_PER_PRINCIPAL"])
def test_per_principal_caps_must_be_positive(monkeypatch, name):
    monkeypatch.setenv(name, "0")
    with pytest.raises(ValueError, match=name):
        HttpConfig.from_env()
