"""Hierarchical, scope-aware memory for nuvel agents."""

from nuvel.memory.embedder import Embedder, GoogleEmbedder, NullEmbedder
from nuvel.memory.resolver import ConfigScopeResolver, ScopeResolver
from nuvel.memory.scope import Scope, ScopeChain
from nuvel.memory.store import MemoryRow, MemoryStore, ScopeAuthorizationError

__all__ = [
    "ConfigScopeResolver",
    "Embedder",
    "GoogleEmbedder",
    "MemoryRow",
    "MemoryStore",
    "NullEmbedder",
    "Scope",
    "ScopeAuthorizationError",
    "ScopeChain",
    "ScopeResolver",
]
