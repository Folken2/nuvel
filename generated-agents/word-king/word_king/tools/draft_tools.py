"""
Heuristic drafting/rewriting helper tools.

The LLM does the writing judgement; these tools provide objective
ground truth so the output is anchored to what's actually in the
passage (or what the user asked for) — not hallucinated structure.

Pair with ``recall_writing_style`` for voice-aware drafting and
rewriting.

Both tools are pure Python — no LLM calls, no external services.
"""

from __future__ import annotations

import re

from google.adk.tools import FunctionTool

_HEDGE_PATTERNS = (
    r"\bjust\b", r"\bmaybe\b", r"\bperhaps\b", r"\bkind of\b", r"\bsort of\b",
    r"\bI think\b", r"\bI feel like\b", r"\bI guess\b", r"\bI'm not sure\b",
    r"\barguably\b", r"\bsomewhat\b", r"\bin some ways\b",
)
_BE_FORMS = ("is", "was", "were", "are", "be", "been", "being")
_PASSIVE_RE = re.compile(rf"\b(?:{'|'.join(_BE_FORMS)})\s+\w+ed\b", re.I)
_QUOTE_RE = re.compile(r"[\"“][^\"“”]{4,}[\"”]")
_CITATION_RE = re.compile(r"\[\d+\]|\(\d{4}\)|\bet\s+al\.")


def _split_sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


def _word_count(text: str) -> int:
    return len(re.findall(r"\b[\w']+\b", text))


