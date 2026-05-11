"""
Heuristic draft-analysis tools.

The LLM does the coaching judgement; these tools provide objective
ground truth so feedback is anchored to what's actually in the draft —
not hallucinated structure. Pair with ``recall_writing_style`` for
voice-match feedback.
"""

from __future__ import annotations

import re

from google.adk.tools import FunctionTool

_HEDGE_PATTERNS = (
    r"\bjust\b", r"\bmaybe\b", r"\bperhaps\b", r"\bkind of\b", r"\bsort of\b",
    r"\bI think\b", r"\bI feel like\b", r"\bI guess\b", r"\bI'm not sure\b",
)
_APOLOGY_PATTERNS = (
    r"\bsorry\b", r"\bapologies\b", r"\bmy bad\b", r"\bI apologize\b",
)
_BE_FORMS = ("is", "was", "were", "are", "be", "been", "being")
_PASSIVE_RE = re.compile(rf"\b(?:{'|'.join(_BE_FORMS)})\s+\w+ed\b", re.I)
_OPENER_RE = re.compile(r"^(hi|hey|hello|dear|good\s+(morning|afternoon|evening))\b", re.I)
_SIGNOFF_RE = re.compile(
    r"^(thanks|thank you|cheers|best|regards|sincerely|talk soon|all the best|--)\b",
    re.I,
)


def analyze_draft(draft_body: str, recipients: str = "") -> dict:
    """Run objective structural checks on an email draft.

    Returns counts and structural observations. The agent combines this
    with ``recall_writing_style`` to produce coaching feedback that's
    grounded (not vibes-based) and voice-aware (not generic).

    Args:
        draft_body: The email body the user is composing.
        recipients: Comma-separated recipient addresses. Optional — helps
            the agent calibrate formality (one-to-many vs one-to-one).

    Returns:
        Structural metrics dict. Status is ``"empty"`` if the draft has no
        content, otherwise ``"ok"``.
    """
    if not draft_body or not draft_body.strip():
        return {"status": "empty", "message": "Draft is empty."}

    body = draft_body.strip()
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", body) if s.strip()]
    words = re.findall(r"\b[\w']+\b", body)
    lines = [ln.strip() for ln in body.splitlines() if ln.strip()]

    sentence_word_counts = [len(re.findall(r"\b[\w']+\b", s)) for s in sentences] or [0]

    lower = body.lower()
    hedges = [m.group(0) for p in _HEDGE_PATTERNS for m in re.finditer(p, lower)]
    apologies = [m.group(0) for p in _APOLOGY_PATTERNS for m in re.finditer(p, lower)]
    passive_hits = _PASSIVE_RE.findall(body)

    has_opener = bool(lines and _OPENER_RE.match(lines[0]))
    # Sign-off may sit on the last line OR the line above a one-word name.
    tail = lines[-3:] if len(lines) >= 3 else lines[1:]
    has_signoff = any(_SIGNOFF_RE.match(ln) for ln in tail)

    recipient_list = [r.strip() for r in recipients.split(",") if r.strip()] if recipients else []

    return {
        "status": "ok",
        "word_count": len(words),
        "sentence_count": len(sentences),
        "avg_words_per_sentence": round(len(words) / max(len(sentences), 1), 1),
        "longest_sentence_words": max(sentence_word_counts),
        "long_sentences_over_30w": sum(1 for c in sentence_word_counts if c > 30),
        "hedge_count": len(hedges),
        "hedges_found": list(set(hedges))[:8],
        "apology_count": len(apologies),
        "passive_voice_approx": len(passive_hits),
        "has_opener": has_opener,
        "has_signoff": has_signoff,
        "first_line": lines[0][:140] if lines else "",
        "last_line": lines[-1][:140] if len(lines) > 1 else "",
        "recipient_count": len(recipient_list),
        "is_broadcast": len(recipient_list) > 3,
    }


coach_tool_list = [FunctionTool(analyze_draft)]
