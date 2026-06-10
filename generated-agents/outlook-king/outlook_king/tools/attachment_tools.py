"""
Attachment tools — let the agent download and read attachment content.

The agent runs in the FastAPI backend; attachment bytes live in the
user's Outlook client. The flow is the same two-step indirection as the
other Outlook actions:

  1. ``fetch_attachment`` queues a ``fetch_attachment`` action. After the
     turn ends, the add-in downloads the content via Office.js
     ``getAttachmentContentAsync`` (Mailbox 1.8+) and POSTs it to
     ``/api/outlook/attachment-content``.
  2. The backend stores the raw bytes as an ADK artifact
     (``attachment:<name>``), extracts text for PDF / Excel / CSV / text
     files into a companion artifact (``attachment_text:<name>``), and
     records an index entry in session state under
     ``outlook:fetched_attachments``.
  3. On the next turn the agent reads it. Two readers:
     - ``load_artifacts`` on ``attachment:<name>`` sends the ORIGINAL
       file to the model (ADK's LiteLLM bridge converts PDFs and images
       into provider file/image parts) — full layout, tables, charts.
     - ``read_attachment`` returns extracted plain text, paged — cheap
       for long documents, and the only reader for Excel/CSV.

Attachment ids come from ``get_selected_message`` /
``get_current_compose`` — each attachment entry carries ``id``, ``name``,
``size`` and ``content_type``.
"""

from __future__ import annotations

import re

from google.adk.tools import FunctionTool, ToolContext

from .outlook_actions import _queue

FETCHED_ATTACHMENTS_KEY = "outlook:fetched_attachments"

# Office.js getAttachmentContentAsync caps file attachments at 25 MB
# pre-encoding; we stop earlier to keep upload + artifact sizes sane.
MAX_FETCH_BYTES = 20 * 1024 * 1024

# read_attachment paging: small default keeps tool responses cheap in
# context (search_attachment finds the right offset first); hard max for
# when the agent explicitly asks for more.
DEFAULT_READ_CHARS = 6_000
MAX_READ_CHARS = 20_000

MAX_SEARCH_HITS = 20
MAX_SNIPPET_CONTEXT = 2_000


def fetch_attachment(tool_context: ToolContext, attachment_id: str, name: str) -> dict:
    """Download an attachment from the current Outlook item so you can read it.

    Queues a download in the add-in; the content arrives AFTER this turn
    ends. Tell the user you're fetching it, end the turn, then on the
    next turn call ``load_artifacts`` on ``attachment:<name>`` to view
    the original (best for PDFs with tables/layout, and for images), or
    ``read_attachment`` for paged plain text (Excel/CSV, long docs).

    Get ``attachment_id`` and ``name`` from the ``attachments`` list in
    ``get_selected_message`` or ``get_current_compose``. Supported:
    PDF, Excel (.xlsx), CSV/text, images. Not supported: cloud/OneDrive
    links and legacy .xls files.

    Args:
        attachment_id: The attachment's ``id`` from the context snapshot.
        name: The attachment filename (used to store and later read it).
    """
    if not attachment_id or not name:
        return {
            "status": "error",
            "message": "attachment_id and name are required — read them from "
            "get_selected_message / get_current_compose first.",
        }
    fetched = tool_context.state.get(FETCHED_ATTACHMENTS_KEY) or {}
    if name in fetched:
        return {
            "status": "already_fetched",
            "name": name,
            "message": f"'{name}' is already downloaded — call read_attachment(name='{name}').",
        }
    return _queue(
        tool_context,
        {
            "type": "fetch_attachment",
            "params": {"attachment_id": attachment_id, "name": name},
            "requires_mode": "any",
            "description": f"Download attachment: {name}",
        },
    )


def list_fetched_attachments(tool_context: ToolContext) -> dict:
    """List attachments already downloaded into this session.

    Each entry shows ``kind`` ("text" means read_attachment works;
    "image" means use load_artifacts) plus size and extraction status.
    """
    fetched = tool_context.state.get(FETCHED_ATTACHMENTS_KEY) or {}
    return {"status": "ok", "count": len(fetched), "attachments": list(fetched.values())}


