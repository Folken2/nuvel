"""``command_guard`` — a ``before_tool_callback`` that vets shell commands.

Sibling to ``exfil_guard``: where that scans tool arguments for leaked secrets,
this runs a shell-executing tool's command through the structural
``command_safety.classify`` verdict *before* the tool runs.

* ``deny`` (e.g. ``rm -rf /``) — block the call and return an error result so
  the tool never runs.
* ``ask`` (e.g. ``git push --force``) — allow the call but log a warning and
  leave a breadcrumb in ``tool_context`` state for a downstream observer.
* no verdict — pass through untouched.

Behaviour is controlled by ``COMMAND_GUARD_STRICT`` (default on), mirroring
``exfil_guard``'s knob: in lax mode a ``deny`` is downgraded to warn-and-allow.
Only tools that actually execute a shell command are judged; every other tool
passes straight through.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from typing import Any

from .command_safety import classify

logger = logging.getLogger(__name__)

# Tool names that execute a shell command, matched case-insensitively against
# the tool's ``name``. Anything else is ignored — the guard only judges shells.
_SHELL_TOOL_NAMES = frozenset(
    {
        "bash", "sh", "shell", "terminal", "run_shell", "run_command",
        "run_shell_command", "execute_command", "exec", "command",
    }
)
# Argument keys a shell tool is likely to carry its command string under.
_COMMAND_ARG_KEYS = ("command", "cmd", "script", "shell_command", "input")


def _strict_mode() -> bool:
    return os.getenv("COMMAND_GUARD_STRICT", "1").strip().lower() in ("1", "true", "yes")


def _is_shell_tool(tool: Any) -> bool:
    return (getattr(tool, "name", "") or "").lower() in _SHELL_TOOL_NAMES


def _extract_command(args: Any) -> str | None:
    """Pull the command string out of a tool's arguments, or ``None``."""
    if isinstance(args, str):
        return args or None
    if isinstance(args, Mapping):
        for key in _COMMAND_ARG_KEYS:
            val = args.get(key)
            if isinstance(val, str) and val.strip():
                return val
    return None


def command_guard_callback(tool: Any, args: Any, tool_context: Any) -> dict | None:
    """Block (deny) or flag (ask) a shell command by its structural verdict.

    Registered as a ``before_tool_callback`` alongside ``exfil_guard``.
    Returning a dict short-circuits the tool and hands that dict back to the
    model as the tool result; returning ``None`` lets the call proceed.
    """
    if not _is_shell_tool(tool):
        return None
    command = _extract_command(args)
    if not command:
        return None
    verdict = classify(command)
    if verdict is None:
        return None

    severity, reason = verdict
    tool_name = getattr(tool, "name", "") or "tool"
    if severity == "deny" and _strict_mode():
        message = (
            f"blocked: {tool_name} was asked to run a dangerous command "
            f"({reason}). Refusing to execute it."
        )
        return {
            "error": message,
            "blocked_by": "command_guard",
            "severity": severity,
            "reason": reason,
        }

    # An ``ask`` verdict, or a ``deny`` in lax mode: allow but warn + breadcrumb.
    message = f"command flagged by command_guard ({severity}): {reason}"
    logger.warning("%s -> %s", message, command)
    try:
        tool_context.state["command_warning"] = message
    except Exception:  # tool_context without a writable state — don't crash the call
        pass
    return None


__all__ = ["command_guard_callback"]
