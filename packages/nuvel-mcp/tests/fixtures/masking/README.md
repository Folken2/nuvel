# Masking corpus

Business sentences for testing the server-side recipe masker. Written for the
test; no real client data.

Layout: `<split>/<lang>.jsonl`, with `tune/` for the split used while building
the masker and languages `es`, `en`, `ca`. One JSON object per line:

    {"category": "invoicing", "text": "Hablé con [[Pedro Martínez]] de [[Acme]]."}

- `category` is an identifier from `recipe-categories.md` (or `other`).
- `[[...]]` marks a secret: names and surnames, proper names of companies,
  products, projects and places, emails, phones, URLs and ids, and exact
  amounts and dates (digits or words). Generic nouns are never marked.
- `\n` in the text is a real newline (bullets and lists).

`tests/masking_corpus.py` loads it (`load_corpus`) and scores a mask function
(`evaluate`): a leak is a secret word token that survives in the output
(accent- and case-insensitive); `kept` is the share of non-secret word tokens
that survive (readability).
