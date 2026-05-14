"""
Word action tools.

The agent doesn't talk to Word directly — Office.js runs in the
taskpane. To "do something in the document" the agent enqueues a
structured action via one of the tools below; the backend includes the
action queue in the final response of ``/api/word/chat[/stream]``; the
add-in drains the queue through ``executeWordAction`` in
``addin/src/taskpane/helpers/wordActions.ts``.

Every action is a plain dict ``{"kind": str, "params": dict,
"description": str}``. Keep ``kind`` values stable — the add-in
dispatches on them.

State key:
    word:pending_actions   list[dict]  — drained on every turn boundary.
"""

from __future__ import annotations

from typing import Any

from google.adk.tools import FunctionTool, ToolContext

PENDING_ACTIONS_KEY = "word:pending_actions"

_KNOWN_LOCATIONS = {"selection", "cursor", "start", "end", "before", "after"}
_KNOWN_STYLES = {
    "Normal", "Title", "Subtitle", "Heading1", "Heading2", "Heading3",
    "Heading4", "Heading5", "Heading6", "Quote", "IntenseQuote",
    "ListParagraph", "Emphasis", "Strong", "NoSpacing",
}


def _queue(tool_context: ToolContext, kind: str, params: dict, description: str) -> dict:
    pending: list[dict] = list(tool_context.state.get(PENDING_ACTIONS_KEY) or [])
    action = {"kind": kind, "params": params, "description": description}
    pending.append(action)
    tool_context.state[PENDING_ACTIONS_KEY] = pending
    return {"status": "queued", "kind": kind, "description": description, "queue_length": len(pending)}


def insert_text(
    text: str,
    location: str = "selection",
    tool_context: ToolContext = None,  # type: ignore[assignment]
) -> dict:
    """Queue an action to insert plain text into the document.

    Use this when the user asks to "add", "insert", "write here", or
    "append" content. For replacing the current selection prefer
    ``replace_selection`` so the intent is unambiguous.

    Args:
        text: The plain text to insert. Newlines become paragraph breaks
            in Word.
        location: Where to put it. One of ``"selection"`` (default —
            inserts at the caret or replaces the current selection),
            ``"start"`` (top of document) or ``"end"`` (bottom).

    Returns:
        ``{"status": "queued", "kind": "insert_text", ...}``.
    """
    if not text:
        return {"status": "error", "message": "Empty text — nothing to insert."}
    loc = (location or "selection").lower()
    if loc not in {"selection", "cursor", "start", "end"}:
        return {"status": "error", "message": f"Unknown location {location!r}. Use selection/start/end."}
    return _queue(
        tool_context,
        "insert_text",
        {"text": text, "location": loc},
        f"Insert {len(text)} chars at {loc}",
    )


def replace_selection(text: str, tool_context: ToolContext = None) -> dict:  # type: ignore[assignment]
    """Queue an action to replace the user's current selection with text.

    Always pair with ``get_current_selection`` first so the rewrite is
    grounded in what's actually selected. If nothing is selected, the
    add-in falls back to inserting at the caret.

    Args:
        text: The plain text to write in place of the selection.

    Returns:
        ``{"status": "queued", "kind": "replace_selection", ...}``.
    """
    if not text:
        return {"status": "error", "message": "Empty replacement text."}
    return _queue(
        tool_context,
        "replace_selection",
        {"text": text},
        f"Replace selection with {len(text)} chars",
    )


def apply_formatting(
    bold: bool | None = None,
    italic: bool | None = None,
    underline: bool | None = None,
    style: str | None = None,
    target: str = "selection",
    tool_context: ToolContext = None,  # type: ignore[assignment]
) -> dict:
    """Queue a formatting action on the current selection or a paragraph.

    Pass any combination of ``bold`` / ``italic`` / ``underline``
    booleans, and optionally a Word paragraph ``style`` (``"Heading1"``,
    ``"Heading2"``, ``"Title"``, ``"Quote"``, ``"Normal"``, etc.). At
    least one parameter must be set.

    Args:
        bold: True to bold, False to un-bold, None to leave alone.
        italic: True/False/None — same as bold.
        underline: True/False/None — same as bold.
        style: Word paragraph style name (e.g. ``"Heading2"``).
        target: ``"selection"`` (default) or ``"paragraph"`` for the
            paragraph containing the caret.

    Returns:
        ``{"status": "queued", "kind": "apply_formatting", ...}``.
    """
    params: dict[str, Any] = {}
    if bold is not None:
        params["bold"] = bool(bold)
    if italic is not None:
        params["italic"] = bool(italic)
    if underline is not None:
        params["underline"] = bool(underline)
    if style:
        if style not in _KNOWN_STYLES:
            return {
                "status": "error",
                "message": (
                    f"Unknown style {style!r}. Use one of: {sorted(_KNOWN_STYLES)}."
                ),
            }
        params["style"] = style
    if not params:
        return {"status": "error", "message": "Nothing to apply — pass bold/italic/underline/style."}
    params["target"] = target if target in {"selection", "paragraph"} else "selection"
    return _queue(
        tool_context,
        "apply_formatting",
        params,
        "Format " + params["target"] + ": " + ", ".join(f"{k}={v}" for k, v in params.items() if k != "target"),
    )


