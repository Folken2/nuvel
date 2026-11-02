from nuvel_mcp.clientip import client_ip, trust_forwarded_for


def test_peer_when_not_trusted():
    assert client_ip({"X-Forwarded-For": "203.0.113.9"}, "10.0.0.1", False) == "10.0.0.1"


def test_rightmost_valid_forwarded_entry_when_trusted():
    # The client can prepend anything; only the proxy's own append is trusted.
    headers = {"X-Forwarded-For": "1.2.3.4, 203.0.113.9"}
    assert client_ip(headers, "10.0.0.1", True) == "203.0.113.9"
    assert client_ip({"X-Forwarded-For": "203.0.113.9, junk"}, "10.0.0.1", True) == "203.0.113.9"
    assert client_ip({"X-Forwarded-For": "2001:db8::1"}, "10.0.0.1", True) == "2001:db8::1"


def test_peer_when_trusted_but_header_missing_or_garbage():
    assert client_ip({}, "10.0.0.1", True) == "10.0.0.1"
    assert client_ip({"X-Forwarded-For": "nope"}, "10.0.0.1", True) == "10.0.0.1"


def test_trust_flag():
    assert trust_forwarded_for({"NUVEL_MCP_TRUST_FORWARDED_FOR": "1"})
    assert not trust_forwarded_for({})
