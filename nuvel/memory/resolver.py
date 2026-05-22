"""ScopeResolver protocol + config-driven implementation."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Protocol

import yaml

from nuvel.memory.scope import Scope, ScopeChain

log = logging.getLogger(__name__)


class ScopeResolver(Protocol):
    org_id: str

    def resolve(self, user_id: str) -> ScopeChain: ...


class ConfigScopeResolver:
    def __init__(self, *, org_id: str, levels: list[str], users: dict[str, list[Scope]]) -> None:
        self.org_id = org_id
        self.levels = levels
        self._users = users

    @classmethod
    def from_yaml(cls, path: str | Path) -> "ConfigScopeResolver":
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        users = {
            uid: [Scope(**s) for s in body["chain"]]
            for uid, body in (data.get("users") or {}).items()
        }
        return cls(
            org_id=data["org_id"],
            levels=list(data.get("levels") or []),
            users=users,
        )

    def resolve(self, user_id: str) -> ScopeChain:
        scopes = self._users.get(user_id)
        if scopes is None:
            log.warning("unknown user %r — falling back to user-leaf-only scope", user_id)
            return ScopeChain(scopes=[Scope(level="user", id=user_id)])
        return ScopeChain(scopes=list(scopes))
