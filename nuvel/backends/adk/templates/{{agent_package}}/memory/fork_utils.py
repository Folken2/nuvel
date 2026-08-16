"""
Pure helpers shared by the memory forks.

Owns the conversation-snapshot formatter and the tool-whitelist callback
factory used to gate a throwaway judge agent's toolset. No I/O, no ADK
runner plumbing (that lives in ``sibling_runner.py``) — kept dependency-free
so it is trivially unit-testable.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


def event_text(event: Any) -> str:
    """Concatenate the text parts of an ADK event's content."""
    content = getattr(event, "content", None)
    if not content:
        return ""
    parts = getattr(content, "parts", None) or []
    return "".join(p.text for p in parts if getattr(p, "text", None))


def format_conversation_snapshot(events: list[Any], *, tag: str = "CONVERSATION") -> str:
    """Render events as a ``<CONVERSATION>...</CONVERSATION>`` block.

    Pure-tool-call events (no text) are skipped so the judge sees the
    conversation, not the plumbing.
    """
    lines: list[str] = []
    for event in events:
        text = event_text(event)
        if not text:
            continue
        author = getattr(event, "author", "?") or "?"
        lines.append(f"[{author}]\n{text}")
    body = "\n\n".join(lines) if lines else "(no conversation)"
    return f"<{tag}>\n{body}\n</{tag}>"


ExtraCheck = Callable[[str, Any], Optional[dict]]


def make_whitelist_callback(
    allowed: frozenset[str],
    *,
    fork_name: str,
    extra_check: ExtraCheck | None = None,
) -> Callable[..., Any]:
    """Build a ``before_tool_callback`` that blocks anything outside ``allowed``.

    ``extra_check(name, args)`` runs after the allow-list check and may return
    its own block dict (same ADK convention: a dict short-circuits the tool
    with that result, ``None`` lets the call proceed).
    """

    async def _callback(*, tool: Any, tool_args: Any = None, **kwargs: Any):
        name = getattr(tool, "name", "") or ""
        if name not in allowed:
            return {
                "error": (
                    f"Tool {name!r} is not available in the {fork_name} fork. "
                    f"Allowed: {sorted(allowed)}."
                )
            }
        if extra_check is not None:
            args = tool_args if tool_args is not None else kwargs.get("args")
            return extra_check(name, args)
        return None

    return _callback


__all__ = [
    "event_text",
    "format_conversation_snapshot",
    "make_whitelist_callback",
    "ExtraCheck",
]
