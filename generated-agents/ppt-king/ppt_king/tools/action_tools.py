"""
PowerPoint action tools.

The agent cannot reach Office.js directly — instead it *queues* actions
into session state under ``ppt:pending_actions``. After the agent turn
finishes, the FastAPI backend reads the queue, emits an ``actions`` SSE
event, and the taskpane runs each action against PowerPoint via
``PowerPoint.run(...)`` (see addin/src/taskpane/helpers/pptContext.ts).

Every action is a plain JSON dict with a ``type`` field and a small
parameter object. New action types just need a queue helper here and an
executor in the addin.

Supported action types:
    apply_slide        replace title/bullets/notes on a slide
    insert_slide       insert a new slide after a given index
    duplicate_slide    duplicate a slide
    delete_slide       delete a slide
    move_slide         move a slide from one index to another
    set_notes          replace speaker notes on a slide
    set_shape_text     replace text inside a named shape on a slide
    replace_text       deck-wide find/replace
    add_text_box       insert a freeform text box at given coordinates
    request_refresh    ask the taskpane to push a fresh context snapshot
"""

from __future__ import annotations

from typing import Any

from google.adk.tools import FunctionTool, ToolContext

from .ppt_context import PENDING_ACTIONS_KEY


def _enqueue(tool_context: ToolContext, action: dict[str, Any]) -> dict:
    queue = list(tool_context.state.get(PENDING_ACTIONS_KEY) or [])
    queue.append(action)
    tool_context.state[PENDING_ACTIONS_KEY] = queue
    return {"status": "queued", "action": action, "queue_size": len(queue)}


def queue_apply_slide(
    tool_context: ToolContext,
    slide_index: int,
    title: str,
    bullets: list[str],
    notes: str = "",
) -> dict:
    """Queue a full title/bullets/notes replacement on a slide.

    Use this when the user has accepted a tightened or rewritten slide
    and wants it applied in place. Targets the slide at ``slide_index``
    (0-based, matches ``get_current_slide``).

    Args:
        slide_index: 0-based index of the slide to update.
        title: New title text (empty string keeps it blank).
        bullets: New bullet list — one string per bullet line.
        notes: Optional speaker notes; empty string clears them.

    Returns:
        ``{"status": "queued", "action": {...}, "queue_size": int}``.
    """
    return _enqueue(
        tool_context,
        {
            "type": "apply_slide",
            "slide_index": int(slide_index),
            "title": title or "",
            "bullets": list(bullets or []),
            "notes": notes or "",
        },
    )


def queue_insert_slide(
    tool_context: ToolContext,
    after_index: int,
    title: str,
    bullets: list[str],
    notes: str = "",
) -> dict:
    """Queue a brand-new slide inserted after ``after_index``.

    Use when expanding a deck, adding a missing agenda / CTA slide, or
    materialising one slide from a multi-slide outline.

    Args:
        after_index: 0-based index of the slide the new slide should
            appear after. Use ``-1`` to insert at the very start.
        title: Title for the new slide.
        bullets: Bullet list for the new slide.
        notes: Optional speaker notes.
    """
    return _enqueue(
        tool_context,
        {
            "type": "insert_slide",
            "after_index": int(after_index),
            "title": title or "",
            "bullets": list(bullets or []),
            "notes": notes or "",
        },
    )


def queue_duplicate_slide(tool_context: ToolContext, slide_index: int) -> dict:
    """Queue a duplicate of an existing slide right after the original.

    Useful when the user wants a sibling slide ("same layout, different
    content") or to A/B two variants of a slide.

    Args:
        slide_index: 0-based index of the slide to duplicate.
    """
    return _enqueue(
        tool_context,
        {"type": "duplicate_slide", "slide_index": int(slide_index)},
    )


def queue_delete_slide(tool_context: ToolContext, slide_index: int) -> dict:
    """Queue deletion of a slide.

    Be conservative — only call when the user explicitly asks to remove
    a slide. Never delete a slide as part of a "tighten" or "reorder"
    workflow without confirmation.

    Args:
        slide_index: 0-based index of the slide to delete.
    """
    return _enqueue(
        tool_context,
        {"type": "delete_slide", "slide_index": int(slide_index)},
    )


