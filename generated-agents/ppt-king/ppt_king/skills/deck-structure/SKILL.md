---
name: deck-structure
description: When and how to suggest reordering, splitting, or trimming a deck — agenda placement, CTA placement, problem-solution arc, methodology-results arc
when_to_use: The user asks to restructure, reorder, "fix the flow", "what's missing", or to review the whole deck rather than a single slide. Also fires for "should I split this into two decks?" and "is the order right?".
---

# Suggesting deck structure changes

Structural suggestions are expensive — every move ripples through the deck, every cut burns work. So the bar is high: a change earns its place only when the deck is meaningfully better afterwards.

## The mandatory prelude

1. `get_deck_outline` — read the live outline (titles, bullet counts, has-notes per slide).
2. `analyze_deck_flow(outline_json)` — get evidence of structural problems: repeated titles, missing agenda, missing CTA, bullet overload, section imbalance.
3. `suggest_reordering(outline_json)` — get a concrete list of move suggestions with reasons.

If both `analyze_deck_flow` and `suggest_reordering` come back with no findings, say "the flow holds — no moves I'd defend" and stop. Don't invent problems.

## Canonical arcs by intent

Pick the arc that matches what the deck is actually doing:

- **Pitch:** Problem → Solution → Evidence → Ask. The ask is the closing slide.
- **Report:** TL;DR → Methodology → Results → Implications → Recommendations.
- **Training:** Why this matters → Concept → Hands-on / example → Check-for-understanding.
- **Status update:** Progress vs plan → Wins → Blockers → Decisions needed (with owners).

For a 10-slide pitch, that's typically 1 title + 1 agenda + 2 problem + 2 solution + 2 evidence + 1 ask + 1 thanks (optional). Map the user's outline to that template; the gaps are your structural observations.

## Placement rules

- **Agenda** sits at index 1 (right after the title). Skip for decks <=8 slides; required for longer.
- **CTA / "the ask"** sits on the last (or second-to-last) slide. Never bury it inside a "Wrap-up" multi-bullet.
- **"Thanks / Q&A"** is decorative, not a closing. Either skip it or put it after the ask.
- **Methodology before results.** In reports. Always.
- **Problem before solution.** In pitches. Always.

## When to suggest a move

Only when one of:

- A canonical placement is violated (CTA buried, agenda missing on a long deck, methodology after results).
- A run of >=3 slides shares a title.
- A section dominates (>=50% of slides under one heading) and the deck has >6 slides.
- A single slide carries >=7 bullets while the rest carry 3-5 — split it.

Skip aesthetic preferences. "I'd rather have the team slide earlier" is not a structural problem.

## How to deliver

For each suggested move, return:

```
Move slide N ("<title>") → position M
Why: <one-sentence reason>
```

Cap at 3 moves in one pass. If there are more, name the top 3 and say "more once we settle these."

When the user asks "what's missing?", answer with at most 2 missing slides, each with the title you'd give it and where it'd go.

## Anti-patterns

- **Reordering for the sake of reordering.** If you can't explain the win in one sentence, don't move it.
- **Big-bang rewrites.** The user already has a deck; respect the work. Move three slides, not fifteen.
- **Suggesting splits prematurely.** Splitting a deck into two is a major operation. Only suggest when the deck has >25 slides and clearly carries two arcs.
- **Quoting full slide bodies in your reply.** Slide indices and titles are enough; the user knows the contents.
