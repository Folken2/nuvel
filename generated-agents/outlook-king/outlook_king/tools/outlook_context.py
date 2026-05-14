"""
Outlook context tools.

The Office.js taskpane pushes the user's current mailbox state into ADK
session state via ``POST /api/outlook/context`` (see backend/main.py).
These tools surface that state to the agent so it can act on "this email"
or "my draft" without re-asking the user.

State keys (per session):
    outlook:current_compose   {body, subject, to, cc, mode, conversation_id}
    outlook:selected_message  {id, subject, from, to, body, conversation_id,
                               received, has_attachments}
"""

from __future__ import annotations

from google.adk.tools import FunctionTool, ToolContext

COMPOSE_KEY = "outlook:current_compose"
MESSAGE_KEY = "outlook:selected_message"


def get_current_compose(tool_context: ToolContext) -> dict:
    """Return the user's currently-open compose window.

    Call this BEFORE drafting or coaching whenever the user references "my
    draft", "fix this", "make it shorter", or any deictic reference to what
    they are writing right now. The taskpane pushes a fresh snapshot into
    session state every time the compose body changes.

    Returns:
        ``{"status": "ok", "body": str, "subject": str, "to": list[str],
        "cc": list[str], "mode": "newMail" | "reply" | "forward",
        "conversation_id": str | None}`` when a compose is active, otherwise
        ``{"status": "no_compose", "message": str}``.
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

    Use this when the user references "this email", "the message I'm
    reading", or asks for a reply/summary of what they are looking at.
    For cross-folder or historical search, use the Composio ``OUTLOOK_*``
    tools instead — they hit the full mailbox.

    Returns:
        Message payload or ``{"status": "no_selection", "message": str}``.
    """
    payload = tool_context.state.get(MESSAGE_KEY)
    if not payload:
        return {
            "status": "no_selection",
            "message": "No message selected. Use Composio's Outlook search tools to find one.",
        }
    return {"status": "ok", **payload}


outlook_context_tool_list = [
    FunctionTool(get_current_compose),
    FunctionTool(get_selected_message),
]
