"""
Outlook context tools.

The Office.js taskpane pushes the user's current mailbox state into ADK
session state via ``POST /api/outlook/context`` (see backend/main.py).
These tools surface that state to the agent so it can act on "this email"
or "my draft" without re-asking the user.

State keys (per session):
    outlook:current_compose   compose snapshot incl. body, subject, to/cc/bcc,
                              mode (newMail|reply|forward), selection, cursor,
                              attachments, importance, conversation_id
    outlook:selected_message  read-mode snapshot incl. id, subject, from, to,
                              cc, body, conversation_id, received, folder,
                              categories, flag, has_attachments, attachments
    outlook:account           user account info (email, displayName, timeZone)
    outlook:recent_actions    rolling log of actions executed in this session
"""

from __future__ import annotations

from google.adk.tools import FunctionTool, ToolContext

COMPOSE_KEY = "outlook:current_compose"
MESSAGE_KEY = "outlook:selected_message"
ACCOUNT_KEY = "outlook:account"
RECENT_ACTIONS_KEY = "outlook:recent_actions"
# Stashed by the JSON-manifest OnNewMessageCompose / OnMessageCompose events.
# Lives in parallel to outlook:current_compose so the agent can see the draft
# even before the taskpane is opened.
COMPOSE_DRAFT_KEY = "outlook:compose_draft"
# Append-only log written by the integrated spam-reporting handler.
SPAM_REPORTS_KEY = "outlook:spam_reports"


def get_current_compose(tool_context: ToolContext) -> dict:
    """Return the user's currently-open compose window.

    Call this BEFORE drafting or coaching whenever the user references "my
    draft", "fix this", "make it shorter", or any deictic reference to what
    they are writing right now. The taskpane pushes a fresh snapshot every
    time the compose body changes.

    The payload now includes the user's current selection inside the body
    (``selection`` is empty string when nothing is selected) plus attachment
    metadata and importance. Use ``selection`` to know exactly which span
    the user wants you to operate on when they say "fix this".

    Returns:
        On success: ``{"status": "ok", "body", "subject", "to", "cc", "bcc",
        "mode", "selection", "selection_is_html", "attachments",
        "importance", "conversation_id"}``.
        Otherwise ``{"status": "no_compose", "message": str}``.
    """
    payload = tool_context.state.get(COMPOSE_KEY)
    if not payload:
        return {
            "status": "no_compose",
            "message": "No compose window is open. Ask the user to open a reply or new mail.",
        }
    return {"status": "ok", **payload}


def get_selected_message(tool_context: ToolContext) -> dict:
    """Return the message the user has selected in their inbox.

    Use when the user references "this email", "the message I'm reading",
    or asks for a reply/summary of what they are looking at. For cross-
    folder or historical search, use the Composio ``OUTLOOK_*`` tools.

    Payload now includes folder name, categories, flag state, and a list
    of attachment descriptors (name, size, type). Use those before
    suggesting moves, flags, or attachment-based replies.
    """
    payload = tool_context.state.get(MESSAGE_KEY)
    if not payload:
        return {
            "status": "no_selection",
            "message": "No message selected. Use Composio's Outlook search tools to find one.",
        }
    return {"status": "ok", **payload}


def get_outlook_account(tool_context: ToolContext) -> dict:
    """Return the user's Outlook account info (email, display name, timezone).

    Useful for personalizing drafts (sign-off, first-person voice) and for
    knowing which inbox we're operating in when the user has multiple.
    """
    payload = tool_context.state.get(ACCOUNT_KEY)
    if not payload:
        return {"status": "unknown", "message": "Account info not yet pushed by the add-in."}
    return {"status": "ok", **payload}


def get_full_outlook_state(tool_context: ToolContext) -> dict:
    """Return everything the agent knows about the user's current Outlook view.

    One-shot snapshot of compose + selected message + account + recent
    actions. Prefer the targeted tools when you only need one piece; this
    is the right call when you're planning a multi-step operation and
    want the full picture in one read.
    """
    return {
        "status": "ok",
        "compose": tool_context.state.get(COMPOSE_KEY),
        "selected": tool_context.state.get(MESSAGE_KEY),
        "account": tool_context.state.get(ACCOUNT_KEY),
        "recent_actions": list(tool_context.state.get(RECENT_ACTIONS_KEY) or [])[-10:],
    }


def get_compose_draft_snapshot(tool_context: ToolContext) -> dict:
    """Return the early compose snapshot captured by event-based activation.

    Populated by the JSON manifest's ``OnNewMessageCompose`` and
    ``OnMessageCompose`` handlers as soon as the user opens a draft —
    before the task pane is even shown. Use this when the user asks
    "what's in my current draft?" outside of a task-pane interaction
    or when ``get_current_compose`` returns ``no_compose`` (e.g. the
    task pane hasn't pushed yet but the event handler already fired).

    Returns ``{"status": "ok", "compose_type", ...compose fields}`` on
    success, otherwise ``{"status": "no_draft", "message": str}``.
    """
    payload = tool_context.state.get(COMPOSE_DRAFT_KEY)
    if not payload:
        return {
            "status": "no_draft",
            "message": "No compose-opened event has fired yet (or the add-in is sideloaded via XML manifest).",
        }
    return {"status": "ok", **payload}


outlook_context_tool_list = [
    FunctionTool(get_current_compose),
    FunctionTool(get_selected_message),
    FunctionTool(get_outlook_account),
    FunctionTool(get_full_outlook_state),
    FunctionTool(get_compose_draft_snapshot),
]
