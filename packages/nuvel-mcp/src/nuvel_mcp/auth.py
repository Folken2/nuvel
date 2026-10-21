"""Tenant context for the HTTP transport.

``current_principal`` holds the tenant of the caller handling the current tool
call; ``None`` means an unauthenticated local session (stdio, or HTTP with
``--no-auth``). :mod:`nuvel_mcp.http_transport` sets it to the OAuth token's
``client_id`` for the duration of a request; ``record_run_trace`` stamps
``tenant`` with it and ``check_skill_health`` scopes by it. Authentication
itself lives in :mod:`nuvel_mcp.oauth`.
"""

from __future__ import annotations

import contextvars

current_principal: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "nuvel_mcp_current_principal", default=None
)
