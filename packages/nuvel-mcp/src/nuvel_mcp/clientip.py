"""The caller's IP address, behind a reverse proxy or not.

Behind Railway's edge proxy every TCP peer is the proxy, so per-IP limits and
country lookups need the address the proxy saw. ``X-Forwarded-For`` is only
trusted when the operator says the server sits behind such a proxy
(``NUVEL_MCP_TRUST_FORWARDED_FOR=1``); otherwise a caller could forge it.
"""

from __future__ import annotations

import ipaddress
import os

TRUST_ENV = "NUVEL_MCP_TRUST_FORWARDED_FOR"


def trust_forwarded_for(environ=None) -> bool:
    value = (environ if environ is not None else os.environ).get(TRUST_ENV, "")
    return value.strip().lower() in ("1", "true", "yes", "on")


def client_ip(headers, peer: str, trust_forwarded: bool) -> str:
    """The caller's address: the right-most valid ``X-Forwarded-For`` entry when
    trusted (the one our own proxy appended), else the TCP *peer*.

    Every ``X-Forwarded-For`` line counts, in order: a caller can send its own
    line and a proxy may append a separate one, so reading only the first
    line would let the forged one win."""
    if trust_forwarded:
        get_all = getattr(headers, "get_all", None)
        lines = get_all("X-Forwarded-For") if get_all is not None else None
        if lines is None:  # a plain dict, or no such header
            lines = [headers.get("X-Forwarded-For") or ""]
        raw = ",".join(lines)
        for part in reversed(raw.split(",")):
            candidate = part.strip()
            try:
                return str(ipaddress.ip_address(candidate))
            except ValueError:
                continue
    return peer
