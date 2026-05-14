"""
Word context tools.

The Office.js taskpane pushes the user's current Word state into ADK
session state via ``POST /api/word/context`` (see backend/main.py).
These tools surface that state to the agent so it can act on "the text
I selected", "this section", "the heading right above me" without
re-asking the user.

State keys (per session):
    word:current_selection   {text, paragraph_count, word_count, style_name,
                              start_offset, end_offset, is_empty, in_table,
                              in_list, hyperlink, parent_style}
    word:full_document       {text, paragraph_count, word_count, title}
    word:surrounding         {paragraph_before, paragraph_at, paragraph_after,
                              preceding_heading}
    word:headings            list[{text, level, index}]
    word:document_meta       {title, page_count, language, track_changes,
                              comments_count}
    word:recent_edits        list[{kind, summary, at}]
    word:pending_actions     list[dict]  — queued for the add-in to execute

The agent can also call ``request_context_refresh`` to ask the add-in
to push a fresh snapshot mid-turn (e.g. after enqueueing an action it
wants to verify). Refresh requests surface to the add-in via the
pending-actions queue.
"""

from __future__ import annotations

from google.adk.tools import FunctionTool, ToolContext

SELECTION_KEY = "word:current_selection"
DOCUMENT_KEY = "word:full_document"
SURROUNDING_KEY = "word:surrounding"
HEADINGS_KEY = "word:headings"
DOC_META_KEY = "word:document_meta"
RECENT_EDITS_KEY = "word:recent_edits"

# Re-exported here so backend/main.py and tests have one import home.
PENDING_ACTIONS_KEY = "word:pending_actions"


def get_current_selection(tool_context: ToolContext) -> dict:
    """Return the text the user has selected in the document.

    Call this BEFORE rewriting whenever the user says "rewrite this",
    "fix this", "make it tighter", or any deictic reference to "this".
    The taskpane pushes a fresh snapshot of the selection every time it
    changes (and whenever the user issues a chat turn).

    Returns:
        ``{"status": "ok", "text": str, "paragraph_count": int,
        "word_count": int, "style_name": str | None, "is_empty": bool,
        "in_table": bool, "in_list": bool, "hyperlink": str | None,
        "start_offset": int, "end_offset": int}`` when a selection is
        active, otherwise ``{"status": "no_selection", "message": str}``.
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


def get_surrounding_context(tool_context: ToolContext) -> dict:
    """Return the paragraphs immediately around the caret/selection.

    Use this for localized "continue from here", "match the paragraph
    above", or "what comes next" prompts where pulling the entire
    document is overkill. Returns the paragraph the caret is in plus
    the ones immediately before and after, and the closest preceding
    heading (so the agent knows which section it's in).

    Returns:
        ``{"status": "ok", "paragraph_before": str, "paragraph_at": str,
        "paragraph_after": str, "preceding_heading": {text, level} | None}``
        or ``{"status": "no_context", "message": str}``.
    """
    payload = tool_context.state.get(SURROUNDING_KEY)
    if not payload:
        return {
            "status": "no_context",
            "message": "No surrounding context yet. Tell the user to place the cursor in the document.",
        }
    return {"status": "ok", **payload}


def get_document_outline(tool_context: ToolContext) -> dict:
    """Return the document's heading outline (table of contents).

    Each heading is ``{"text": str, "level": int 1..6, "index": int}``.
    ``index`` is the paragraph index in the document body — pass that
    string to ``navigate_to_heading`` to jump there.

    Returns:
        ``{"status": "ok", "headings": list[dict], "count": int}`` or
        ``{"status": "no_outline", "message": str}`` if the document
        has no heading-styled paragraphs.
    """
    headings = tool_context.state.get(HEADINGS_KEY)
    if not headings:
        return {
            "status": "no_outline",
            "message": "Document has no headings. Use insert_heading to start one.",
        }
    return {"status": "ok", "headings": headings, "count": len(headings)}


def get_document_meta(tool_context: ToolContext) -> dict:
    """Return document-level metadata: title, page count, language, etc.

    Useful for tone calibration ("this is a legal brief, raise the
    register") and for honoring the document's tracked-changes mode (if
    track_changes is on, the agent should prefer comments over silent
    edits).

    Returns:
        ``{"status": "ok", "title": str, "page_count": int,
        "language": str, "track_changes": bool, "comments_count": int}``
        or ``{"status": "no_meta", "message": str}``.
    """
    meta = tool_context.state.get(DOC_META_KEY)
    if not meta:
        return {
            "status": "no_meta",
            "message": "No document metadata yet — the taskpane hasn't pushed it.",
        }
    return {"status": "ok", **meta}


def get_recent_edits(tool_context: ToolContext) -> dict:
    """Return a short log of the most recent edits the agent has made.

    The add-in appends an entry each time it executes a queued action
    (insert / replace / format / comment / etc.). Use this to avoid
    re-doing an action the user just undid, or to phrase a follow-up
    response (e.g. "I already inserted the heading above").

    Returns:
        ``{"status": "ok", "edits": list[dict]}`` — newest last.
    """
    edits = tool_context.state.get(RECENT_EDITS_KEY) or []
    return {"status": "ok", "edits": edits[-10:], "count": len(edits)}


def request_context_refresh(reason: str = "", tool_context: ToolContext = None) -> dict:  # type: ignore[assignment]
    """Ask the add-in to push a fresh Word snapshot before the next step.

    Queues a ``refresh_context`` action that the add-in handles by
    re-running ``snapshotCurrentContext`` and posting back to
    ``/api/word/context``. The next turn will see fresh state.

    Args:
        reason: Optional short note ("after inserting heading, need to
            re-check outline") for logging.

    Returns:
        ``{"status": "queued", "kind": "refresh_context"}``.
    """
    pending: list[dict] = list(tool_context.state.get(PENDING_ACTIONS_KEY) or [])
    pending.append(
        {
            "kind": "refresh_context",
            "params": {"reason": reason or ""},
            "description": f"Refresh context: {reason or 'on-demand'}",
        }
    )
    tool_context.state[PENDING_ACTIONS_KEY] = pending
    return {"status": "queued", "kind": "refresh_context"}


word_context_tool_list = [
    FunctionTool(get_current_selection),
    FunctionTool(get_full_document),
    FunctionTool(get_surrounding_context),
    FunctionTool(get_document_outline),
    FunctionTool(get_document_meta),
    FunctionTool(get_recent_edits),
    FunctionTool(request_context_refresh),
]
