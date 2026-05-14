---
name: deck-outlining
description: Pattern for turning a brief into a coherent deck outline — intent detection, section ratios, draft headings, expansion
when_to_use: The user asks to outline, plan, scaffold, or draft a new deck from a brief. Includes "make me a deck about X", "outline a pitch for Y", "what slides do I need for Z".
---

# Outlining a deck from a brief

The goal is an outline the user can walk through and say "yes, that arc". Not a generic skeleton; a deck shaped by what kind of deck this *is*.

## The mandatory prelude

Every outlining call starts with:

1. `recall_deck_style` — load the user's deck-style rulebook. If empty, use defaults (3-5 bullets per slide, <=10 words each, parallel verb-led phrasing) and tell the user "I haven't learned your deck style yet — this is generic; keep what works and I'll learn."
2. `plan_deck_outline(brief, target_slide_count=10)` — get the intent (pitch / training / report / status / general), the intro/body/closing slide budget, and intent-specific hints.

If the user gave no slide count, default to 10. If the brief mentions a constraint ("3-minute pitch" → ~5 slides, "30-minute training" → ~15), respect it.

## The pattern: detect intent → set ratios → draft headings → expand

1. **Detect intent.** `plan_deck_outline` does this from keywords. If the result feels wrong (the brief mentions "pitch" but the user clearly means a status update), override the intent in your draft, but say so explicitly: "I read this as a status update, not a pitch — tell me if you want pitch shaping instead."
2. **Honour the ratios.** The scaffold returns `{"intro": N, "body": N, "closing": N}`. Build that many slides in each section; don't pad and don't trim without saying why.
3. **Draft headings.** One title per slide. Statement titles ("Q3 revenue beat plan by 8%") beat label titles ("Q3 revenue"). Keep titles to <=10 words.
4. **Expand each slide.** 3-5 bullets, <=10 words each, parallel phrasing. 2-4 lines of speaker notes that carry the narration — not a repeat of the bullets.

## Anti-patterns

- **Too many sections.** If you're proposing >5 top-level sections in a 10-slide deck, you're slicing the arc too thin. Merge.
- **Hidden CTA.** The ask should be on its own slide, near the end, with a verb-led title. If it's buried in a "Next steps" bullet on the wrap-up, it'll get missed.
- **Missing agenda for long decks.** Anything over 8 slides needs a one-line agenda right after the title. Skip for short decks.
- **"Thanks / Questions?" as the closing slide.** That's not a closing — it's a placeholder. Put the CTA there, or leave the deck closing on the strongest takeaway.
- **Three threads, one deck.** A deck does one thing. If the brief mentions three goals, ask which is primary; the other two go in an appendix or a separate deck.
- **Speaker notes that echo the bullets.** Notes are for what the speaker *says*; bullets are what stays visible after they look away. Echo = wasted real estate.

## Output shape

Return the outline as a structured block:

```
Slide 1 — Title: <title>
  Bullets:
    - <bullet>
    - <bullet>
  Notes: <2-4 lines>

Slide 2 — Title: …
  …
```

The taskpane parses this to surface per-slide "Insert as new slide" buttons. Keep titles, bullets, and notes on their own lines.

## When the brief is thin

If the brief is one sentence ("a deck about onboarding"), ask exactly one clarifying question — audience or duration — and proceed with reasonable defaults if the user doesn't answer. Don't outline blind on a vague brief.
