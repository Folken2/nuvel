"""
Writing-style memory tools.

Writing style lives as a markdown topic in the agent's existing memory
system (state/memory.py). These tools give the agent a stable, named
interface for the learning loop:

    recall_writing_style              read the consolidated style guide
    learn_style_from_passage          append a structured fingerprint after the
                                      user keeps a passage past an agent edit
    consolidate_writing_style         compress raw fingerprints into a rulebook

The fingerprint is intentionally surface-level (paragraph length
distribution, sentence shape, register hints, bullet frequency). Voice
judgements are the LLM's job; this tool just gives it objective
evidence to reason over.

The ``writing-style`` topic name is shared with other Office-surface
agents (outlook-king) so style memory can roam across surfaces.
"""

from __future__ import annotations

import re

from google.adk.tools import FunctionTool

from ..state.memory import append_topic, load_topic, save_topic

STYLE_TOPIC = "writing-style"

# Words that bias toward a more formal register when frequent.
_FORMAL_MARKERS = (
    "therefore", "furthermore", "moreover", "hereby", "henceforth",
    "accordingly", "notwithstanding", "consequently", "thus", "whereby",
    "pursuant", "regarding", "aforementioned", "shall",
)


def _fingerprint(body: str) -> dict:
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
    paragraph_word_counts = [
        len(re.findall(r"\b[\w']+\b", p)) for p in paragraphs
    ] or [0]

    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", body) if s.strip()]
    words = re.findall(r"\b[\w']+\b", body)
    lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
    lower = body.lower()

    formal_hits = sum(lower.count(m) for m in _FORMAL_MARKERS)
    bullets = sum(1 for ln in lines if re.match(r"^[-*•]\s|^\d+[.)]\s", ln))

    return {
        "word_count": len(words),
        "paragraph_count": len(paragraphs),
        "sentence_count": len(sentences),
        "avg_words_per_sentence": round(len(words) / max(len(sentences), 1), 1),
        "avg_words_per_paragraph": round(
            sum(paragraph_word_counts) / max(len(paragraph_word_counts), 1), 1
        ),
        "longest_paragraph_words": max(paragraph_word_counts),
        "shortest_paragraph_words": min(paragraph_word_counts),
        "exclamations": body.count("!"),
        "questions": body.count("?"),
        "contractions": len(re.findall(r"\b\w+'\w+\b", body)),
        "em_dashes": body.count("—") + len(re.findall(r"\s--\s", body)),
        "bullet_lines": bullets,
        "formal_marker_count": formal_hits,
    }


def recall_writing_style() -> dict:
    """Read the consolidated writing-style guide for the user.

    Always call this BEFORE drafting a new section or rewriting a
    passage in the user's voice. Returns the markdown style rulebook
    the user (or the agent's own learning loop) has built up over
    time.
    """
    content = load_topic(STYLE_TOPIC)
    if not content:
        return {
            "status": "empty",
            "message": (
                "No writing-style notes yet. After the user accepts a few "
                "drafts or rewrites, call learn_style_from_passage to start "
                "the learning loop, then consolidate_writing_style to distill."
            ),
        }
    return {"status": "ok", "style": content}


def learn_style_from_passage(
    passage: str,
    source: str = "",
    note: str = "",
) -> dict:
    """Append a structured style fingerprint from a passage the user kept.

    This is the learning loop's *write* step. Fires after the user
    accepts an agent-produced draft (the kept text is gold-standard
    voice data), or when the user explicitly asks the agent to "study
    this paragraph". Stores objective markers — paragraph length,
    sentence shape, formal-marker frequency, bullets, punctuation —
    that the LLM can later distill into voice rules via
    ``consolidate_writing_style``.

    Args:
        passage: The text the user kept (or asked us to study).
        source: Free-form provenance — e.g. ``"accepted-draft"``,
            ``"selection-kept-as-is"``, ``"user-pasted"``. Helps
            weighting later; we never expose this to the user.
        note: Optional one-line annotation to file alongside the
            fingerprint (e.g. "exec summary, formal").
    """
    if not passage or not passage.strip():
        return {"status": "skip", "reason": "Empty passage — nothing to learn from."}

    fp = _fingerprint(passage.strip())
    block = (
        "## Passage sample\n"
        f"- source: {source or 'unknown'}\n"
        f"- note: {note or '(none)'}\n"
        f"- words/paragraphs/sentences: {fp['word_count']}/"
        f"{fp['paragraph_count']}/{fp['sentence_count']} "
        f"(avg {fp['avg_words_per_sentence']} w/s, "
        f"{fp['avg_words_per_paragraph']} w/paragraph)\n"
        f"- paragraph length range: {fp['shortest_paragraph_words']}-"
        f"{fp['longest_paragraph_words']} words\n"
        f"- punctuation: {fp['exclamations']}! / {fp['questions']}? / "
        f"{fp['contractions']} contractions / {fp['em_dashes']} em-dashes\n"
        f"- structure: {fp['bullet_lines']} bullet/numbered lines\n"
        f"- register: {fp['formal_marker_count']} formal markers "
        "(therefore/furthermore/etc.)"
    )
    return append_topic(STYLE_TOPIC, block)


def consolidate_writing_style(distilled_markdown: str) -> dict:
    """Replace the writing-style memory with a consolidated rulebook.

    After several fingerprints accumulate, the agent reads them, derives
    durable rules (paragraph length target, formality register,
    sentence length target, bullet habits, contraction frequency, etc.)
    and writes the distilled summary back via this tool. Keep it
    short — 5 to 15 bullet points the drafting and rewrite steps can
    apply directly.

    Args:
        distilled_markdown: Clean markdown rulebook. Should NOT contain
            raw passage text; only durable rules.
    """
    if not distilled_markdown or not distilled_markdown.strip():
        return {
            "status": "error",
            "message": "Empty rulebook — refusing to overwrite memory.",
        }
    return save_topic(STYLE_TOPIC, distilled_markdown.strip() + "\n")


style_tool_list = [
    FunctionTool(recall_writing_style),
    FunctionTool(learn_style_from_passage),
    FunctionTool(consolidate_writing_style),
]
