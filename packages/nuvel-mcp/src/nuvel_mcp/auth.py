"""Authentication for the HTTP transport.

``current_principal`` holds the tenant of the caller handling the current tool
call; ``None`` means an unauthenticated local (stdio) session. Transports set
it; ``record_run_trace`` reads it to stamp ``tenant``.

An :class:`Authenticator` maps request headers to a principal name (or
``None`` to reject). :class:`BearerAuthenticator` is the built-in one, fed by
``$NUVEL_MCP_TOKENS`` (``name:token,name:token``); the principal is the name,
never the token.
"""

from __future__ import annotations

import contextvars
import hmac
import importlib
import os
from collections.abc import Mapping
from typing import Protocol

current_principal: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "nuvel_mcp_current_principal", default=None
)

TOKENS_ENV = "NUVEL_MCP_TOKENS"
# Host names that only reach this machine. The HTTP transport may run without
# an authenticator only when bound to one of these.
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


class Authenticator(Protocol):
    def authenticate(self, headers: Mapping[str, str]) -> str | None:
        """Return the principal name for these request headers, or None to reject."""
        ...


class BearerAuthenticator:
    """Static bearer tokens: ``Authorization: Bearer <token>`` -> token's name."""

    def __init__(self, tokens: Mapping[str, str]) -> None:
        # name -> token; stored as bytes so compare_digest accepts any text.
        self._tokens = [(name, token.encode("utf-8")) for name, token in tokens.items()]

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> BearerAuthenticator:
        raw = env.get(TOKENS_ENV, "")
        if not raw.strip():
            raise ValueError(f"bearer auth requires {TOKENS_ENV} (name:token,name:token)")
        tokens: dict[str, str] = {}
        for index, entry in enumerate(raw.split(","), start=1):
            name, sep, token = entry.partition(":")
            name, token = name.strip(), token.strip()
            if not sep or not name or not token:
                raise ValueError(f"{TOKENS_ENV} entry {index} must be 'name:token'")
            if name in tokens:
                raise ValueError(f"{TOKENS_ENV} entry {index} repeats an earlier name")
            tokens[name] = token
        return cls(tokens)

    def authenticate(self, headers: Mapping[str, str]) -> str | None:
        value = next((v for k, v in headers.items() if k.lower() == "authorization"), None)
        if value is None:
            return None
        scheme, sep, presented = value.partition(" ")
        if not sep or scheme.lower() != "bearer" or not presented:
            return None
        candidate = presented.encode("utf-8")
        principal: str | None = None
        for name, token in self._tokens:  # check every token: no early exit
            if hmac.compare_digest(candidate, token) and principal is None:
                principal = name
        return principal

    def __repr__(self) -> str:
        return f"BearerAuthenticator(tokens={len(self._tokens)})"


def load_authenticator(spec: str | None) -> Authenticator | None:
    """Build the authenticator named by *spec*.

    ``None`` means no authentication; ``"bearer"`` is
    :meth:`BearerAuthenticator.from_env`; ``"module:factory"`` imports
    ``module`` and calls ``factory()`` with no arguments; the result must have
    a callable ``authenticate``. Raises ``ValueError`` for anything else, so a
    factory returning ``None`` never silently disables authentication.
    """
    if spec is None:
        return None
    if spec == "bearer":
        return BearerAuthenticator.from_env()
    module_name, sep, attr = spec.partition(":")
    if not sep or not module_name or not attr:
        raise ValueError("authenticator must be 'bearer' or 'module:factory'")
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise ValueError(f"cannot import authenticator module {module_name!r}: {exc}") from exc
    factory = getattr(module, attr, None)
    if not callable(factory):
        raise ValueError(f"authenticator factory {spec!r} not found or not callable")
    authenticator = factory()
    if not callable(getattr(authenticator, "authenticate", None)):
        raise ValueError(
            f"authenticator factory {spec!r} must return an object with an authenticate() method"
        )
    return authenticator
