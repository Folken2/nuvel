"""``exfil_guard`` — a ``before_tool_callback`` that stops secrets leaving via tools.

Before a tool runs, its arguments are scanned for high-confidence secret
patterns (cloud keys, private-key blocks, provider tokens). This catches the
common data-exfiltration shape where the model pastes a credential it read from
the environment into an outbound call (an HTTP request, a message send).

Behaviour is controlled by ``EXFIL_GUARD_STRICT`` (default on):

* strict (``1``/``true``/``yes``) — block the call and return an error result
  so the tool never runs.
* lax — allow the call through but stamp ``exfil_warning`` into tool_context
  state so a downstream logger/plugin can surface it.
"""

from __future__ import annotations

import os
import re
from typing import Any

# High-signal secret shapes. Kept deliberately narrow to avoid false positives
# on ordinary arguments; each pattern targets a credential with a fixed prefix
# or an unmistakable envelope.
_SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("aws-access-key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("google-api-key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("github-token", re.compile(r"\bgh[pousr]_[0-9A-Za-z]{36,}\b")),
    ("slack-token", re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{10,}\b")),
    ("openai-key", re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}\b")),
    ("anthropic-key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}\b")),
    ("stripe-key", re.compile(r"\b[rs]k_(?:live|test)_[0-9A-Za-z]{20,}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b")),
    ("private-key-block", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----")),
]


def _strict_mode() -> bool:
    return os.getenv("EXFIL_GUARD_STRICT", "1").strip().lower() in ("1", "true", "yes")


def _scan(value: Any) -> str | None:
    """Return the name of the first secret pattern found anywhere in ``value``."""
    if isinstance(value, str):
        for name, pattern in _SECRET_PATTERNS:
            if pattern.search(value):
                return name
        return None
    if isinstance(value, dict):
        for item in value.values():
            hit = _scan(item)
            if hit:
                return hit
        return None
    if isinstance(value, (list, tuple, set)):
        for item in value:
            hit = _scan(item)
            if hit:
                return hit
        return None
    return None


def exfil_guard(tool: Any, args: Any, tool_context: Any) -> dict | None:
    """Block (strict) or flag (lax) a tool call whose args carry a secret.

    Registered as a ``before_tool_callback``. Returning a dict short-circuits
    the tool and hands that dict back to the model as the tool result;
    returning ``None`` lets the call proceed.
    """
    hit = _scan(args)
    if not hit:
        return None

    tool_name = getattr(tool, "name", "") or "tool"
    message = (
        f"blocked: the arguments to {tool_name} contain what looks like a "
        f"{hit} secret. Refusing to send credentials to a tool."
    )
    if _strict_mode():
        return {"error": message, "blocked_by": "exfil_guard", "pattern": hit}

    # Lax mode: let it through but leave a breadcrumb for observers.
    try:
        tool_context.state["exfil_warning"] = message
    except Exception:  # tool_context without a writable state — don't crash the call
        pass
    return None


__all__ = ["exfil_guard"]