def propose_section_outline(brief: str, target_word_count: int = 600) -> dict:
    """Propose a structural outline for a new section the agent will draft.

    Heuristic only — splits the brief into discoverable beats (numbered
    or bulleted hints) and produces a balanced outline of section
    headings with a one-line scope per heading. The actual drafting is
    the LLM's job; this tool just gives it a stable structural starting
    point so it doesn't blow word count or skip a stated beat.

    Args:
        brief: The user's brief for the new section. Free-form text —
            paragraph, list, or one-liner.
        target_word_count: Approximate words the final section should
            hit (default 600). Used to compute words-per-heading.

    Returns:
        On success: ``{"status": "ok", "headings": [{"heading": str,
        "scope": str, "target_words": int}], "total_target_words": int,
        "notes": str}``. On empty brief: ``{"status": "error", ...}``.
    """
    if not brief or not brief.strip():
        return {"status": "error", "message": "Empty brief — give me something to outline."}
    if target_word_count <= 0:
        return {"status": "error", "message": "target_word_count must be > 0."}

    # Find explicit beats from numbered/bulleted lines or "and"/";" splits.
    lines = [ln.strip() for ln in brief.splitlines() if ln.strip()]
    beats: list[str] = []
    for ln in lines:
        m = re.match(r"^(?:\d+[.)]|[-*•])\s+(.*)$", ln)
        if m:
            beats.append(m.group(1).strip())

    if not beats:
        # Fall back to splitting the brief on semicolons or " and "
        # connectors so we still produce a multi-section outline.
        flat = " ".join(lines)
        parts = re.split(r";|\s+and\s+(?=[a-z])|\.\s+(?=[A-Z])", flat)
        beats = [p.strip(" .") for p in parts if len(p.strip()) >= 8]

    # Guarantee at least 3 headings; cap at 6 so we don't fragment.
    if len(beats) < 3:
        # If we still have too few, generate generic structural slots so
        # the LLM has at least intro / body / wrap to work with.
        seed = beats[0] if beats else brief.strip()[:80]
        while len(beats) < 3:
            if len(beats) == 0:
                beats.append(f"Setup and stakes for: {seed}")
            elif len(beats) == 1:
                beats.append(f"Substance — the core argument or detail of: {seed}")
            else:
                beats.append(f"Takeaway or next step from: {seed}")
    beats = beats[:6]

    per_section = max(target_word_count // len(beats), 60)
    headings: list[dict] = []
    for i, beat in enumerate(beats, 1):
        # Heading = capitalized version of the first ~6 words of the beat.
        words = re.findall(r"\b[\w']+\b", beat)[:8]
        heading = " ".join(words).strip().capitalize() or f"Section {i}"
        headings.append({
            "heading": heading[:80],
            "scope": beat[:200],
            "target_words": per_section,
        })

    return {
        "status": "ok",
        "headings": headings,
        "total_target_words": per_section * len(headings),
        "notes": (
            "Headings are heuristic — the agent should tighten them to "
            "the document's tone. Sum of target_words can drift "
            f"~{abs(target_word_count - per_section * len(headings))} from the requested "
            f"{target_word_count} after even split."
        ),
    }


def rewrite_passage_hints(text: str, instruction: str) -> dict:
    """Return objective metrics on a passage to ground a rewrite.

    Pure heuristics — counts, longest sentence, passive count, hedge
    count, presence of quotes and citations. The agent uses these to
    keep the rewrite scoped (match the ask, keep length, preserve
    quotes verbatim) without re-reading the text in its head.

    Args:
        text: The passage the user wants rewritten.
        instruction: The user's rewrite instruction (e.g. "make it
            tighter", "fix the typo", "make it more formal"). Used to
            classify the ask so the agent doesn't silently expand scope.

    Returns:
        Metrics dict. Status is ``"empty"`` if the passage has no
        content, otherwise ``"ok"``.
    """
    if not text or not text.strip():
        return {"status": "empty", "message": "Passage is empty."}

    body = text.strip()
    sentences = _split_sentences(body)
    sentence_word_counts = [_word_count(s) for s in sentences] or [0]

    lower = body.lower()
    hedges = [m.group(0) for p in _HEDGE_PATTERNS for m in re.finditer(p, lower)]
    passive_hits = _PASSIVE_RE.findall(body)
    quoted = _QUOTE_RE.findall(body)
    citations = _CITATION_RE.findall(body)

    instr = (instruction or "").strip().lower()
    # Classify the ask so the agent enforces scope (typo fix ≠ rewrite).
    if not instr:
        ask = "unspecified"
    elif any(k in instr for k in ("typo", "spelling", "grammar fix")):
        ask = "minimal-fix"
    elif any(k in instr for k in ("shorten", "tighten", "trim", "cut")):
        ask = "shorten"
    elif any(k in instr for k in ("expand", "longer", "more detail", "flesh out")):
        ask = "expand"
    elif any(k in instr for k in ("formal", "professional", "polish")):
        ask = "raise-register"
    elif any(k in instr for k in ("casual", "loose", "friendlier")):
        ask = "lower-register"
    elif any(k in instr for k in ("rewrite", "rephrase", "redo", "voice")):
        ask = "rewrite-preserve-meaning"
    elif any(k in instr for k in ("clear", "clarity", "simpler", "plain")):
        ask = "clarify"
    else:
        ask = "other"

    word_count = _word_count(body)
    # ±20% bounds for length unless the ask explicitly grows or shrinks.
    if ask == "shorten":
        target_low, target_high = int(word_count * 0.55), int(word_count * 0.85)
    elif ask == "expand":
        target_low, target_high = int(word_count * 1.2), int(word_count * 1.6)
    else:
        target_low = max(int(word_count * 0.8), 1)
        target_high = max(int(word_count * 1.2), word_count)

    return {
        "status": "ok",
        "classified_ask": ask,
        "word_count": word_count,
        "sentence_count": len(sentences),
        "avg_words_per_sentence": round(word_count / max(len(sentences), 1), 1),
        "longest_sentence_words": max(sentence_word_counts),
        "longest_sentence_index": (
            sentence_word_counts.index(max(sentence_word_counts)) if sentences else -1
        ),
        "hedge_count": len(hedges),
        "hedges_found": list(set(hedges))[:8],
        "passive_voice_approx": len(passive_hits),
        "quoted_spans": len(quoted),
        "quoted_examples": quoted[:3],
        "citation_markers": len(citations),
        "target_word_count_low": target_low,
        "target_word_count_high": target_high,
        "scope_guidance": (
            "Match the classified_ask exactly. If 'minimal-fix', change only "
            "the broken token. If 'shorten' or 'expand', honor the new "
            "length window. Otherwise stay within ±20% of word_count. "
            "Preserve quoted_examples and citation markers verbatim."
        ),
    }


draft_tool_list = [
    FunctionTool(propose_section_outline),
    FunctionTool(rewrite_passage_hints),
]
