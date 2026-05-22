"""Backend-agnostic MemoryStore protocol and shared types."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from nuvel.memory.scope import Scope


class ScopeAuthorizationError(RuntimeError):
    """Raised when a write targets a scope outside the caller's chain."""


@dataclass
class MemoryRow:
    id: str | None
    org_id: str
    scope_level: str
    scope_id: str
    scope_chain: list[str]
    content: str
    embedding: list[float] | None
    source_app: str | None = None
    source_session: str | None = None
    custom_metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime | None = None
    score: float | None = None  # populated by search


class MemoryStore(Protocol):
    async def insert(self, row: MemoryRow) -> str: ...

    async def search(
        self,
        *,
        org_id: str,
        user_chain_tags: list[str],
        q_embedding: list[float] | None,
        query_text: str,
        k: int,
        tier_boost: dict[str, float],
    ) -> list[MemoryRow]: ...

    async def move(self, memory_id: str, new_scope: Scope, new_chain: list[str]) -> None: ...

    async def delete(self, memory_id: str) -> None: ...

    async def list_by_scope(self, *, org_id: str, scope: Scope, limit: int = 100) -> list[MemoryRow]: ...
