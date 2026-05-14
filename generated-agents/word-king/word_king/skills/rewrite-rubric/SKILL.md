---
name: rewrite-rubric
description: How to rewrite a selected passage per the user's instruction without silently expanding scope, breaking length, or losing quoted material
when_to_use: The user asks to "rewrite this", "fix this", "tighten this", "make this clearer", "make this more formal", "fix the typo", or otherwise applies an edit to the currently-selected text. Also fires when the user pastes a passage with an edit instruction.
---

# Rewriting selected passages

Rewriting is edit work, not authoring. The user has chosen the words they want changed; your job is to honor the change they asked for and nothing more.

## The mandatory prelude

Before producing the rewrite, always:

1. `recall_writing_style` — load the voice rulebook. Voice is preserved even on a minimal edit.
2. `get_current_selection` — read the actual selected text. Never rewrite from memory or paraphrase the user's selection.
3. `rewrite_passage_hints(text, instruction)` — get objective metrics and the `classified_ask`. The classified_ask is the contract for this turn.

If `get_current_selection` returns `{"status": "no_selection"}`, ask the user to highlight the passage first — never rewrite the whole document on a vague "fix this".

## The rubric (apply in order)

### (a) Match the exact ask

The `classified_ask` from `rewrite_passage_hints` tells you the shape of the edit:

| classified_ask | Allowed | NOT allowed |
|---|---|---|
| `minimal-fix` | Change only the broken token (typo, grammar slip). | Touching anything that wasn't wrong. |
| `shorten` | Cut up to ~30-45% of words. | Adding new ideas. |
| `expand` | Add detail or examples up to ~60% more. | Padding with hedges or filler. |
| `clarify` | Restructure or simplify sentences. | Changing the argument or claim. |
| `raise-register` | More formal word choice, fewer contractions. | Adding pomp the user wouldn't use. |
| `lower-register` | Looser, contractions, friendlier tone. | Slang the user has never used in style memory. |
| `rewrite-preserve-meaning` | Voice-match rewrite at same length. | Drifting on meaning to chase prettiness. |
| `unspecified` / `other` | Ask the user what they want. | Guessing. |

### (b) Keep length within ±20% (unless the ask grows or shrinks it)

The hints tool returns `target_word_count_low` and `target_word_count_high`. The rewrite must land inside that window. If it doesn't, trim or expand before returning.

### (c) Preserve quotes and citations verbatim

`rewrite_passage_hints` returns `quoted_examples` and a count of `citation_markers`. These are inviolable:

- Quoted spans (anything in double quotes ≥ 5 chars) must appear in the rewrite character-for-character.
- Citation markers ([1], (2024), "et al.") must remain in the same positions relative to the surrounding claim.
- If a quote is malformed in the source (e.g. a smart quote inside a typo), flag it to the user — don't silently fix or paraphrase it.

### (d) Preserve technical terminology

If the surrounding document uses specific terminology (`get_full_document` reveals it), the rewrite must keep it:

- Don't swap "the rollout" → "the launch" for variety.
- Don't translate "p-value" → "probability".
- Don't capitalize what the user wrote lowercase, or vice versa.

The user's terminology is part of their voice.

## How to deliver

- Return ONLY the rewritten passage. No preamble. No "Here's the rewrite:". No fences.
- The taskpane replaces the selection with what you return character-for-character.
- If you needed to leave a placeholder (e.g. `[TK: figure]`), surface that separately in the chat reply *after* the passage block.

## Failure modes to avoid

- Silently rewriting a paragraph when the user said "fix the typo". The classified_ask was `minimal-fix`; honor it.
- "Improving" the user's argument while rewriting. Rewriting changes form, not content.
- Inflating word count under `clarify` or `raise-register`. Stay within the window.
- Dropping a citation because it "interrupts the flow". Citations are content.
- Treating a quoted span as paraphraseable. It isn't.
