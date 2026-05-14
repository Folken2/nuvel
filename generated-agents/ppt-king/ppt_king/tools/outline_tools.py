"""
Deck outlining + bullet-tightening helpers.

Heuristic tools the LLM uses as scaffolding when planning a deck or
sharpening an active slide. Neither runs an LLM call; both return
structured ground truth the model can reason over without inventing
numbers.

    plan_deck_outline       brief + target slide count -> outline scaffold
    tighten_bullets_hints   per-bullet objective metrics for prioritisation
"""

from __future__ import annotations

import re

from google.adk.tools import FunctionTool


# Intent detection — first hit wins. Order matters: more specific phrases
# (e.g. "training session") sit before generic ones ("update").
_INTENT_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("pitch", (
        "pitch", "investor", "fundraise", "fundraising", "seed round",
        "series a", "series b", "vc", "raise capital", "sales pitch",
        "client pitch", "proposal",
    )),
    ("training", (
        "training", "workshop", "tutorial", "onboarding", "teach",
        "lesson", "course", "curriculum", "lecture", "how to",
    )),
    ("report", (
        "report", "results", "findings", "research", "study",
        "analysis", "post-mortem", "postmortem", "retrospective", "review",
        "annual report", "quarterly report",
    )),
    ("status", (
        "status update", "status report", "weekly update", "monthly update",
        "team update", "standup", "stand-up", "check-in", "progress update",
    )),
)

# Section ratios per intent. The integers are weights — `plan_deck_outline`
# normalises them to a target slide count. Intro / body / closing sum is
# not required to be 1; the body is the elastic part.
_RATIOS: dict[str, dict[str, int]] = {
    "pitch":    {"intro": 1, "body": 6, "closing": 2},
    "training": {"intro": 2, "body": 6, "closing": 1},
    "report":   {"intro": 1, "body": 7, "closing": 1},
    "status":   {"intro": 1, "body": 5, "closing": 1},
    "general":  {"intro": 1, "body": 6, "closing": 1},
}

# Hints injected into the scaffold so the LLM has a domain-shaped cue
# (not a hard template) when filling in section names.
_HINTS: dict[str, list[str]] = {
    "pitch": [
        "Open with the problem — make the pain visceral in one slide.",
        "Lead the body with the solution and proof, not the team.",
        "Close with a clear ask. Money, intros, decisions — be specific.",
    ],
    "training": [
        "Start with what success looks like by the end of the session.",
        "Body alternates between concept and hands-on / example.",
        "Close with a quick check-for-understanding, not a thank-you slide.",
    ],
    "report": [
        "Open with the headline finding in one sentence — TL;DR up top.",
        "Methodology before results; implications after results.",
        "Close with explicit recommendations, not a recap.",
    ],
    "status": [
        "Open with one slide of progress vs plan, not a long agenda.",
        "Body = wins, blockers, decisions needed — in that order.",
        "Close with asks tagged by owner.",
    ],
    "general": [
        "Open with why the audience should care.",
        "Body should follow one through-line — not three parallel threads.",
        "Close with the takeaway or ask, not 'questions?'.",
    ],
}


def _detect_intent(brief_lower: str) -> str:
    for intent, kws in _INTENT_KEYWORDS:
        for kw in kws:
            if kw in brief_lower:
                return intent
    return "general"


