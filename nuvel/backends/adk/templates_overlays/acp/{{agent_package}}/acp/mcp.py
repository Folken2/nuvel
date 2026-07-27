"""Wire editor-supplied MCP servers into the agent.

An ACP client passes ``mcpServers`` in ``session/new`` — the MCP servers the
editor wants this session's agent to have access to (filesystem, git, etc.).
This module turns those declarations into ADK ``McpToolset`` instances so the
agent gains the editor's tools at connect time.

Parsing (:func:`parse_mcp_servers`) is pure and importable without ADK;
toolset construction (:func:`build_mcp_toolsets`) imports ADK/mcp lazily and
degrades gracefully — a server we can't build is skipped with a warning
rather than failing the whole session.

Supported transports mirror the capabilities advertised in ``initialize``:
``stdio`` (always), plus ``http`` (Streamable HTTP) and ``sse``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class McpServerSpec:
    """A normalized, transport-agnostic view of one ``mcpServers`` entry."""

    name: str
    transport: str  # "stdio" | "http" | "sse"
    command: str = ""
    args: list = field(default_factory=list)
    env: dict = field(default_factory=dict)
    url: str = ""
    headers: dict = field(default_factory=dict)


def _pairs_to_dict(items: Any) -> dict:
    """Coerce ACP name/value pair lists (env, headers) into a plain dict.

    ACP encodes env vars and HTTP headers as ``[{"name": ..., "value": ...}]``.
    A plain ``{k: v}`` mapping is tolerated too for forgiving inputs.
    """
    if isinstance(items, dict):
        return {str(k): str(v) for k, v in items.items()}
    result: dict[str, str] = {}
    if isinstance(items, list):
        for it in items:
            if isinstance(it, dict) and "name" in it:
                result[str(it["name"])] = str(it.get("value", ""))
    return result


def parse_mcp_server(entry: Any) -> Optional[McpServerSpec]:
    """Normalize one ``mcpServers`` entry, or ``None`` if unsupported.

    The ACP stdio shape has no ``type`` field (just ``command``/``args``/``env``);
    ``http`` and ``sse`` transports are tagged with ``type`` and carry a ``url``.
    """
    if not isinstance(entry, dict):
        return None
    name = entry.get("name")
    if not isinstance(name, str) or not name:
        return None

    mtype = entry.get("type")
    if mtype in (None, "stdio") and entry.get("command"):
        return McpServerSpec(
            name=name,
            transport="stdio",
            command=str(entry["command"]),
            args=[str(a) for a in (entry.get("args") or [])],
            env=_pairs_to_dict(entry.get("env")),
        )
    if mtype == "http" and entry.get("url"):
        return McpServerSpec(
            name=name,
            transport="http",
            url=str(entry["url"]),
            headers=_pairs_to_dict(entry.get("headers")),
        )
    if mtype == "sse" and entry.get("url"):
        return McpServerSpec(
            name=name,
            transport="sse",
            url=str(entry["url"]),
            headers=_pairs_to_dict(entry.get("headers")),
        )
    return None


def parse_mcp_servers(entries: Any) -> list[McpServerSpec]:
    """Parse the whole ``mcpServers`` list, dropping unsupported entries."""
    if not isinstance(entries, list):
        return []
    specs: list[McpServerSpec] = []
    for entry in entries:
        spec = parse_mcp_server(entry)
        if spec is not None:
            specs.append(spec)
        else:
            logger.warning("Skipping unsupported mcpServers entry: %r", entry)
    return specs


def toolset_from_spec(spec: McpServerSpec, cwd: Optional[str] = None) -> Optional[object]:
    """Build an ADK ``McpToolset`` for one spec, or ``None`` on failure."""
    try:
        from google.adk.tools.mcp_tool.mcp_session_manager import (
            SseConnectionParams,
            StdioConnectionParams,
            StreamableHTTPConnectionParams,
        )
        from google.adk.tools.mcp_tool.mcp_toolset import McpToolset
    except ImportError as exc:
        logger.warning(
            "ADK MCP support unavailable (%s); skipping MCP server %r.", exc, spec.name
        )
        return None

    try:
        if spec.transport == "stdio":
            from mcp import StdioServerParameters

            params = StdioServerParameters(
                command=spec.command,
                args=list(spec.args),
                env=dict(spec.env) or None,
                cwd=cwd,
            )
            return McpToolset(
                connection_params=StdioConnectionParams(server_params=params)
            )
        if spec.transport == "http":
            return McpToolset(
                connection_params=StreamableHTTPConnectionParams(
                    url=spec.url, headers=spec.headers or None
                )
            )
        if spec.transport == "sse":
            return McpToolset(
                connection_params=SseConnectionParams(
                    url=spec.url, headers=spec.headers or None
                )
            )
    except Exception as exc:  # noqa: BLE001 — one bad server shouldn't kill the session
        logger.warning("Failed to build MCP toolset for %r: %s", spec.name, exc)
        return None
    return None


def build_mcp_toolsets(entries: Any, cwd: Optional[str] = None) -> list:
    """Turn an ACP ``mcpServers`` list into a list of ADK toolsets."""
    toolsets = []
    for spec in parse_mcp_servers(entries):
        toolset = toolset_from_spec(spec, cwd=cwd)
        if toolset is not None:
            toolsets.append(toolset)
            logger.info("Wired MCP server %r (%s) into the session.", spec.name, spec.transport)
    return toolsets
