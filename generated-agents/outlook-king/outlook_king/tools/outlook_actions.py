"""
Outlook action tools.

These tools don't reach into Outlook directly — the ADK agent runs in the
FastAPI backend, not inside Office. Instead, each tool *records an action
request* into ADK session state under ``outlook:pending_actions``. The
backend drains that list at the end of the turn and ships it back to the
add-in (via the chat response payload and SSE ``action`` events). The
Office.js add-in then executes the action against the live Mailbox/Item
and POSTs the outcome back to ``/api/outlook/action-result``.

This indirection is what lets the agent *do things in Outlook* without
the ADK process having an Office context.

Action schema (one entry per call):
    {
        "id": "<uuid>",
        "type": "insert_text" | "replace_body" | "set_subject" | ...,
        "params": {...},
        "requires_mode": "compose" | "read" | "any",
        "description": str,   # human-readable for the UI
    }

The agent should call at most one action per turn unless they're clearly
related (e.g. ``set_subject`` + ``add_recipients`` + ``insert_text`` for
a draft scaffold).
"""

from __future__ import annotations

import uuid
from typing import Any

from google.adk.tools import FunctionTool, ToolContext

PENDING_ACTIONS_KEY = "outlook:pending_actions"
ACTION_RESULTS_KEY = "outlook:action_results"


def _queue(tool_context: ToolContext, action: dict[str, Any]) -> dict:
    pending = list(tool_context.state.get(PENDING_ACTIONS_KEY) or [])
    action.setdefault("id", uuid.uuid4().hex[:12])
    pending.append(action)
    tool_context.state[PENDING_ACTIONS_KEY] = pending
    return {
        "status": "queued",
        "action_id": action["id"],
        "type": action["type"],
        "note": (
            "Action queued for execution in Outlook. The add-in will run it "
            "after this turn completes and post the result back."
        ),
    }


# ── Compose actions ─────────────────────────────────────────────────


def insert_text_at_cursor(
    tool_context: ToolContext,
    text: str,
    as_html: bool = False,
) -> dict:
    """Insert text into the open compose window at the cursor / selection.

    Replaces the current selection if any, otherwise inserts at the caret.
    Requires an active compose window. Prefer this over ``replace_body``
    when the user has typed something they want preserved — e.g. you're
    finishing a sentence or adding a paragraph.

    Args:
        text: The text (or HTML) to insert. Plain text by default.
        as_html: Set true to insert HTML markup; default is plain text.
    """
    if not text:
        return {"status": "skip", "reason": "empty text"}
    return _queue(
        tool_context,
        {
            "type": "insert_text",
            "params": {"text": text, "as_html": bool(as_html)},
            "requires_mode": "compose",
            "description": f"Insert {len(text)} chars at cursor",
        },
    )


def replace_compose_body(
    tool_context: ToolContext,
    body: str,
    as_html: bool = False,
) -> dict:
    """Replace the entire compose body with new text.

    Destructive — wipes whatever the user had. Only use when the user
    explicitly asked for a full rewrite, or when the existing body is
    empty. For tweaks, use ``insert_text_at_cursor`` instead.

    Args:
        body: The new body content.
        as_html: Treat ``body`` as HTML; default plain text.
    """
    return _queue(
        tool_context,
        {
            "type": "replace_body",
            "params": {"body": body, "as_html": bool(as_html)},
            "requires_mode": "compose",
            "description": "Replace entire draft body",
        },
    )


def set_subject(tool_context: ToolContext, subject: str) -> dict:
    """Set the subject line on the open compose window."""
    return _queue(
        tool_context,
        {
            "type": "set_subject",
            "params": {"subject": subject},
            "requires_mode": "compose",
            "description": f"Set subject: {subject[:60]}",
        },
    )


