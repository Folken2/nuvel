---
name: style-learning-loop
description: How to learn the user's deck style from kept slides and when to consolidate fingerprints into a tight rulebook
when_to_use: After the user keeps a generated or tightened slide (the taskpane fires this), or when the user explicitly asks the agent to "learn from" / "study" their decks.
---

# The deck-style learning loop

The agent gets better at matching the user's deck style over time. The mechanism is a markdown topic called `deck-style` that accumulates fingerprints and gets compressed into rules.

## Stage 1 — collect fingerprints (every kept slide)

For each kept slide, call `learn_style_from_kept_slide(title, bullets, notes, layout_name)`. This appends a structured note like:

```
## Kept slide
- layout: Title and Content
- title: "Q3 revenue beat plan by 8%" (6 w)
- bullets: 4 (avg 7.5 w, max 9 w)
- notes: yes (ratio 1.8)
```

These are raw signals, not rules. Don't show them to the user; they're working memory.

A "kept slide" means: the user accepted the agent's generated slide, or applied a tightening with no further edits, or hit Insert-as-new-slide on a proposed slide. Edits before keep are also worth recording — the post-edit fingerprint is the gold-standard signal.

## Stage 2 — consolidate (every ~10 samples)

When fingerprints accumulate, read them and derive durable rules. Then call `consolidate_deck_style(distilled_markdown)` with a tight rulebook.

A good rulebook looks like:

```markdown
# User deck style — rulebook (v2)

## Bullets
- Default: 4 bullets per content slide.
- Length: average 7 words; cap at 10.
- Phrasing: parallel verb-led ("Cut", "Build", "Ship").

## Titles
- Statement titles, not labels.
- Average 6 words; cap at 10.
- No trailing punctuation.

## Speaker notes
- Always present on content slides.
- 2-4 lines; carry the narration, not the bullets.
- Notes-to-bullets word ratio ~1.5-2.0.

## Layouts
- "Title and Content" for body slides.
- "Section Header" for transitions only.

## Voice tics
- No exclamation marks. Convert ! → . if drafting.
- No emoji.
- Em-dashes — used sparingly.
```

Keep it under ~2KB. Rules the outlining and tightening tools can apply mechanically.

## Stage 3 — when the user edits before keeping

If the taskpane reports an edit (kept slide differs from the proposed slide), that delta is the strongest learning signal. Save the *delta* as a memory note:

```
recall_memory → check for "edit-patterns" topic
save_memory(topic="edit-patterns", content="User cuts trailing periods on bullets. User shortens verb-phrases from 'Improving' to 'Improve'.")
```

These get folded into the rulebook on the next consolidation.

## Anti-patterns

- **Over-fitting to one slide.** A single 3-bullet slide doesn't mean the user always wants 3 bullets. Wait for >=5 samples before claiming a rule.
- **Treating layout choice as a rule.** Layout depends on slide role. "User likes Title-and-Content" is meaningless — every content slide uses it.
- **Burying durable rules in raw fingerprints.** That's why Stage 2 exists. The rulebook is what `recall_deck_style` returns to outlining and tightening; raw fingerprints should be pruned.
- **Forgetting to consolidate.** If the deck-style topic grows past ~5KB, consolidate now.
- **Confusing per-deck choices with per-user style.** A pitch and a status update have different bullet styles; a per-intent override is fine, but don't promote a one-deck choice to a global rule.
