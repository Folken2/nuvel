"""
Search query helpers for Outlook via Composio.

The real Outlook search is exposed to the agent through Composio's MCP
toolset (``OUTLOOK_*`` tools). This module adds two small primitives:

    plan_email_search   turn natural language into Outlook-search arguments
    rank_search_hits    re-rank Composio results by recency + sender weight

Neither calls Composio directly — they're advisory. The agent runs the
Composio tool itself; these just keep the prompt small and structured.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from google.adk.tools import FunctionTool

_RELATIVE_DATES = {
    "today": 0, "yesterday": 1,
    "this week": 7, "last week": 14, "past week": 7,
    "this month": 30, "last month": 60, "past month": 30,
    "this quarter": 90, "this year": 365,
}
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def plan_email_search(natural_query: str) -> dict:
    """Translate a natural-language search request into Outlook filter args.

    Extracts likely senders, date windows, keywords, and attachment hints
    from a user phrase like "emails from anna about the Q3 budget last
    week with a spreadsheet". Returns a structured plan the agent can pass
    to Composio's ``OUTLOOK_LIST_MESSAGES`` (or equivalent) tool.

    Args:
        natural_query: The user's search phrase.

    Returns:
        Plan dict with keys: ``from_addresses``, ``keywords``, ``has_attachments``,
        ``after_iso``, ``before_iso``, ``raw_query``. Empty list/None where
        nothing was detected.
    """
    q = natural_query.strip()
    if not q:
        return {"status": "error", "message": "Empty query."}

    lower = q.lower()
    addresses = _EMAIL_RE.findall(q)

    now = datetime.now(timezone.utc)
    after_iso = None
    for phrase, days in _RELATIVE_DATES.items():
        if phrase in lower:
            after_iso = (now - timedelta(days=days)).date().isoformat()
            break

    has_attachments = any(
        kw in lower for kw in ("attachment", "attached", "spreadsheet", "pdf", "doc", "file")
    )

    stop = {
        "from", "to", "email", "emails", "message", "messages",
        "about", "with", "the", "a", "an", "this", "that", "and", "or",
        "last", "past", "week", "month", "today", "yesterday", "year",
        "quarter", "attached", "attachment", "find", "search", "show",
        "any", "send", "sent", "received", "in", "my", "inbox",
    }
    tokens = re.findall(r"\b[\w']+\b", lower)
    keywords = [t for t in tokens if t not in stop and "@" not in t and len(t) > 2]

    return {
        "status": "ok",
        "from_addresses": addresses,
        "keywords": keywords[:8],
        "has_attachments": has_attachments,
        "after_iso": after_iso,
        "before_iso": None,
        "raw_query": q,
        "hint": (
            "Pass these to OUTLOOK_LIST_MESSAGES (or equivalent Composio tool). "
            "If keywords are vague, also try a $search across body+subject."
        ),
    }


def rank_search_hits(hits_json: str, prefer_recent_days: int = 30) -> dict:
    """Re-rank a JSON list of message hits by recency + sender weight.

    Pass the raw list of messages Composio returns (as a JSON string). The
    tool boosts hits inside ``prefer_recent_days`` and hits where the
    sender appears repeatedly (signal that the user converses with them).

    Args:
        hits_json: JSON-encoded list of message dicts. Each item should have
            ``received`` (ISO-8601) and ``from`` keys; missing fields are
            tolerated.
        prefer_recent_days: How aggressively to favor recent results.

    Returns:
        ``{"status": "ok", "ranked": [...]}`` with hits sorted best-first.
    """
    import json

    try:
        hits = json.loads(hits_json) if isinstance(hits_json, str) else hits_json
    except json.JSONDecodeError as e:
        return {"status": "error", "message": f"Invalid JSON: {e}"}
    if not isinstance(hits, list):
        return {"status": "error", "message": "Expected a JSON list of message dicts."}

    now = datetime.now(timezone.utc)
    sender_counts: dict[str, int] = {}
    for h in hits:
        s = (h.get("from") or "").lower()
        if s:
            sender_counts[s] = sender_counts.get(s, 0) + 1

    def _score(h: dict) -> float:
        score = 0.0
        recv = h.get("received") or h.get("receivedDateTime")
        if recv:
            try:
                dt = datetime.fromisoformat(recv.replace("Z", "+00:00"))
                age_days = max((now - dt).days, 0)
                score += max(0.0, 1.0 - age_days / max(prefer_recent_days, 1))
            except ValueError:
                pass
        sender = (h.get("from") or "").lower()
        if sender:
            score += min(sender_counts.get(sender, 0), 5) * 0.1
        if h.get("hasAttachments") or h.get("has_attachments"):
            score += 0.05
        return score

    ranked = sorted(hits, key=_score, reverse=True)
    return {"status": "ok", "ranked": ranked, "count": len(ranked)}


search_hint_tool_list = [
    FunctionTool(plan_email_search),
    FunctionTool(rank_search_hits),
]
