"""
Word context tools.

The Office.js taskpane pushes the user's current Word state into ADK
session state via ``POST /api/word/context`` (see backend/main.py).
These tools surface that state to the agent so it can act on "the text
I selected" or "this section" without re-asking the user.

State keys (per session):
    word:current_selection  {text, paragraph_count, word_count, style_name}
    word:full_document      {text, paragraph_count, word_count, style_name}
"""

from __future__ import annotations

from google.adk.tools import FunctionTool, ToolContext

SELECTION_KEY = "word:current_selection"
DOCUMENT_KEY = "word:full_document"


def get_current_selection(tool_context: ToolContext) -> dict:
    """Return the text the user has selected in the document.

    Call this BEFORE rewriting whenever the user says "rewrite this",
    "fix this", "make it tighter", or any deictic reference to "this".
    The taskpane pushes a fresh snapshot of the selection every time it
    changes (and whenever the user issues a chat turn).

    Returns:
        ``{"status": "ok", "text": str, "paragraph_count": int,
        "word_count": int, "style_name": str | None}`` when a selection
        is active, otherwise ``{"status": "no_selection", "message": str}``.
    """
    payload = tool_context.state.get(SELECTION_KEY)
    if not payload or not (payload.get("text") or "").strip():
        return {
            "status": "no_selection",
            "message": "No text is selected. Ask the user to select the passage they want to edit.",
        }
    return {"status": "ok", **payload}


def get_full_document(tool_context: ToolContext) -> dict:
    """Return the full body of the user's current Word document.

    Use this when the user references "the document", "this report",
    "what I have so far", or when drafting a new section that must
    match the surrounding text's register and terminology. For
    rewriting a localized passage, prefer ``get_current_selection``;
    pulling the whole document is heavier and noisier.

    Returns:
        Document payload or ``{"status": "no_document", "message": str}``.
    """
    payload = tool_context.state.get(DOCUMENT_KEY)
    if not payload or not (payload.get("text") or "").strip():
        return {
            "status": "no_document",
            "message": "No document context. The taskpane should push it on open; ask the user to retry.",
        }
    return {"status": "ok", **payload}


word_context_tool_list = [
    FunctionTool(get_current_selection),
    FunctionTool(get_full_document),
]
