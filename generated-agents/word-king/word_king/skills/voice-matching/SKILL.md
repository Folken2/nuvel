---
name: voice-matching
description: How to draft new Word sections and extensions that sound like the user, using the style memory and the document context
when_to_use: The user asks to draft, write, add, continue, expand, or "knock out" a section in the document. Includes "draft a section about X", "continue from the cursor", "write the intro", "add a paragraph on Y".
---

# Drafting in the user's voice

The goal is text the user could insert unchanged. Not "good writing" — *their* good writing.

## The mandatory prelude

Every drafting call starts with:

1. `recall_writing_style` — load the voice rulebook. If empty, draft conservatively (short paragraphs, plain words, no flourishes) and tell the user "I haven't learned your style yet — this is generic; keep what works and I'll learn."
2. `get_full_document` if the brief says "add", "continue", "extend", "the intro", "the next section", or otherwise lives inside an existing doc. Mirror the surrounding paragraphs' register, paragraph length, and terminology. Do NOT silently replace what's already there.
3. For anything beyond ~300 words, `propose_section_outline(brief, target_word_count)` to lock structure before prose. Adjust the heuristic headings to match the document's tone — they're starting points, not final.

## Applying the style rulebook

The style memory typically contains rules like:
- Paragraph length target (short and punchy, or long and dense).
- Sentence length target and cap.
- Formality register: formal-marker frequency, contraction habits.
- Bullet vs prose preference for lists.
- Punctuation tics (em-dashes, no exclamations, etc.).

Apply them mechanically. If the memory says "no exclamation marks" and the draft has one, fix it before returning.

## Voice rules that override defaults

- **Mirror the surrounding paragraphs.** When extending an existing doc, the immediate neighbors are stronger evidence of voice than the global rulebook. Match their length within ~30%, their register exactly, their terminology word-for-word.
- **Inherit the document's terminology.** If the doc calls the launch "the rollout", call it the rollout — not "the deployment". If the doc says "the customer", don't switch to "the user".
- **Never invent proper nouns, statistics, quotes, or citations.** When the brief implies one, leave a clearly-marked placeholder: `[TK: source]`, `[NAME]`, `[FIGURE]`. Surface the placeholder list in your reply so the user knows what to fill in.

## What the output looks like

When the agent inserts a draft into the document:

- Plain text with blank-line paragraph breaks.
- No markdown headings — Word's heading styles are applied by the user after insert.
- No `Title:` or `Section:` prefix in the body.
- No "Here's a draft:" preamble. Just the prose.
- If returning multiple sections, use `## Heading` lines only when the user asked for headings; otherwise rely on paragraph breaks.

When the agent returns a draft for the user to see in the chat first (no insert):

- Plain prose; the Insert button copies it verbatim into the document.

## Post-acceptance learning

After the user accepts a draft (taskpane fires `learn_style_from_passage` on insert), the agent treats it as a teaching signal. The accepted text is gold-standard voice data — better than anything the user wrote on the fly, because the user actively kept it.
