"""Nuvel Agent Plugin Registry.

A self-contained loader that discovers, validates, and loads Agent Plugins
(https://agent-plugins.org/) — the vendor-neutral packaging standard for AI
agent skills + MCP servers (Agent Plugins v1.0.0).

Uses only the Python standard library.
"""

from __future__ import annotations

from .exceptions import (
    AgentPluginError,
    ComponentDiscoveryError,
    ManifestError,
    PathEscapeError,
    SchemaVersionError,
)
from .manifest import PluginManifest
from .mcp_reader import McpServerEntry, read_mcp_config
from .registry import PluginInfo, PluginLoadError, PluginRegistry
from .schema import SUPPORTED_SCHEMA_ID, validate_manifest
from .skill_discovery import DiscoveredSkill, discover_skills

__version__ = "1.0.0"

__all__ = [
    "__version__",
    # registry
    "PluginRegistry",
    "PluginInfo",
    "PluginLoadError",
    # manifest
    "PluginManifest",
    "validate_manifest",
    "SUPPORTED_SCHEMA_ID",
    # skills
    "DiscoveredSkill",
    "discover_skills",
    # mcp
    "McpServerEntry",
    "read_mcp_config",
    # exceptions
    "AgentPluginError",
    "ManifestError",
    "SchemaVersionError",
    "ComponentDiscoveryError",
    "PathEscapeError",
]
