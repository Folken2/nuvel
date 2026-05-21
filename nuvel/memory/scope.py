"""Scope value objects: a single scope and an ordered leaf→root chain."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class Scope(BaseModel):
    model_config = ConfigDict(frozen=True)

    level: str
    id: str

    def tag(self) -> str:
        return f"{self.level}:{self.id}"


class ScopeChain(BaseModel):
    scopes: list[Scope]

    def tags(self) -> list[str]:
        return [s.tag() for s in self.scopes]

    def contains(self, scope: Scope) -> bool:
        return scope in self.scopes
