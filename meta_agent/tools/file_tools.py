"""File-operation tools scoped to the generated-agent output directory."""

from __future__ import annotations

import os
from pathlib import Path

from google.adk.tools import FunctionTool

_OUTPUT_DIR = os.getenv("AGENTS_OUTPUT_DIR", "./generated-agents")


# ── Path safety ────────────────────────────────────────────────────────


def _resolve_safe_path(path: str, base_dir: str) -> str:
    """Resolve *path* relative to *base_dir*; reject escapes.

    Raises ValueError if the path is absolute or escapes base_dir via ``../``.
    """
    if os.path.isabs(path):
        raise ValueError(f"Absolute paths are not allowed: {path}")

    resolved = os.path.normpath(os.path.join(base_dir, path))
    base_resolved = os.path.normpath(base_dir)

    if not (resolved == base_resolved or resolved.startswith(base_resolved + os.sep)):
        raise ValueError(f"Path escapes base directory: {path}")

    return resolved


# ── Impl functions (no ToolContext dependency) ─────────────────────────


def _write_file_impl(path: str, content: str, base_dir: str) -> dict:
    try:
        full = _resolve_safe_path(path, base_dir)
    except ValueError as exc:
        return {"status": "error", "message": str(exc)}

    Path(full).parent.mkdir(parents=True, exist_ok=True)
    encoded = content.encode("utf-8")
    Path(full).write_bytes(encoded)
    return {
        "status": "success",
        "message": f"Wrote {path}",
        "path": path,
        "bytes": len(encoded),
    }


def _read_file_impl(path: str, base_dir: str) -> dict:
    try:
        full = _resolve_safe_path(path, base_dir)
    except ValueError as exc:
        return {"status": "error", "message": str(exc)}

    if not os.path.isfile(full):
        return {"status": "error", "message": f"File not found: {path}"}

    content = Path(full).read_text(encoding="utf-8")
    return {
        "status": "success",
        "path": path,
        "content": content,
        "bytes": len(content.encode("utf-8")),
    }


def _list_files_impl(path: str, base_dir: str) -> dict:
    try:
        full = _resolve_safe_path(path, base_dir)
    except ValueError as exc:
        return {"status": "error", "message": str(exc)}

    if not os.path.isdir(full):
        return {"status": "error", "message": f"Directory not found: {path}"}

    entries: list[str] = []
    for name in sorted(os.listdir(full)):
        if os.path.isdir(os.path.join(full, name)):
            entries.append(name + "/")
        else:
            entries.append(name)

    return {
        "status": "success",
        "path": path,
        "entries": entries,
        "count": len(entries),
    }


# ── Wrapped functions (accept ToolContext) ─────────────────────────────


def _get_base_dir(tool_context=None) -> str:
    if tool_context is not None:
        return tool_context.state.get("agent_output_dir", _OUTPUT_DIR)
    return _OUTPUT_DIR


def write_file(path: str, content: str, tool_context=None) -> dict:
    """Write a file to the generated agent directory."""
    return _write_file_impl(path, content, _get_base_dir(tool_context))


def read_file(path: str, tool_context=None) -> dict:
    """Read a file from the generated agent directory."""
    return _read_file_impl(path, _get_base_dir(tool_context))


def list_files(path: str = ".", tool_context=None) -> dict:
    """List files and directories in the generated agent directory."""
    return _list_files_impl(path, _get_base_dir(tool_context))


# ── FunctionTool instances ─────────────────────────────────────────────

write_file_tool = FunctionTool(func=write_file)
read_file_tool = FunctionTool(func=read_file)
list_files_tool = FunctionTool(func=list_files)
