---
name: style-learning-loop
description: How to learn and refine the user's writing style from passages they keep after agent edits, and when to consolidate fingerprints into a tight rulebook
when_to_use: After the user accepts a draft or rewrite (the taskpane will call this automatically), or when the user explicitly asks the agent to "study this paragraph" / "learn from what I just wrote".
---

# The style learning loop

The agent gets better at sounding like the user over time. The mechanism is a markdown topic called `writing-style` that accumulates fingerprints and gets compressed into rules.

The signal for word-king is **kept passages**: text the user accepts (inserts a draft, replaces a selection and doesn't undo) or hands the agent and says "study this". A kept passage is gold-standard voice data — the user actively chose it.

## Stage 1 — collect fingerprints (every accepted passage)

For each kept passage, call `learn_style_from_passage(passage, source, note)`. This appends a structured block like:

```
## Passage sample
- source: accepted-draft
- note: exec summary, formal
- words/paragraphs/sentences: 412/4/22 (avg 18.7 w/s, 103.0 w/paragraph)
- paragraph length range: 78-142 words
- punctuation: 0! / 1? / 3 contractions / 4 em-dashes
- structure: 0 bullet/numbered lines
- register: 5 formal markers (therefore/furthermore/etc.)
```

These are raw signals, not rules. Don't show them to the user; they're working memory.

The `source` field matters for weighting later:
- `accepted-draft` — strongest signal (user took our output).
- `kept-selection` — strong (user kept their original past our edit).
- `user-pasted` — explicit ("study this").
- `unknown` — weakest; weight low when consolidating.

## Stage 2 — consolidate (every ~10 samples)

When fingerprints accumulate, read them and derive durable rules. Then call `consolidate_writing_style(distilled_markdown)` with a tight rulebook.

A good rulebook looks like:

```markdown
# User voice — rulebook (v3)

## Paragraph shape
- Target: ~100 words per paragraph. Range 60-140.
- 4-6 paragraphs per section is the common shape.
- One-sentence paragraphs are reserved for emphasis (max 1 per section).

## Sentence shape
- Average 18 words per sentence; cap at 30.
- Mix declarative + one rhetorical question every ~3 paragraphs.

## Register
- Mid-formal. Uses "therefore", "furthermore" sparingly (~3 per page).
- Contractions allowed but rare (~2 per page).
- Zero exclamation marks. Convert ! → . if drafting.

## Lists
- Prefers prose over bullets in body sections.
- Numbered lists for sequences, bullets for parallel items.

## Voice tics
- Em-dashes for asides — comfortably.
- "We" not "I" in exec-facing text.
- Avoids "very", "really", "actually".

## Per-context overrides
- See `recipient-<context>` memories for one-off calibrations.
```

Keep it under ~2KB. Rules the drafting and rewrite tools can apply mechanically.

## Stage 3 — fold edits

If the taskpane reports an *edit* (the user inserted our draft but then modified it before continuing), the edit is the strongest learning signal. Save the *delta* as a memory note:

```
recall_memory → check for "edit-patterns" topic
save_memory(topic="edit-patterns", content="User cut 'Furthermore,' from paragraph starts. Prefers cold opens.")
```

These get folded into the rulebook on the next consolidation.

## Anti-patterns

- **Over-fitting to one passage.** One formal-register paragraph from a board memo doesn't mean the user is always formal. Wait for ≥5 samples of the same register before claiming a rule.
- **Treating one section's terminology as global.** Document-specific terms belong in document-scoped notes, not the global rulebook.
- **Burying durable rules in raw fingerprints.** That's why Stage 2 exists. The rulebook is what `recall_writing_style` returns to drafting/rewriting; raw fingerprints should be pruned on consolidation.
- **Forgetting to consolidate.** If the writing-style topic grows past ~5KB, consolidate now — the LLM doesn't reason well over a wall of raw fingerprints.