def add_recipients(
    tool_context: ToolContext,
    addresses: str,
    field: str = "to",
) -> dict:
    """Add recipients to the open compose window.

    Args:
        addresses: Comma-separated email addresses.
        field: ``"to"``, ``"cc"``, or ``"bcc"``. Defaults to ``"to"``.
            (Note: Office.js exposes ``bcc`` only when the compose surface
            has a BCC field showing; ``bcc`` falls back to ``cc`` if not.)
    """
    field = (field or "to").lower().strip()
    if field not in ("to", "cc", "bcc"):
        return {"status": "error", "message": f"Unknown field: {field}"}
    parsed = [a.strip() for a in addresses.split(",") if a.strip()]
    if not parsed:
        return {"status": "skip", "reason": "no addresses parsed"}
    return _queue(
        tool_context,
        {
            "type": "add_recipients",
            "params": {"addresses": parsed, "field": field},
            "requires_mode": "compose",
            "description": f"Add {len(parsed)} recipient(s) to {field.upper()}",
        },
    )


def remove_recipients(
    tool_context: ToolContext,
    addresses: str,
    field: str = "to",
) -> dict:
    """Remove recipients from the open compose window.

    Args:
        addresses: Comma-separated email addresses to drop.
        field: ``"to"``, ``"cc"``, or ``"bcc"``.
    """
    field = (field or "to").lower().strip()
    parsed = [a.strip().lower() for a in addresses.split(",") if a.strip()]
    if not parsed:
        return {"status": "skip", "reason": "no addresses parsed"}
    return _queue(
        tool_context,
        {
            "type": "remove_recipients",
            "params": {"addresses": parsed, "field": field},
            "requires_mode": "compose",
            "description": f"Remove {len(parsed)} from {field.upper()}",
        },
    )


def set_importance(tool_context: ToolContext, level: str) -> dict:
    """Set message importance on the open compose window.

    Args:
        level: ``"low"``, ``"normal"``, or ``"high"``.
    """
    norm = (level or "normal").lower().strip()
    if norm not in ("low", "normal", "high"):
        return {"status": "error", "message": f"Unknown importance: {level}"}
    return _queue(
        tool_context,
        {
            "type": "set_importance",
            "params": {"level": norm},
            "requires_mode": "compose",
            "description": f"Set importance: {norm}",
        },
    )


def attach_file_from_url(
    tool_context: ToolContext,
    url: str,
    name: str,
    is_inline: bool = False,
) -> dict:
    """Attach a file to the open compose by URL (must be publicly fetchable).

    Office.js supports adding attachments by URL via ``addFileAttachmentAsync``.
    The mailbox server downloads the URL — it must be reachable from the
    user's Outlook host. No auth headers can be passed.

    Args:
        url: HTTPS URL of the file.
        name: Filename to show in the message.
        is_inline: Inline (embedded) vs traditional attachment.
    """
    if not url or not name:
        return {"status": "error", "message": "url and name are required"}
    return _queue(
        tool_context,
        {
            "type": "attach_file_url",
            "params": {"url": url, "name": name, "is_inline": bool(is_inline)},
            "requires_mode": "compose",
            "description": f"Attach: {name}",
        },
    )


# ── Read-mode actions ───────────────────────────────────────────────


def create_reply_draft(
    tool_context: ToolContext,
    body: str = "",
    reply_all: bool = False,
    as_html: bool = False,
) -> dict:
    """Open a new reply (or reply-all) draft for the currently-selected message.

    Use when the user is in read-mode and asks to "reply to this". The
    add-in opens the reply compose window pre-populated with ``body``.

    Args:
        body: Optional initial draft text. Empty opens a blank reply.
        reply_all: If true, reply to everyone (To + Cc); else just To.
        as_html: Treat ``body`` as HTML.
    """
    return _queue(
        tool_context,
        {
            "type": "create_reply",
            "params": {"body": body, "reply_all": bool(reply_all), "as_html": bool(as_html)},
            "requires_mode": "read",
            "description": "Reply-all" if reply_all else "Reply",
        },
    )


