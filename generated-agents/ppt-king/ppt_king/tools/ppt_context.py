"""
PowerPoint context tools.

The Office.js taskpane pushes the user's current PowerPoint state into
ADK session state via ``POST /api/ppt/context`` (see backend/main.py).
These tools surface that state to the agent so it can act on "this slide"
or "the deck I have open" without re-asking the user.

State keys (per session):
    ppt:current_slide   {index, title, bullets, notes, layout_name}
    ppt:deck_outline    {slide_count, slides: [{index, title, bullet_count,
                         has_notes}]}
"""

from __future__ import annotations

from google.adk.tools import FunctionTool, ToolContext

CURRENT_SLIDE_KEY = "ppt:current_slide"
DECK_OUTLINE_KEY = "ppt:deck_outline"


def get_current_slide(tool_context: ToolContext) -> dict:
    """Return the slide the user has selected in PowerPoint.

    Call this BEFORE tightening, rewriting, or adding speaker notes to
    "this slide", "the slide I'm on", or any deictic reference to the
    active slide. The taskpane pushes a fresh snapshot into session state
    whenever the selection changes.

    Returns:
        ``{"status": "ok", "index": int, "title": str, "bullets": list[str],
        "notes": str, "layout_name": str}`` when a slide is selected,
        otherwise ``{"status": "no_slide", "message": str}``.
    """
    payload = tool_context.state.get(CURRENT_SLIDE_KEY)
    if not payload:
        return {
            "status": "no_slide",
            "message": (
                "No slide is selected. Ask the user to click a slide, or "
                "work from the deck outline instead."
            ),
        }
    return {"status": "ok", **payload}


def get_deck_outline(tool_context: ToolContext) -> dict:
    """Return the outline of the whole deck the user has open.

    Use this for structure / reordering work, "what's missing?", or any
    question that needs the whole arc rather than a single slide. The
    outline is a list of every slide's title plus a bullet count and a
    has-notes flag — enough to reason over flow without dragging full
    bullet text into the prompt.

    Returns:
        ``{"status": "ok", "slide_count": int, "slides":
        [{"index": int, "title": str, "bullet_count": int,
        "has_notes": bool}, ...]}`` when a deck is open, otherwise
        ``{"status": "no_deck", "message": str}``.
    """
    payload = tool_context.state.get(DECK_OUTLINE_KEY)
    if not payload:
        return {
            "status": "no_deck",
            "message": (
                "No deck outline available. Ask the user to open a "
                "presentation, then try again."
            ),
        }
    return {"status": "ok", **payload}


ppt_context_tool_list = [
    FunctionTool(get_current_slide),
    FunctionTool(get_deck_outline),
]
