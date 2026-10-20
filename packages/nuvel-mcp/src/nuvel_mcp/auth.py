"""Authenticated principal for the current request.

``current_principal`` holds the tenant of the caller handling the current tool
call; ``None`` means an unauthenticated local (stdio) session. Transports set
it; ``record_run_trace`` reads it to stamp ``tenant``.
"""

from __future__ import annotations

import contextvars

current_principal: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "nuvel_mcp_current_principal", default=None
)
