"""
Writing-style memory tools.

Writing style lives as a markdown topic in the agent's existing memory
system (state/memory.py). These tools give the agent a stable, named
interface for the learning loop:

    recall_writing_style          read the consolidated style guide
    learn_style_from_sent_email   append a structured fingerprint after a send
    consolidate_writing_style     compress raw fingerprints into a rulebook

The fingerprint is intentionally surface-level (length, sentence shape,
opener/sign-off, contraction count). Voice judgements are the LLM's job;
this tool just gives it objective evidence to reason over.
"""

from __future__ import annotations

import re

from google.adk.tools import FunctionTool

from ..state.memory import append_topic, load_topic, save_topic

STYLE_TOPIC = "writing-style"


def _fingerprint(body: str) -> dict:
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", body) if s.strip()]
    words = re.findall(r"\b[\w']+\b", body)
    lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
    return {
        "word_count": len(words),
        "sentence_count": len(sentences),
        "avg_words_per_sentence": round(len(words) / max(len(sentences), 1), 1),
        "opener": (lines[0] if lines else "")[:120],
        "signoff": (lines[-1] if len(lines) > 1 else "")[:120],
        "exclamations": body.count("!"),
        "questions": body.count("?"),
        "contractions": len(re.findall(r"\b\w+'\w+\b", body)),
        "em_dashes": body.count("—") + len(re.findall(r"\s--\s", body)),
        "bullet_lines": sum(1 for ln in lines if re.match(r"^[-*•]\s", ln)),
    }


def recall_writing_style() -> dict:
    """Read the consolidated writing-style guide for the user.

    Always call this BEFORE drafting an email in the user's voice or
    coaching a draft. Returns the markdown style rulebook the user (or the
    agent's own learning loop) has built up over time.
    """
    content = load_topic(STYLE_TOPIC)
    if not content:
        return {
            "status": "empty",
            "message": (
                "No writing-style notes yet. After the user sends a few "
                "emails, call learn_style_from_sent_email to start the "
                "learning loop, then consolidate_writing_style to distill."
            ),
        }
    return {"status": "ok", "style": content}


def learn_style_from_sent_email(
    body: str,
    recipient: str = "",
    subject: str = "",
) -> dict:
    """Append a structured style fingerprint after the user sends an email.

    This is the learning loop's *write* step. The backend calls this (via
    the agent) immediately after a send. Stores objective markers — length,
    opener, sign-off, punctuation — that the LLM can later distill into
    voice rules via consolidate_writing_style.

    Args:
        body: Full body of the email the user just sent.
        recipient: Recipient address (helps per-context calibration later).
        subject: Subject line.
    """
    if not body or not body.strip():
        return {"status": "skip", "reason": "Empty body — nothing to learn from."}

    fp = _fingerprint(body.strip())
    note = (
        "## Sent sample\n"
        f"- to: {recipient or 'unknown'}\n"
        f"- subject: {subject or 'unknown'}\n"
        f"- words/sentences: {fp['word_count']}/{fp['sentence_count']} "
        f"(avg {fp['avg_words_per_sentence']} w/s)\n"
        f"- opener: \"{fp['opener']}\"\n"
        f"- signoff: \"{fp['signoff']}\"\n"
        f"- punctuation: {fp['exclamations']}! / {fp['questions']}? / "
        f"{fp['contractions']} contractions / {fp['em_dashes']} em-dashes / "
        f"{fp['bullet_lines']} bullet lines"
    )
    return append_topic(STYLE_TOPIC, note)


def consolidate_writing_style(distilled_markdown: str) -> dict:
    """Replace the writing-style memory with a consolidated rulebook.

    After several fingerprints accumulate, the agent reads them, derives
    durable rules (preferred sign-off, formality range, sentence length
    target, contraction habits, etc.) and writes the distilled summary
    back via this tool. Keep it short — 5 to 15 bullet points the
    drafting and coaching steps can apply directly.

    Args:
        distilled_markdown: Clean markdown rulebook. Should NOT contain raw
            email text; only durable rules.
    """
    if not distilled_markdown or not distilled_markdown.strip():
        return {"status": "error", "message": "Empty rulebook — refusing to overwrite memory."}
    return save_topic(STYLE_TOPIC, distilled_markdown.strip() + "\n")


style_tool_list = [
    FunctionTool(recall_writing_style),
    FunctionTool(learn_style_from_sent_email),
    FunctionTool(consolidate_writing_style),
]
