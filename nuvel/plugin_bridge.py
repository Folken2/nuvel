"""Thin wiring between the standalone Agent Plugin Registry and Nuvel.

The :mod:`nuvel.agent_plugins` package is self-contained — it knows nothing
about Google ADK. This module is the only place that bridges its discovered
components into Nuvel's runtime:

* :func:`build_registry` constructs a :class:`PluginRegistry` from config and
  runs discovery.
* :func:`load_plugin_skills` turns discovered ``SKILL.md`` directories into ADK
  ``Skill`` objects for the meta-agent's :class:`SkillToolset`.
* :func:`build_plugin_mcp_toolsets` turns discovered ``mcp.json`` entries into
  ADK ``McpToolset`` instances.

ADK / MCP imports are lazy so this module (and everything importing the
registry) stays usable in environments where those optional deps are absent.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .agent_plugins import PluginRegistry
from .config import get_plugin_dirs

logger = logging.getLogger(__name__)


def build_registry(
    plugin_dirs: list[Path] | None = None,
    *,
    plugin_data_dir: Path | None = None,
    discover: bool = True,
) -> PluginRegistry:
    """Create a :class:`PluginRegistry` and (by default) run discovery.

    When ``plugin_dirs`` is ``None`` the directories are read from config via
    :func:`nuvel.config.get_plugin_dirs`.
    """
    dirs = plugin_dirs if plugin_dirs is not None else get_plugin_dirs()
    registry = PluginRegistry(dirs, plugin_data_dir=plugin_data_dir)
    if discover:
        registry.discover_plugins()
    return registry


def load_plugin_skills(registry: PluginRegistry) -> list[Any]:
    """Load every discovered plugin skill as an ADK ``Skill``.

    Skills that fail to load are logged and skipped — one broken plugin never
    prevents the rest from loading.
    """
    discovered_skills = registry.get_skills()
    if not discovered_skills:
        return []

    try:
        from google.adk.skills import load_skill_from_dir
    except ImportError as exc:  # pragma: no cover - env-dependent
        logger.warning("google.adk.skills unavailable; plugin skills disabled: %s", exc)
        return []

    skills: list[Any] = []
    for discovered in discovered_skills:
        skill_dir = discovered.skill_md_path.parent
        try:
            skills.append(load_skill_from_dir(skill_dir))
            logger.info("Loaded plugin skill: %s", discovered.name)
        except Exception as exc:  # noqa: BLE001 - isolate a bad skill
            logger.warning("Failed to load plugin skill %s: %s", discovered.name, exc)
    return skills


def build_plugin_mcp_toolsets(registry: PluginRegistry) -> list[Any]:
    """Build ADK ``McpToolset`` instances for every discovered MCP server.

    Deprecated ``sse`` entries and any entry whose transport cannot be wired are
    skipped with a warning. Returns an empty list when the ADK MCP deps (or the
    ``mcp`` package) are unavailable.
    """
    grouped = registry.get_mcp_servers()
    if not grouped:
        return []

    try:
        from google.adk.tools.mcp_tool.mcp_toolset import McpToolset
        from google.adk.tools.mcp_tool.mcp_session_manager import (
            SseConnectionParams,
            StdioConnectionParams,
            StreamableHTTPConnectionParams,
        )
        from mcp import StdioServerParameters
    except ImportError as exc:  # pragma: no cover - env-dependent
        logger.warning("ADK MCP deps unavailable; plugin MCP servers disabled: %s", exc)
        return []

    toolsets: list[Any] = []
    for name, entries in grouped.items():
        for entry in entries:
            try:
                if entry.transport == "stdio":
                    params = StdioConnectionParams(
                        server_params=StdioServerParameters(
                            command=entry.command,
                            args=entry.args or [],
                            env=entry.env or None,
                            cwd=entry.cwd,
                        )
                    )
                elif entry.transport == "streamable-http":
                    params = StreamableHTTPConnectionParams(
                        url=entry.url, headers=entry.headers or None
                    )
                elif entry.transport == "sse":
                    params = SseConnectionParams(
                        url=entry.url, headers=entry.headers or None
                    )
                else:  # pragma: no cover - reader only emits known transports
                    logger.warning(
                        "Skipping MCP server %s: unknown transport %r",
                        name,
                        entry.transport,
                    )
                    continue
            except Exception as exc:  # noqa: BLE001 - isolate a bad server
                logger.warning("Failed to wire MCP server %s: %s", name, exc)
                continue
            toolsets.append(McpToolset(connection_params=params))
            logger.info("Wired plugin MCP server: %s (%s)", name, entry.transport)
    return toolsets


__all__ = [
    "build_registry",
    "load_plugin_skills",
    "build_plugin_mcp_toolsets",
]