def create_forward_draft(
    tool_context: ToolContext,
    to_addresses: str,
    body: str = "",
    as_html: bool = False,
) -> dict:
    """Open a forward draft for the currently-selected message.

    Args:
        to_addresses: Comma-separated recipients for the forward.
        body: Optional intro text the user can edit.
        as_html: Treat ``body`` as HTML.
    """
    parsed = [a.strip() for a in to_addresses.split(",") if a.strip()]
    return _queue(
        tool_context,
        {
            "type": "create_forward",
            "params": {"to": parsed, "body": body, "as_html": bool(as_html)},
            "requires_mode": "read",
            "description": f"Forward to {', '.join(parsed) or '(no recipients)'}",
        },
    )


def apply_categories(tool_context: ToolContext, categories: str) -> dict:
    """Apply Outlook categories to the currently-selected message.

    Categories must already exist in the user's master list (Outlook will
    silently drop unknown ones on some clients). Works in both read and
    compose mode.

    Args:
        categories: Comma-separated category names.
    """
    parsed = [c.strip() for c in categories.split(",") if c.strip()]
    if not parsed:
        return {"status": "skip", "reason": "no categories"}
    return _queue(
        tool_context,
        {
            "type": "apply_categories",
            "params": {"categories": parsed},
            "requires_mode": "any",
            "description": f"Apply categories: {', '.join(parsed)}",
        },
    )


def set_flag(tool_context: ToolContext, state: str = "flagged") -> dict:
    """Flag or unflag the currently-selected message for follow-up.

    Args:
        state: ``"flagged"``, ``"complete"``, or ``"none"``.
    """
    norm = (state or "flagged").lower().strip()
    if norm not in ("flagged", "complete", "none"):
        return {"status": "error", "message": f"Unknown flag state: {state}"}
    return _queue(
        tool_context,
        {
            "type": "set_flag",
            "params": {"state": norm},
            "requires_mode": "read",
            "description": f"Set flag: {norm}",
        },
    )


# ── Context refresh ─────────────────────────────────────────────────


def refresh_outlook_context(tool_context: ToolContext) -> dict:
    """Ask the add-in to re-snapshot the current Outlook state.

    Use when you suspect the in-session context is stale — e.g. the user
    said "I just changed it" or you're about to make a destructive edit
    and want the freshest selection / cursor position. The add-in will
    re-read the mailbox item and push a new context payload before the
    next turn.

    Returns immediately; the refresh runs after the turn ends.
    """
    return _queue(
        tool_context,
        {
            "type": "refresh_context",
            "params": {},
            "requires_mode": "any",
            "description": "Refresh Outlook context",
        },
    )


# ── Result inspection ───────────────────────────────────────────────


def get_recent_action_results(tool_context: ToolContext, limit: int = 5) -> dict:
    """Read the outcomes of recently-executed Outlook actions.

    The add-in posts results back to the backend after each action runs;
    they accumulate in session state. Use this to confirm an action
    succeeded (e.g. before telling the user "done") or to recover from
    a failure (read ``error`` and try a different approach).

    Args:
        limit: Max number of most-recent results to return.
    """
    results = list(tool_context.state.get(ACTION_RESULTS_KEY) or [])
    return {
        "status": "ok",
        "count": len(results),
        "results": results[-max(1, int(limit)):],
    }


outlook_action_tool_list = [
    FunctionTool(insert_text_at_cursor),
    FunctionTool(replace_compose_body),
    FunctionTool(set_subject),
    FunctionTool(add_recipients),
    FunctionTool(remove_recipients),
    FunctionTool(set_importance),
    FunctionTool(attach_file_from_url),
    FunctionTool(create_reply_draft),
    FunctionTool(create_forward_draft),
    FunctionTool(apply_categories),
    FunctionTool(set_flag),
    FunctionTool(refresh_outlook_context),
    FunctionTool(get_recent_action_results),
]
