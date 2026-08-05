"""Human-in-the-loop tool approval over ACP (``session/request_permission``).

When the agent is about to call a tool that needs a human's sign-off, this
gate asks the *editor* — via an agent→client ``session/request_permission``
request — and lets the user Allow / Reject (once or always). It plugs into
ADK as a ``before_tool_callback``: returning ``None`` lets the tool run,
returning a dict short-circuits it with that dict as the tool result.

This is the ACP transport for the same idea as the ``adk-callbacks-hitl``
skill's defensive tool gate — here the approval decision comes from the
editor UI instead of session state.

Which tools are gated is configured by environment variables (read once, at
session start):

    ACP_PERMISSION_MODE    off | sensitive | all   (default: sensitive)
    ACP_PERMISSION_TOOLS   comma-separated tool names that require approval;
                           when set it *replaces* the built-in sensitive set.

``sensitive`` gates a small built-in set of obviously-consequential tools
(and anything the agent author lists in ``ACP_PERMISSION_TOOLS``); ``all``
gates every tool except a few always-safe ones; ``off`` disables the gate.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

# Sends an agent→client request and resolves with the client's result.
ClientRequester = Callable[[str, dict], Awaitable[Any]]

# Tools consequential enough to gate by default under `sensitive` mode.
_SENSITIVE_TOOLS = {
    "delete_record",
    "send_email",
    "modify_permissions",
    "execute_sql",
    "deploy_service",
    "write_text_file",  # the fs bridge write — edits the user's workspace
}
# Name prefixes that read as destructive regardless of the specific agent.
_SENSITIVE_PREFIXES = ("delete_", "remove_", "drop_", "purge_", "deploy_")
# Never gate these — they are read-only / navigational.
_ALWAYS_ALLOWED = {
    "read_text_file",
    "list_skills",
    "load_skill",
    "load_skill_resource",
}

# ACP permission option ids we offer the client.
_OPTIONS = [
    {"optionId": "allow-once", "name": "Allow", "kind": "allow_once"},
    {"optionId": "allow-always", "name": "Always allow", "kind": "allow_always"},
    {"optionId": "reject-once", "name": "Reject", "kind": "reject_once"},
    {"optionId": "reject-always", "name": "Always reject", "kind": "reject_always"},
]


def _needs_permission(name: str, mode: str, explicit: Optional[set]) -> bool:
    """Decide whether tool ``name`` requires approval under the configured mode."""
    if mode == "off" or not name:
        return False
    if name in _ALWAYS_ALLOWED:
        return False
    if mode == "all":
        return True
    # mode == "sensitive"
    if explicit is not None:
        return name in explicit
    return name in _SENSITIVE_TOOLS or name.startswith(_SENSITIVE_PREFIXES)


def make_permission_callback(
    session_id: str,
    requester: ClientRequester,
    *,
    mode: str = "sensitive",
    explicit: Optional[set] = None,
    chained: Optional[Callable] = None,
):
    """Build a ``before_tool_callback`` that gates tool calls over ACP.

    Args:
        session_id: the ACP session these approvals belong to.
        requester: :meth:`ACPAgent.request` — issues the client request.
        mode: ``off`` | ``sensitive`` | ``all``.
        explicit: when set (from ``ACP_PERMISSION_TOOLS``), the exact set of
            tool names to gate under ``sensitive`` mode.
        chained: an existing ``before_tool_callback`` to run first; if it
            returns a result (blocking the tool) we honor it and skip the
            approval prompt.

    Returns ``None`` when the gate is disabled (``mode == "off"`` and no
    chained callback), so the caller can leave the agent's callback untouched.
    """
    if mode == "off" and chained is None:
        return None

    # Remembered "always" decisions for this session (in-process lifetime).
    allowed_always: set[str] = set()
    rejected_always: set[str] = set()

    async def _maybe_await(value):
        if hasattr(value, "__await__"):
            return await value
        return value

    async def _ask(name: str, args: dict, tool_context: Any) -> bool:
        """Return True to allow the tool, False to block it."""
        tool_call_id = str(getattr(tool_context, "function_call_id", "") or name)
        try:
            result = await requester(
                "session/request_permission",
                {
                    "sessionId": session_id,
                    "toolCall": {
                        "toolCallId": tool_call_id,
                        "title": name,
                        "kind": "other",
                        "rawInput": args,
                    },
                    "options": _OPTIONS,
                },
            )
        except Exception as exc:  # noqa: BLE001 — fail closed on client error
            logger.warning("[HITL BLOCK] permission request for %r failed: %s", name, exc)
            return False

        outcome = (result or {}).get("outcome") or {}
        if outcome.get("outcome") == "cancelled":
            logger.info("[HITL REJECT] permission for %r cancelled", name)
            return False
        option_id = str(outcome.get("optionId", ""))
        if option_id.startswith("allow"):
            if option_id == "allow-always":
                allowed_always.add(name)
            logger.info("[HITL APPROVE] tool %r approved (%s)", name, option_id)
            return True
        if option_id == "reject-always":
            rejected_always.add(name)
        logger.info("[HITL REJECT] tool %r rejected (%s)", name, option_id or "no option")
        return False

    async def before_tool_callback(tool, args, tool_context):
        # Honor any pre-existing gate first (e.g. a defensive tool callback).
        if chained is not None:
            chained_result = await _maybe_await(chained(tool, args, tool_context))
            if chained_result is not None:
                return chained_result

        name = getattr(tool, "name", "") or ""
        if name in allowed_always:
            return None
        if name in rejected_always:
            return {
                "status": "rejected",
                "message": f"The user previously chose to always reject '{name}'.",
            }
        if not _needs_permission(name, mode, explicit):
            return None

        if await _ask(name, args or {}, tool_context):
            return None
        return {
            "status": "rejected",
            "message": (
                f"The user declined to run '{name}'. Do not retry it; "
                f"explain what you were about to do and ask how to proceed."
            ),
        }

    return before_tool_callback


def permission_callback_from_env(
    session_id: str,
    requester: ClientRequester,
    *,
    chained: Optional[Callable] = None,
):
    """Build the permission callback from ``ACP_PERMISSION_*`` env vars.

    Returns ``None`` when disabled and there is nothing to chain, so callers
    can leave the agent's ``before_tool_callback`` as-is.
    """
    mode = (os.getenv("ACP_PERMISSION_MODE", "sensitive") or "sensitive").strip().lower()
    if mode not in ("off", "sensitive", "all"):
        logger.warning("Unknown ACP_PERMISSION_MODE=%r; falling back to 'sensitive'.", mode)
        mode = "sensitive"

    explicit: Optional[set] = None
    raw = os.getenv("ACP_PERMISSION_TOOLS")
    if raw:
        names = {n.strip() for n in raw.split(",") if n.strip()}
        explicit = names or None

    return make_permission_callback(
        session_id, requester, mode=mode, explicit=explicit, chained=chained
    )