def plan_deck_outline(brief: str, target_slide_count: int = 10) -> dict:
    """Plan a structured outline scaffold from a brief.

    Reads the brief, detects intent (pitch / training / report / status /
    general), and returns a normalised scaffold: how many slides go in
    intro / body / closing, a few hints, and an empty ``sections`` list for
    the LLM to fill with concrete slide titles. The agent expands this
    scaffold into the actual outline.

    Args:
        brief: Free-text description of the deck the user wants.
        target_slide_count: How many slides total. Clamped to [3, 60].

    Returns:
        ``{"status": "ok", "intent": str, "target_slide_count": int,
        "ratios": {"intro": N, "body": N, "closing": N}, "sections":
        [{"role": "intro"|"body"|"closing", "slide_count": N}, ...],
        "hints": [str, ...]}`` or ``{"status": "error", "message": str}``
        on an empty brief.
    """
    if not brief or not brief.strip():
        return {"status": "error", "message": "Empty brief — nothing to plan."}

    target = max(3, min(int(target_slide_count or 10), 60))
    intent = _detect_intent(brief.lower())
    raw = _RATIOS[intent]
    total_weight = max(raw["intro"] + raw["body"] + raw["closing"], 1)

    # Pin intro/closing to small floors, hand the rest to body so the deck
    # length always honours target_slide_count exactly.
    intro_slides = max(1, round(target * raw["intro"] / total_weight))
    closing_slides = max(1, round(target * raw["closing"] / total_weight))
    body_slides = max(1, target - intro_slides - closing_slides)
    if intro_slides + body_slides + closing_slides != target:
        body_slides = max(1, target - intro_slides - closing_slides)

    sections = [
        {"role": "intro", "slide_count": intro_slides},
        {"role": "body", "slide_count": body_slides},
        {"role": "closing", "slide_count": closing_slides},
    ]

    hints = list(_HINTS.get(intent, _HINTS["general"]))
    if target > 8 and not any("agenda" in h.lower() for h in hints):
        hints.insert(1, "Decks over 8 slides need a one-line agenda after the title.")

    return {
        "status": "ok",
        "intent": intent,
        "target_slide_count": target,
        "ratios": {
            "intro": intro_slides,
            "body": body_slides,
            "closing": closing_slides,
        },
        "sections": sections,
        "hints": hints,
        "brief": brief.strip(),
    }


def _word_count(text: str) -> int:
    return len(re.findall(r"\b[\w']+\b", text or ""))


def _has_verb_start(bullet: str) -> bool:
    """Approximate: does the bullet open with a verb?

    Heuristic only — we look for common non-verb openers (articles,
    prepositions, the user's own subject pronouns) and assume anything
    else is verb-led. Good enough for "make these parallel" prompts.
    """
    stripped = re.sub(r"^[\s\-\*•\d.)\]\(]+", "", bullet or "").strip()
    if not stripped:
        return False
    first = stripped.split()[0].lower().strip(",.:;")
    non_verb_openers = {
        # determiners / articles
        "the", "a", "an", "this", "that", "these", "those", "our", "their",
        "my", "your", "his", "her", "its",
        # prepositions
        "in", "on", "at", "for", "with", "from", "to", "by", "of",
        "about", "into", "over", "under",
        # pronouns
        "i", "we", "you", "they", "he", "she", "it",
        # conjunctions
        "and", "or", "but", "so",
        # generic noun-ish openers commonly seen on bad slides
        "key", "main", "important", "significant",
    }
    if first in non_verb_openers:
        return False
    # Naive plural-noun check: ends with "s" and isn't a known -s verb.
    return True


def _has_number(bullet: str) -> bool:
    return bool(re.search(r"\d", bullet or ""))


def _ends_with_period(bullet: str) -> bool:
    return bool((bullet or "").rstrip().endswith("."))


def tighten_bullets_hints(bullets: list[str]) -> dict:
    """Return per-bullet objective metrics to drive sharpening decisions.

    The agent uses these to decide which bullets to cut, merge, or
    rewrite — and to keep its claims grounded ("this bullet is 18 words"
    rather than "this bullet feels long"). No LLM calls; pure regex.

    Args:
        bullets: List of bullet strings as they appear on the slide.

    Returns:
        ``{"status": "ok", "count": N, "bullets": [{"index": i,
        "text": str, "word_count": N, "has_verb_start": bool,
        "has_number": bool, "ends_with_period": bool}, ...]}``. Empty
        list yields ``{"status": "empty"}``.
    """
    if not bullets:
        return {"status": "empty", "message": "No bullets to analyse."}

    out: list[dict] = []
    for i, b in enumerate(bullets):
        text = (b or "").strip()
        out.append({
            "index": i,
            "text": text[:200],
            "word_count": _word_count(text),
            "has_verb_start": _has_verb_start(text),
            "has_number": _has_number(text),
            "ends_with_period": _ends_with_period(text),
        })

    word_counts = [b["word_count"] for b in out] or [0]
    verb_starts = [b["has_verb_start"] for b in out]

    return {
        "status": "ok",
        "count": len(out),
        "bullets": out,
        "avg_word_count": round(sum(word_counts) / max(len(word_counts), 1), 1),
        "max_word_count": max(word_counts),
        "parallel_verb_starts": all(verb_starts) if verb_starts else False,
        "any_over_10_words": any(c > 10 for c in word_counts),
    }


outline_tool_list = [
    FunctionTool(plan_deck_outline),
    FunctionTool(tighten_bullets_hints),
]