async def _load_text_entry(tool_context: ToolContext, name: str) -> tuple[dict | None, str | None, dict | None]:
    """Resolve an index entry + its extracted text, or a structured error.

    Returns ``(entry, text, error_response)`` — exactly one of
    ``text`` / ``error_response`` is set.
    """
    fetched = tool_context.state.get(FETCHED_ATTACHMENTS_KEY) or {}
    entry = fetched.get(name)
    if entry is None:
        available = ", ".join(fetched.keys()) or "(none)"
        return None, None, {
            "status": "not_fetched",
            "message": (
                f"'{name}' hasn't been downloaded. Call fetch_attachment first "
                f"(this turn must end before the content arrives). Already fetched: {available}"
            ),
        }
    if entry.get("kind") == "image":
        return entry, None, {
            "status": "is_image",
            "artifact": entry.get("artifact"),
            "message": (
                f"'{name}' is an image — use the load_artifacts tool with "
                f"artifact name '{entry.get('artifact')}' to view it."
            ),
        }
    if not entry.get("text_artifact"):
        return entry, None, {
            "status": "no_text",
            "message": entry.get("extraction_error")
            or f"No text could be extracted from '{name}'.",
        }
    part = await tool_context.load_artifact(entry["text_artifact"])
    text = getattr(part, "text", None) if part is not None else None
    if not text:
        return entry, None, {
            "status": "error",
            "message": f"Extracted text for '{name}' is missing — fetch it again.",
        }
    return entry, text, None


async def search_attachment(
    tool_context: ToolContext,
    name: str,
    query: str,
    max_hits: int = 5,
    context_chars: int = 300,
) -> dict:
    """Search inside a fetched attachment and return matching snippets.

    ALWAYS prefer this over reading a long document front-to-back — it
    finds the relevant passage without loading the whole file into
    context. Each hit comes with its character ``offset``; follow up
    with ``read_attachment(name, offset=<hit offset>)`` to read more
    around a hit.

    Args:
        name: The attachment filename used in ``fetch_attachment``.
        query: Keywords or a regular expression (case-insensitive). If
            the regex is invalid it is searched as a literal string.
        max_hits: Max snippets to return (default 5).
        context_chars: Characters of context around each match (default 300).
    """
    if not query or not query.strip():
        return {"status": "error", "message": "query is required."}
    entry, text, error = await _load_text_entry(tool_context, name)
    if error is not None:
        return error

    try:
        pattern = re.compile(query, re.IGNORECASE)
    except re.error:
        pattern = re.compile(re.escape(query), re.IGNORECASE)

    max_hits = max(1, min(int(max_hits), MAX_SEARCH_HITS))
    context_chars = max(50, min(int(context_chars), MAX_SNIPPET_CONTEXT))

    hits = []
    for match in pattern.finditer(text):
        start = max(0, match.start() - context_chars)
        end = min(len(text), match.end() + context_chars)
        hits.append({"offset": match.start(), "snippet": text[start:end]})
        if len(hits) >= max_hits:
            break

    return {
        "status": "ok",
        "name": name,
        "query": query,
        "total_chars": len(text),
        "hit_count": len(hits),
        "hits": hits,
        "note": (
            "Use read_attachment(name, offset=<hit offset>) to read more around a hit."
            if hits
            else "No matches — try different keywords, or read_attachment from offset 0."
        ),
    }


async def read_attachment(
    tool_context: ToolContext, name: str, offset: int = 0, limit: int = DEFAULT_READ_CHARS
) -> dict:
    """Read the extracted plain text of a previously fetched attachment.

    Works for PDF, Excel (.xlsx), CSV and plain-text attachments after
    ``fetch_attachment`` completed. For long documents, FIRST call
    ``search_attachment`` to locate the passage you need, then read from
    that offset — don't page through the whole file. If ``has_more`` is
    true, continue from ``next_offset``.

    Plain text loses document structure. For PDFs where layout matters
    (tables, charts, forms, scans), prefer the ``load_artifacts`` tool
    with the ``raw_artifact`` name from this response — it sends you the
    original file. Images are only viewable that way.

    Args:
        name: The attachment filename used in ``fetch_attachment``.
        offset: Character offset to continue reading from (default 0).
        limit: Max characters to return (default 6000, max 20000).
    """
    entry, text, error = await _load_text_entry(tool_context, name)
    if error is not None:
        return error

    offset = max(0, int(offset))
    limit = max(200, min(int(limit), MAX_READ_CHARS))
    chunk = text[offset : offset + limit]
    has_more = offset + limit < len(text)
    return {
        "status": "ok",
        "name": name,
        "total_chars": len(text),
        "offset": offset,
        "has_more": has_more,
        "next_offset": offset + limit if has_more else None,
        "raw_artifact": entry.get("artifact"),
        "text": chunk,
    }


attachment_tool_list = [
    FunctionTool(fetch_attachment),
    FunctionTool(list_fetched_attachments),
    FunctionTool(search_attachment),
    FunctionTool(read_attachment),
]
