"""
Deck-style memory tools.

Deck style lives as a markdown topic in the agent's existing memory
system (state/memory.py). These tools give the agent a stable, named
interface for the learning loop:

    recall_deck_style          read the consolidated deck style guide
    learn_style_from_kept_slide append a structured fingerprint after a keep
    consolidate_deck_style     compress raw fingerprints into a rulebook

The fingerprint is intentionally surface-level (bullet count, average
and max bullet length, title length, presence of notes, notes-to-bullets
ratio). Style judgements are the LLM's job; this tool just gives it
objective evidence to reason over.
"""

from __future__ import annotations

import re

from google.adk.tools import FunctionTool

from ..state.memory import append_topic, load_topic, save_topic

STYLE_TOPIC = "deck-style"


def _word_count(text: str) -> int:
    return len(re.findall(r"\b[\w']+\b", text or ""))


def _fingerprint(title: str, bullets: list[str], notes: str) -> dict:
    bullet_word_counts = [_word_count(b) for b in bullets if b and b.strip()]
    notes_words = _word_count(notes)
    bullets_words_total = sum(bullet_word_counts)
    return {
        "bullet_count": len(bullet_word_counts),
        "avg_bullet_words": (
            round(sum(bullet_word_counts) / max(len(bullet_word_counts), 1), 1)
            if bullet_word_counts else 0.0
        ),
        "max_bullet_words": max(bullet_word_counts) if bullet_word_counts else 0,
        "title_words": _word_count(title),
        "has_notes": bool(notes and notes.strip()),
        "notes_to_bullets_ratio": (
            round(notes_words / max(bullets_words_total, 1), 2)
            if bullets_words_total else (float("inf") if notes_words else 0.0)
        ),
    }


def recall_deck_style() -> dict:
    """Read the consolidated deck-style guide for the user.

    Always call this BEFORE outlining a deck, tightening the active slide,
    or suggesting structural changes. Returns the markdown style rulebook
    the user (or the agent's own learning loop) has built up over time.
    """
    content = load_topic(STYLE_TOPIC)
    if not content:
        return {
            "status": "empty",
            "message": (
                "No deck-style notes yet. After the user keeps a few "
                "slides, call learn_style_from_kept_slide to start the "
                "learning loop, then consolidate_deck_style to distill."
            ),
        }
    return {"status": "ok", "style": content}


def learn_style_from_kept_slide(
    title: str,
    bullets: list[str],
    notes: str = "",
    layout_name: str = "",
) -> dict:
    """Append a structured deck-style fingerprint after a slide is kept.

    This is the learning loop's *write* step. The backend calls this (via
    the agent) immediately after the user accepts/keeps a generated or
    tightened slide. Stores objective markers — bullet count, bullet
    length, title length, notes presence — that the LLM can later distill
    into deck-style rules via ``consolidate_deck_style``.

    Args:
        title: The slide's title.
        bullets: List of bullet strings as kept by the user.
        notes: Speaker notes for the slide. Empty string is fine.
        layout_name: Slide layout name (e.g. "Title and Content"). Optional
            — helps per-layout calibration later.
    """
    if not title and not bullets and not (notes or "").strip():
        return {"status": "skip", "reason": "Empty slide — nothing to learn from."}

    bullets = list(bullets or [])
    fp = _fingerprint(title or "", bullets, notes or "")
    note = (
        "## Kept slide\n"
        f"- layout: {layout_name or 'unknown'}\n"
        f"- title: \"{(title or '').strip()[:120]}\" ({fp['title_words']} w)\n"
        f"- bullets: {fp['bullet_count']} "
        f"(avg {fp['avg_bullet_words']} w, max {fp['max_bullet_words']} w)\n"
        f"- notes: {'yes' if fp['has_notes'] else 'no'} "
        f"(ratio {fp['notes_to_bullets_ratio']})"
    )
    return append_topic(STYLE_TOPIC, note)


def consolidate_deck_style(distilled_markdown: str) -> dict:
    """Replace the deck-style memory with a consolidated rulebook.

    After several fingerprints accumulate, the agent reads them, derives
    durable rules (preferred bullet count, bullet length cap, title style,
    notes habits, etc.) and writes the distilled summary back via this
    tool. Keep it short — 5 to 15 bullet points the outlining and
    tightening tools can apply directly.

    Args:
        distilled_markdown: Clean markdown rulebook. Should NOT contain raw
            slide text; only durable rules.
    """
    if not distilled_markdown or not distilled_markdown.strip():
        return {"status": "error", "message": "Empty rulebook — refusing to overwrite memory."}
    return save_topic(STYLE_TOPIC, distilled_markdown.strip() + "\n")


style_tool_list = [
    FunctionTool(recall_deck_style),
    FunctionTool(learn_style_from_kept_slide),
    FunctionTool(consolidate_deck_style),
]
