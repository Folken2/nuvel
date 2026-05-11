---
name: slide-tightening
description: Rubric for sharpening the active slide — parallelism, bullet count, length, title strength, notes-vs-bullets separation
when_to_use: The user asks to tighten, fix, sharpen, rewrite, or "make this slide better" for the slide they're currently on. Also fires for "make these bullets parallel" or "write speaker notes for this".
---

# Tightening the active slide

Tightening = grounded, specific, short edits that earn their place. Generic advice ("make it crisper") is worse than silence.

## The mandatory prelude

Before saying anything about a slide, always:

1. `get_current_slide` — read the actual slide. Never tighten from memory or assumption.
2. `tighten_bullets_hints(bullets)` — get objective metrics (word count per bullet, verb-start flag, number presence, parallelism flag).
3. `recall_deck_style` — load the user's deck-style rules so the defaults below get overridden where they should.

If `recall_deck_style` returns `{"status": "empty"}`, say so once: "I haven't learned your deck style yet — applying defaults until you keep a few slides." Then tighten only on the objective metrics; skip style-match claims.

## The rubric (in priority order)

Walk these in order, stop at 2-3 concrete changes:

1. **Title strength.** Statement titles beat label titles. `"Q3 revenue beat plan by 8%"` > `"Q3 revenue"`. If the title is a label, that's usually the first thing to fix — every other bullet hangs off the title.
2. **Bullet count.** Default 3-5 per slide; override from style memory. If there are 7 bullets, suggest cutting to 4 and name which two to merge and which one to cut. If there's 1 bullet, the slide is probably a section break — call that out instead.
3. **Bullet length.** Default <=10 words per bullet (12 for technical material). The hints tool gives you word counts per bullet — quote them. "Bullet 3 is 18 words; here it is at 9."
4. **Parallelism.** If 4 of 5 bullets start with a verb and one starts with "The …", that's the parallelism break. Fix the outlier or fix the four — don't propose two patterns.
5. **Speaker notes.** If bullets and notes overlap >50%, one of them is wasted. Notes carry narration, examples, transitions. Bullets carry anchors. If notes are missing on a content slide, draft 2-4 lines.

## How to deliver

- Lead with **one** strongest observation, not a list.
- Quote the exact bullet or title you're commenting on. No paraphrasing.
- Offer the concrete rewrite, not a direction. "Try: 'Cut runtime 40% by caching the join'." not "make it punchier."
- If the slide is good, say "looks tight — keep it" and stop. Don't manufacture issues.
- When proposing a full slide update, return the structured block (Title / Bullets / Notes) so the taskpane can apply it.

## Failure modes to avoid

- **Generic platitudes** ("be more concise", "consider impact").
- **Rewriting the whole slide when asked for a tighten.** Edit ≠ rewrite. If the user wants a rewrite, they'll say so.
- **Inventing facts.** If the original bullet says "30% growth", the rewrite still says 30%. Numbers are sacred.
- **Adding flourishes.** Em-dashes, exclamation marks, emojis — never unless the user already uses them.
- **Stacking 6 changes.** The user will action 2-3. Pick the highest-leverage 2-3.

## On layout

Don't propose layout / theme changes. Layout is the user's call. Tighten the text on the layout they chose.