def queue_move_slide(
    tool_context: ToolContext, from_index: int, to_index: int
) -> dict:
    """Queue moving a slide from ``from_index`` to ``to_index``.

    Use one call per move when applying the output of
    ``suggest_reordering``. Indices are 0-based and refer to the deck as
    it currently is — when queueing multiple moves, the addin applies
    them sequentially, so think in terms of "after the previous move".

    Args:
        from_index: Current 0-based index of the slide.
        to_index: Target 0-based index.
    """
    return _enqueue(
        tool_context,
        {
            "type": "move_slide",
            "from_index": int(from_index),
            "to_index": int(to_index),
        },
    )


def queue_set_notes(
    tool_context: ToolContext, slide_index: int, notes: str
) -> dict:
    """Queue a speaker-notes update for a slide without touching the body.

    Use when the user only wants notes added or rewritten and the
    on-slide content should stay as-is.

    Args:
        slide_index: 0-based index of the slide.
        notes: Full replacement notes text. Pass an empty string to
            clear the notes.
    """
    return _enqueue(
        tool_context,
        {
            "type": "set_notes",
            "slide_index": int(slide_index),
            "notes": notes or "",
        },
    )


def queue_set_shape_text(
    tool_context: ToolContext,
    slide_index: int,
    shape_name: str,
    text: str,
) -> dict:
    """Queue a text replacement inside a named shape on a slide.

    Use when the user is pointing at a specific shape (e.g. the title
    placeholder, a callout box) and wants only that shape's text
    changed. The shape name comes from ``get_selected_shape`` /
    ``get_current_slide``.

    Args:
        slide_index: 0-based index of the slide.
        shape_name: The shape's ``name`` as reported by Office.js.
        text: New text for the shape. Use ``\\n`` to separate bullet
            lines inside body placeholders.
    """
    return _enqueue(
        tool_context,
        {
            "type": "set_shape_text",
            "slide_index": int(slide_index),
            "shape_name": shape_name or "",
            "text": text or "",
        },
    )


def queue_replace_text(
    tool_context: ToolContext,
    find: str,
    replace: str,
    scope: str = "deck",
    slide_index: int = -1,
    match_case: bool = False,
) -> dict:
    """Queue a find/replace across the deck or a single slide.

    Use for "rename our product X to Y everywhere", "fix the typo on
    slide 4", or "swap the date across the deck".

    Args:
        find: The string to look for. Must be non-empty.
        replace: The replacement string. May be empty to delete matches.
        scope: ``"deck"`` to apply across every slide (default),
            ``"slide"`` to apply only to ``slide_index``.
        slide_index: Required if ``scope == "slide"``. 0-based.
        match_case: When True, the match is case-sensitive.
    """
    if not find:
        return {"status": "error", "message": "find string must not be empty"}
    if scope not in ("deck", "slide"):
        return {"status": "error", "message": "scope must be 'deck' or 'slide'"}
    return _enqueue(
        tool_context,
        {
            "type": "replace_text",
            "find": find,
            "replace": replace or "",
            "scope": scope,
            "slide_index": int(slide_index),
            "match_case": bool(match_case),
        },
    )


def queue_add_text_box(
    tool_context: ToolContext,
    slide_index: int,
    text: str,
    left: float = 50.0,
    top: float = 50.0,
    width: float = 400.0,
    height: float = 80.0,
) -> dict:
    """Queue insertion of a freeform text box on a slide.

    Coordinates are in points (1 point = 1/72 inch). A standard
    16:9 slide is roughly 960 x 540 points. Use this when the user asks
    for an annotation, callout, or extra label that doesn't fit a
    placeholder.

    Args:
        slide_index: 0-based index of the slide.
        text: Text content for the new box.
        left: X position in points (default 50).
        top: Y position in points (default 50).
        width: Width in points (default 400).
        height: Height in points (default 80).
    """
    return _enqueue(
        tool_context,
        {
            "type": "add_text_box",
            "slide_index": int(slide_index),
            "text": text or "",
            "left": float(left),
            "top": float(top),
            "width": float(width),
            "height": float(height),
        },
    )


action_tool_list = [
    FunctionTool(queue_apply_slide),
    FunctionTool(queue_insert_slide),
    FunctionTool(queue_duplicate_slide),
    FunctionTool(queue_delete_slide),
    FunctionTool(queue_move_slide),
    FunctionTool(queue_set_notes),
    FunctionTool(queue_set_shape_text),
    FunctionTool(queue_replace_text),
    FunctionTool(queue_add_text_box),
]