def insert_heading(text: str, level: int = 2, tool_context: ToolContext = None) -> dict:  # type: ignore[assignment]
    """Queue an action to insert a heading paragraph at the caret.

    Args:
        text: Heading text (no trailing newline needed).
        level: Heading level 1–6. Defaults to 2.

    Returns:
        Queued action dict.
    """
    if not text or not text.strip():
        return {"status": "error", "message": "Empty heading text."}
    if not 1 <= level <= 6:
        return {"status": "error", "message": "level must be 1..6"}
    return _queue(
        tool_context,
        "insert_heading",
        {"text": text.strip(), "level": level},
        f"Insert H{level}: {text.strip()[:60]}",
    )


def insert_table(
    rows: list[list[str]],
    has_header: bool = True,
    tool_context: ToolContext = None,  # type: ignore[assignment]
) -> dict:
    """Queue an action to insert a Word table at the caret.

    Args:
        rows: 2D list of cell strings, ``rows[r][c]``. All rows must
            have the same length.
        has_header: Treat row 0 as a header row (bold). Default True.

    Returns:
        Queued action dict.
    """
    if not rows or not rows[0]:
        return {"status": "error", "message": "Table must have at least one row and one column."}
    width = len(rows[0])
    if any(len(r) != width for r in rows):
        return {"status": "error", "message": "All table rows must have the same column count."}
    return _queue(
        tool_context,
        "insert_table",
        {"rows": rows, "has_header": bool(has_header)},
        f"Insert table {len(rows)}×{width}",
    )


def insert_comment(text: str, on: str = "selection", tool_context: ToolContext = None) -> dict:  # type: ignore[assignment]
    """Queue an action to attach a comment to the current selection.

    Use this to leave a note rather than mutating the document — e.g.
    flagging an unclear sentence, suggesting an edit the user can accept
    in track-changes.

    Args:
        text: Comment text.
        on: ``"selection"`` (default) or ``"paragraph"``.

    Returns:
        Queued action dict.
    """
    if not text or not text.strip():
        return {"status": "error", "message": "Empty comment text."}
    return _queue(
        tool_context,
        "insert_comment",
        {"text": text.strip(), "on": on if on in {"selection", "paragraph"} else "selection"},
        f"Comment: {text.strip()[:60]}",
    )


def find_and_replace(
    find: str,
    replace: str,
    match_case: bool = False,
    whole_word: bool = False,
    tool_context: ToolContext = None,  # type: ignore[assignment]
) -> dict:
    """Queue a find-and-replace across the whole document body.

    Args:
        find: The string to search for.
        replace: The replacement string. Pass ``""`` to delete matches.
        match_case: Case-sensitive search. Default False.
        whole_word: Match whole words only. Default False.

    Returns:
        Queued action dict.
    """
    if not find:
        return {"status": "error", "message": "Empty find string."}
    return _queue(
        tool_context,
        "find_and_replace",
        {
            "find": find,
            "replace": replace,
            "match_case": bool(match_case),
            "whole_word": bool(whole_word),
        },
        f"Replace {find!r} → {replace!r}",
    )


def navigate_to_heading(heading_text: str, tool_context: ToolContext = None) -> dict:  # type: ignore[assignment]
    """Queue an action to scroll to (and select) a heading by text match.

    Uses substring match against the document's headings list — the
    first heading whose text contains ``heading_text`` (case-insensitive)
    wins.

    Args:
        heading_text: Heading text or fragment to jump to.

    Returns:
        Queued action dict.
    """
    if not heading_text or not heading_text.strip():
        return {"status": "error", "message": "Empty heading text."}
    return _queue(
        tool_context,
        "navigate_to_heading",
        {"heading_text": heading_text.strip()},
        f"Go to heading: {heading_text.strip()[:60]}",
    )


def delete_selection(tool_context: ToolContext = None) -> dict:  # type: ignore[assignment]
    """Queue an action to delete the user's current selection.

    Returns:
        Queued action dict.
    """
    return _queue(tool_context, "delete_selection", {}, "Delete selection")


word_action_tool_list = [
    FunctionTool(insert_text),
    FunctionTool(replace_selection),
    FunctionTool(apply_formatting),
    FunctionTool(insert_heading),
    FunctionTool(insert_table),
    FunctionTool(insert_comment),
    FunctionTool(find_and_replace),
    FunctionTool(navigate_to_heading),
    FunctionTool(delete_selection),
]
