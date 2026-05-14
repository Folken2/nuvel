---
name: style-learning-loop
description: How to learn and refine the user's writing style from their sent emails, and when to consolidate fingerprints into a tight rulebook
when_to_use: After the user sends an email (the taskpane will call this automatically), or when the user explicitly asks the agent to "learn from" / "study" their writing.
---

# The style learning loop

The agent gets better at sounding like the user over time. The mechanism is a markdown topic called `writing-style` that accumulates fingerprints and gets compressed into rules.

## Stage 1 — collect fingerprints (every send)

For each sent email, call `learn_style_from_sent_email(body, recipient, subject)`. This appends a structured note like:

```
## Sent sample
- to: anna@…
- subject: Q3 budget
- words/sentences: 84/6 (avg 14.0 w/s)
- opener: "Hi Anna,"
- signoff: "Thanks, J"
- punctuation: 0! / 1? / 6 contractions / 2 em-dashes / 0 bullet lines
```

These are raw signals, not rules. Don't show them to the user; they're working memory.

## Stage 2 — consolidate (every ~10 samples)

When fingerprints accumulate, read them and derive durable rules. Then call `consolidate_writing_style(distilled_markdown)` with a tight rulebook.

A good rulebook looks like:

```markdown
# User voice — rulebook (v3)

## Opener
- Default: `Hi <name>,` on first-touch threads.
- In replies, no opener — jumps straight in.

## Sign-off
- Almost always: `Thanks, J`
- Casual / one-liner: just `J`
- Never: `Best regards`, `Sincerely`, `Kind regards`

## Sentence shape
- Average 14 words per sentence; cap at 25.
- 5–8 sentences per email is the common shape.

## Voice tics
- Uses contractions freely (~6 per email).
- Em-dashes for asides — comfortably.
- Zero exclamation marks. Convert ! → . if drafting.
- One rhetorical question per email is fine; more is rare.

## Recipient-specific overrides
- See `recipient-<email>` memories for one-off calibrations.
```

Keep it under ~2KB. Rules the drafting/coaching tools can apply mechanically.

## Stage 3 — when the user edits before sending

If the taskpane reports an edit (sent body ≠ drafted body), the edit is the strongest learning signal. Save the *delta* as a memory note:

```
recall_memory → check for "edit-patterns" topic
save_memory(topic="edit-patterns", content="Cut 'Just wanted to check in — ' from openers. User prefers cold-start.")
```

These get folded into the rulebook on the next consolidation.

## Anti-patterns

- **Over-fitting to one email.** One short reply doesn't mean the user always wants short replies. Wait for ≥5 samples before claiming a rule.
- **Treating subject lines as rules.** Subjects are highly contextual; don't generalize from them.
- **Burying durable rules in raw fingerprints.** That's why Stage 2 exists. The rulebook is what `recall_writing_style` returns to drafting/coaching; raw fingerprints should be pruned.
- **Forgetting to consolidate.** If the writing-style topic grows past ~5KB, consolidate now.
