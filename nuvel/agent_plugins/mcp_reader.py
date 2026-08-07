"""``mcp.json`` loader + validator.

Failure isolation: an invalid individual server entry is skipped (the rest
still load); an invalid ``mcp.json`` as a whole disables MCP for the plugin but
never affects other components.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

#: Accepted transport (``type``) values.
VALID_TRANSPORTS = frozenset({"stdio", "streamable-http", "sse"})

#: A bare executable name: no path separators, no shell metacharacters.
_BARE_COMMAND_RE = re.compile(r"^[A-Za-z0-9._+-]+$")

_PLACEHOLDER_RE = re.compile(r"\$\{(PLUGIN_ROOT|PLUGIN_DATA)\}")


@dataclass
class McpServerEntry:
    """A single validated MCP server declaration."""

    name: str
    transport: str  # "stdio" | "streamable-http" | "sse"
    command: str | None = None
    args: list[str] | None = None
    env: dict[str, str] | None = None
    cwd: str | None = None
    url: str | None = None
    headers: dict[str, str] | None = None
    deprecated: bool = False


class _ServerError(Exception):
    """Internal marker: this server entry is invalid and should be skipped."""


def _expand(value: str, plugin_root: Path, plugin_data: Path | None) -> str:
    """Expand ``${PLUGIN_ROOT}`` / ``${PLUGIN_DATA}`` placeholders in a string."""

    def repl(match: re.Match) -> str:
        token = match.group(1)
        if token == "PLUGIN_ROOT":
            return str(plugin_root)
        # PLUGIN_DATA
        if plugin_data is not None:
            return str(plugin_data)
        return match.group(0)

    return _PLACEHOLDER_RE.sub(repl, value)


def _is_contained(candidate: Path, roots: list[Path]) -> bool:
    try:
        resolved = candidate.resolve()
        resolved_roots = [r.resolve() for r in roots]
    except OSError:
        return False
    for root in resolved_roots:
        if resolved == root or resolved.is_relative_to(root):
            return True
    return False


def _parse_stdio(
    name: str, spec: dict, plugin_root: Path, plugin_data: Path | None
) -> McpServerEntry:
    command = spec.get("command")
    if not isinstance(command, str) or not command:
        raise _ServerError(f"stdio server {name!r} requires a 'command' string")

    # No shell expansion: command is a bare executable name or a ./ path.
    if command.startswith("./"):
        resolved_cmd = plugin_root / command[2:]
        if not _is_contained(resolved_cmd, [plugin_root]):
            raise _ServerError(f"command path for {name!r} escapes the plugin root")
    elif not _BARE_COMMAND_RE.match(command):
        raise _ServerError(
            f"command for {name!r} must be a bare executable name or a './' path"
        )

    args = None
    if "args" in spec:
        raw_args = spec["args"]
        if not isinstance(raw_args, list) or not all(
            isinstance(a, str) for a in raw_args
        ):
            raise _ServerError(f"'args' for {name!r} must be an array of strings")
        args = [_expand(a, plugin_root, plugin_data) for a in raw_args]

    env = None
    if "env" in spec:
        raw_env = spec["env"]
        if not isinstance(raw_env, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in raw_env.items()
        ):
            raise _ServerError(f"'env' for {name!r} must be a string map")
        env = {k: _expand(v, plugin_root, plugin_data) for k, v in raw_env.items()}

    cwd = None
    if "cwd" in spec:
        raw_cwd = spec["cwd"]
        if not isinstance(raw_cwd, str):
            raise _ServerError(f"'cwd' for {name!r} must be a string")
        if not (
            raw_cwd.startswith("./")
            or raw_cwd.startswith("${PLUGIN_ROOT}")
            or raw_cwd.startswith("${PLUGIN_DATA}")
        ):
            raise _ServerError(
                f"'cwd' for {name!r} must start with './', '${{PLUGIN_ROOT}}' "
                "or '${PLUGIN_DATA}'"
            )
        expanded = _expand(raw_cwd, plugin_root, plugin_data)
        if raw_cwd.startswith("./"):
            candidate = plugin_root / raw_cwd[2:]
        else:
            candidate = Path(expanded)
        allowed_roots = [plugin_root]
        if plugin_data is not None:
            allowed_roots.append(plugin_data)
        if not _is_contained(candidate, allowed_roots):
            raise _ServerError(f"'cwd' for {name!r} escapes allowed roots")
        cwd = expanded

    return McpServerEntry(
        name=name,
        transport="stdio",
        command=command,
        args=args,
        env=env,
        cwd=cwd,
    )


def _parse_http(name: str, spec: dict, deprecated: bool) -> McpServerEntry:
    transport = spec["type"]
    url = spec.get("url")
    if not isinstance(url, str) or not url:
        raise _ServerError(f"{transport} server {name!r} requires a 'url' string")
    if transport == "streamable-http" and not url.startswith("https://"):
        raise _ServerError(f"streamable-http server {name!r} 'url' must be HTTPS")

    headers = None
    if "headers" in spec:
        raw_headers = spec["headers"]
        if not isinstance(raw_headers, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in raw_headers.items()
        ):
            raise _ServerError(f"'headers' for {name!r} must be a string map")
        headers = dict(raw_headers)

    return McpServerEntry(
        name=name,
        transport=transport,
        url=url,
        headers=headers,
        deprecated=deprecated,
    )


def read_mcp_config(
    plugin_root: Path, plugin_data: Path | None = None
) -> dict[str, McpServerEntry]:
    """Read and validate ``plugin_root / 'mcp.json'``.

    Returns a mapping of server-name -> :class:`McpServerEntry`. Missing
    ``mcp.json`` is a valid absence (``{}``). An invalid ``mcp.json`` as a
    whole disables MCP (``{}``). Invalid individual servers are skipped.
    """
    plugin_root = Path(plugin_root)
    mcp_path = plugin_root / "mcp.json"

    if not mcp_path.is_file():
        return {}

    try:
        data = json.loads(mcp_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

    if not isinstance(data, dict):
        return {}

    servers = data.get("mcpServers")
    if not isinstance(servers, dict):
        return {}

    result: dict[str, McpServerEntry] = {}
    for name, spec in servers.items():
        if not isinstance(name, str) or not isinstance(spec, dict):
            continue

        transport = spec.get("type")
        if transport not in VALID_TRANSPORTS:
            continue

        try:
            if transport == "stdio":
                entry = _parse_stdio(name, spec, plugin_root, plugin_data)
            elif transport == "streamable-http":
                entry = _parse_http(name, spec, deprecated=False)
            else:  # sse (deprecated)
                entry = _parse_http(name, spec, deprecated=True)
        except _ServerError:
            # Non-fatal: skip this entry, keep loading the rest.
            continue

        result[name] = entry

    return result


__all__ = ["McpServerEntry", "read_mcp_config", "VALID_TRANSPORTS"]
