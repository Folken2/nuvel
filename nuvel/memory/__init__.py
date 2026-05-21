"""Hierarchical, scope-aware memory for nuvel agents."""

from nuvel.memory.admin import OrgMemoryAdmin
from nuvel.memory.adk_registry import ORG_MEMORY_SCHEME, register_org_memory_scheme
from nuvel.memory.embedder import Embedder, GoogleEmbedder, NullEmbedder
from nuvel.memory.factory import build_default_service
from nuvel.memory.resolver import ConfigScopeResolver, ScopeResolver
from nuvel.memory.scope import Scope, ScopeChain
from nuvel.memory.store import MemoryRow, MemoryStore, ScopeAuthorizationError

__all__ = [
    "build_default_service",
    "ConfigScopeResolver",
    "Embedder",
    "GoogleEmbedder",
    "MemoryRow",
    "MemoryStore",
    "NullEmbedder",
    "OrgMemoryAdmin",
    "ORG_MEMORY_SCHEME",
    "register_org_memory_scheme",
    "Scope",
    "ScopeAuthorizationError",
    "ScopeChain",
    "ScopeResolver",
]
