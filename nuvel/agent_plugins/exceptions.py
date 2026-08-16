"""Exceptions for the Agent Plugin Registry.

All exceptions derive from :class:`AgentPluginError` so callers can catch the
whole family with a single ``except``.
"""

from __future__ import annotations


class AgentPluginError(Exception):
    """Base class for every Agent Plugin error."""


class ManifestError(AgentPluginError):
    """Raised when ``plugin.json`` is missing, unreadable, or fatally invalid."""


class SchemaVersionError(ManifestError):
    """Raised when the ``$schema`` value is missing or unsupported."""


class ComponentDiscoveryError(AgentPluginError):
    """Raised when a plugin component (skills/, mcp.json) cannot be loaded.

    Component failures are isolated by the registry: a broken component is
    reported but does not prevent the rest of the plugin from loading.
    """


class PathEscapeError(AgentPluginError):
    """Raised when a resolved path would escape the plugin root (containment)."""


__all__ = [
    "AgentPluginError",
    "ManifestError",
    "SchemaVersionError",
    "ComponentDiscoveryError",
    "PathEscapeError",
]
