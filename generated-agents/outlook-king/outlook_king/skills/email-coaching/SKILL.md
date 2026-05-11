---
name: email-coaching
description: Rubric for giving honest, specific, voice-aware feedback on the user's draft emails
when_to_use: The user asks for feedback, review, critique, or "make this better" on a draft they're composing. Also fires when they paste a draft and ask "thoughts?"
---

# Coaching the user's draft

Coaching = grounded, specific, short, voice-aware. Generic advice ("be clearer", "more concise") is worse than silence.

## The mandatory prelude

Before saying anything about a draft, always:

1. `get_current_compose` — read the actual draft. Never coach from memory or assumption.
2. `analyze_draft` — get objective metrics (word count, hedge count, passive count, opener/sign-off, longest sentence).
3. `recall_writing_style` — load the user's voice rules.

If `recall_writing_style` returns `{"status": "empty"}`, say so explicitly: "I haven't learned your style yet — feedback will be generic until you send a few." Then coach only on the objective metrics; skip voice claims.

## The rubric (in priority order)

For each draft, walk these in order and stop when you have 2-3 concrete points:

1. **The ask is unclear.** Can the recipient tell, in one read, what they're supposed to do? If not, the rest doesn't matter. Point at the sentence that should carry the ask.
2. **Voice mismatch.** Compare opener, sign-off, contraction count, and sentence length to `recall_writing_style`. Call out one specific drift, not "tone feels off".
3. **Hedge density.** `hedge_count` ≥ 3 in a short email is usually weakness. Quote the hedges. Suggest the assertive rewrite.
4. **Sentence load.** Any sentence > 30 words is a candidate to split. `longest_sentence_words` from the analyzer tells you which.
5. **Apology inflation.** `apology_count` ≥ 2 usually means the user is over-apologizing. Replace with a direct statement of what changed.
6. **Structural drift.** `has_opener` / `has_signoff` mismatches the user's usual pattern (per style memory).

## How to deliver

- Lead with **one** strongest observation. Not a list.
- Quote the exact line you're commenting on. No paraphrasing.
- Offer a concrete rewrite, not a direction. "Try: '…'" not "make it more direct."
- If the draft is good, say "looks good — send it" and stop. Don't manufacture issues.
- Never coach length-of-message: 4 lines is fine, 40 lines is fine, if it serves the ask.

## Failure modes to avoid

- Generic platitudes ("great point!", "consider being more concise").
- Stacking 6 points — the user won't act on 6.
- Re-writing the whole email when they asked for feedback. Edit ≠ rewrite. If they want a rewrite, they'll ask for "draft this for me".
- Inventing voice rules that aren't in the style memory.
