"""Defense-in-depth path validator for file-operation tools.

Fix A (file_tools._get_base_dir) already scopes writes to the scaffolded
agent directory. This callback is the second line of defense: it catches
the common LLM mistake of writing paths that either start with the
kebab-case agent wrapper (e.g. ``ai-news-weekly-digest/...``) or with
``generated-agents/...``. In both cases the path is auto-corrected so it
lands inside the correct agent directory.

Activated via ``before_tool_callback=path_guard`` on the meta-agent.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# Tools whose first positional arg / ``path`` kwarg is a file path we should guard.
_PATH_TOOLS = {"write_file", "read_file", "list_files"}


def _normalize_path(path: str, kebab_name: str, package_name: str) -> tuple[str, str | None]:
    """Strip common wrong prefixes from *path*.

    Returns (new_path, note) where note is a human-readable description of the
    correction, or None if no correction was applied.
    """
    if not path:
        return path, None

    original = path
    # Normalise any Windows-style separators the LLM might emit.
    p = path.replace("\\", "/")

    # Strip a leading ``./`` (literal prefix, not character class — ``.env`` is valid).
    while p.startswith("./"):
        p = p[2:]

    # Strip "generated-agents/" if present at the start.
    prefix = "generated-agents/"
    if p.startswith(prefix):
        p = p[len(prefix):]

    # Handle the kebab-case agent name appearing as the first segment.
    #
    # Two distinct LLM mistakes land here:
    #   (a) Kebab wrapper + snake package: ``my-agent/my_agent/...``.
    #       Writes are already scoped to the agent root via _get_base_dir, so
    #       the wrapper is redundant and must be stripped.
    #   (b) Kebab-as-package confusion: ``my-agent/tools/foo.py`` where the
    #       LLM meant ``my_agent/tools/foo.py``. Stripping alone would land the
    #       file one level too shallow (outside the package), so rewrite the
    #       leading ``my-agent/`` to ``my_agent/`` instead.
    if kebab_name:
        wrapper = kebab_name + "/"
        if p.startswith(wrapper):
            rest = p[len(wrapper):]
            first_segment = rest.split("/", 1)[0]
            if package_name and first_segment != package_name:
                # (b) confusion case — rewrite to snake package.
                p = f"{package_name}/{rest}"
            else:
                # (a) wrapper case — strip.
                p = rest
        elif p == kebab_name:
            # Bare wrapper name with no rest — treat as empty and fall through.
            p = ""

    # If, after stripping, the path is empty (LLM pointed at the wrapper itself),
    # don't return an empty string — just leave the original so write_file can fail
    # with a clear error.
    if not p:
        return original, None

    if p != original:
        note = f"path auto-corrected: {original!r} → {p!r}"
        return p, note
    return original, None


def path_guard(tool, args: dict[str, Any], tool_context) -> dict | None:
    """before_tool_callback: normalise file-tool paths before execution.

    - Returns ``None`` to let the tool run (possibly with corrected args).
    - Returns a dict to short-circuit the tool with a user-facing error.
    """
    tool_name = getattr(tool, "name", None) or getattr(tool, "__name__", "")
    if tool_name not in _PATH_TOOLS:
        return None

    path = args.get("path")
    if not isinstance(path, str):
        return None

    # Absolute paths are always rejected — write_file will also reject them, but
    # failing fast here gives a clearer error message.
    if os.path.isabs(path):
        return {
            "status": "error",
            "message": (
                f"Absolute paths are not allowed: {path}. "
                "Use package-relative paths like 'tools/foo.py' or "
                "'<package>/tools/foo.py'."
            ),
        }

    state = getattr(tool_context, "state", None)
    kebab_name = (state.get("current_agent_name") if state else "") or ""
    package_name = (state.get("current_agent_package") if state else "") or ""

    new_path, note = _normalize_path(path, kebab_name, package_name)
    if note:
        logger.info("[path_guard] %s (tool=%s)", note, tool_name)
        args["path"] = new_path

    return None
