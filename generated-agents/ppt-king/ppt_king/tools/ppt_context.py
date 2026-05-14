"""
PowerPoint context tools.

The Office.js taskpane pushes the user's current PowerPoint state into
ADK session state via ``POST /api/ppt/context`` (see backend/main.py).
These tools surface that state to the agent so it can act on "this slide",
"this shape", or "the deck I have open" without re-asking the user.

State keys (per session):
    ppt:current_slide      {index, slide_id, title, bullets, notes,
                            layout_name, selected_shapes: [...],
                            shape_count}
    ppt:deck_outline       {slide_count, slides: [{index, slide_id, title,
                            bullet_count, has_notes}]}
    ppt:recent_edits       list of recent agent-applied edits (rolling 10)
    ppt:pending_actions    queue of actions the agent wants the addin to
                           execute on the current turn

The agent calls ``get_current_slide`` / ``get_selected_shape`` /
``get_deck_outline`` to *read* state and ``request_context_refresh`` to
ask the addin to push a new snapshot mid-turn.
"""

from __future__ import annotations

from typing import Any

from google.adk.tools import FunctionTool, ToolContext

CURRENT_SLIDE_KEY = "ppt:current_slide"
DECK_OUTLINE_KEY = "ppt:deck_outline"
RECENT_EDITS_KEY = "ppt:recent_edits"
PENDING_ACTIONS_KEY = "ppt:pending_actions"


def _get(tool_context: ToolContext, key: str) -> Any:
    return tool_context.state.get(key)


def get_current_slide(tool_context: ToolContext) -> dict:
    """Return the slide the user has selected in PowerPoint.

    Call this BEFORE tightening, rewriting, or adding speaker notes to
    "this slide", "the slide I'm on", or any deictic reference to the
    active slide. Includes the slide title, bullets, notes, layout name,
    and a summary of the currently-selected shapes (with text, position,
    and size) so you can act on a specific shape if the user is pointing
    at one.

    Returns:
        ``{"status": "ok", "index": int, "slide_id": str, "title": str,
        "bullets": list[str], "notes": str, "layout_name": str,
        "shape_count": int, "selected_shapes": [
            {"name": str, "type": str, "text": str,
             "left": float, "top": float, "width": float, "height": float,
             "is_placeholder": bool}, ...
        ]}`` when a slide is selected, otherwise
        ``{"status": "no_slide", "message": str}``.
    """
    payload = _get(tool_context, CURRENT_SLIDE_KEY)
    if not payload:
        return {
            "status": "no_slide",
            "message": (
                "No slide is selected. Ask the user to click a slide, or "
                "work from the deck outline instead."
            ),
        }
    return {"status": "ok", **payload}


def get_selected_shape(tool_context: ToolContext) -> dict:
    """Return the shape(s) the user has currently selected on the slide.

    Use this when the user says "this textbox", "this title", "the shape
    I just clicked", "fix the text in this box", etc. Returns the first
    selected shape as the primary payload plus the full list when the
    user has multi-selected.

    Returns:
        ``{"status": "ok", "primary": {"name": str, "type": str, "text":
        str, "left": float, "top": float, "width": float, "height":
        float, "is_placeholder": bool}, "all": [...], "count": int}``
        when at least one shape is selected on the current slide,
        otherwise ``{"status": "no_shape", "message": str}``.
    """
    slide = _get(tool_context, CURRENT_SLIDE_KEY) or {}
    shapes = slide.get("selected_shapes") or []
    if not shapes:
        return {
            "status": "no_shape",
            "message": (
                "No shape is selected. Ask the user to click the shape "
                "they want to edit."
            ),
        }
    return {
        "status": "ok",
        "primary": shapes[0],
        "all": shapes,
        "count": len(shapes),
    }


def get_deck_outline(tool_context: ToolContext) -> dict:
    """Return the outline of the whole deck the user has open.

    Use this for structure / reordering work, "what's missing?", or any
    question that needs the whole arc rather than a single slide.

    Returns:
        ``{"status": "ok", "slide_count": int, "slides": [{"index": int,
        "slide_id": str, "title": str, "bullet_count": int,
        "has_notes": bool}, ...]}`` when a deck is open, otherwise
        ``{"status": "no_deck", "message": str}``.
    """
    payload = _get(tool_context, DECK_OUTLINE_KEY)
    if not payload:
        return {
            "status": "no_deck",
            "message": (
                "No deck outline available. Ask the user to open a "
                "presentation, then try again."
            ),
        }
    return {"status": "ok", **payload}


def get_recent_edits(tool_context: ToolContext) -> dict:
    """Return the recent edits the agent applied to the deck this session.

    Useful for "undo my last change", "what did you just do?", or
    avoiding repeating the same edit. The list is bounded to the most
    recent 10 actions.

    Returns:
        ``{"status": "ok", "edits": [
            {"action": str, "slide_index": int, "summary": str,
             "timestamp": str}, ...]}``.
    """
    edits = _get(tool_context, RECENT_EDITS_KEY) or []
    return {"status": "ok", "edits": list(edits)}


def request_context_refresh(tool_context: ToolContext) -> dict:
    """Ask the taskpane to push a fresh PowerPoint snapshot.

    Call this when the agent has just queued actions that mutated the
    deck and needs to read the new state before deciding the next move
    (e.g. after inserting slides, before reordering). The addin watches
    for the ``request_refresh`` flag and re-snapshots on the next tick.
    The refreshed snapshot lands in session state before the next user
    turn — within the same turn the values you have are still the
    pre-mutation values.

    Returns:
        ``{"status": "queued"}``.
    """
    queue = list(_get(tool_context, PENDING_ACTIONS_KEY) or [])
    queue.append({"type": "request_refresh"})
    tool_context.state[PENDING_ACTIONS_KEY] = queue
    return {"status": "queued"}


ppt_context_tool_list = [
    FunctionTool(get_current_slide),
    FunctionTool(get_selected_shape),
    FunctionTool(get_deck_outline),
    FunctionTool(get_recent_edits),
    FunctionTool(request_context_refresh),
]
