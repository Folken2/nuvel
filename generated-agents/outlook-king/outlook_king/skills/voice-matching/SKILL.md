---
name: voice-matching
description: How to draft email replies and new messages that sound like the user, using the style memory
when_to_use: The user asks to draft, write, reply to, compose, or "knock out" an email. Includes "make me a reply to this" and "send Anna a note about X".
---

# Drafting in the user's voice

The goal is a draft the user could send unchanged. Not "a good email" — *their* good email.

## The mandatory prelude

Every drafting call starts with:

1. `recall_writing_style` — load the voice rulebook. If empty, draft conservatively (short, plain, no flourishes) and tell the user "I haven't learned your style yet — this is generic; reply with edits and I'll learn."
2. `get_current_compose` if the user is replying or building on something already in the compose window. Don't blow away their existing text — extend it.
3. `get_selected_message` if the request is "reply to this".

## Applying the style rulebook

The style memory typically contains rules like:
- Preferred opener (`Hi <name>,` vs `Hey <name>` vs none).
- Preferred sign-off (`Thanks,` vs `Best,` vs `— J`).
- Sentence length target.
- Contraction frequency (formal users avoid them).
- Punctuation tics (em-dashes, no exclamations, etc.).

Apply them mechanically. If the memory says "no exclamation marks" and the draft has one, fix it before returning.

## Voice rules that override defaults

- **Match the inbound register.** A reply to a casual one-liner should be one or two lines, not a polished paragraph. Mirror their length within ~30%.
- **Inherit the thread's terminology.** If they call the project "the rollout", call it the rollout — not "the deployment".
- **Recipient calibration.** Per-recipient memory topics (if present) override the general style rulebook. Check `recall_memory(topic="recipient-<email>")` before drafting to someone the user emails often.

## What the output looks like

When the agent inserts a draft into the compose window:

- Plain text (or minimal HTML — paragraph breaks, no markdown).
- No `Subject:` line in the body. Subject goes in the subject field separately.
- No "Here's a draft:" preamble. Just the email.
- No sign-off if the user's style is signature-less (their Outlook signature handles it).

When the agent returns a draft for the user to see in the chat first (no compose open):

- Plain text in a code block.
- One-line preamble: "Drafted in your voice — paste or ask me to refine."

## Post-send learning

After the user confirms a send (the taskpane fires `learn_style_from_sent_email`), the agent treats it as a teaching signal — especially if the user edited the draft before sending. The edit is the gold-standard voice sample.
