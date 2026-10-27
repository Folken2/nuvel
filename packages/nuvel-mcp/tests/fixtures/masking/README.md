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

## What gets marked

Only the identifying token is a secret. Generic words stay unmarked, so a masker
that keeps them is not scored as leaking and the readability measure stays honest.

- Names of people: every token (`[[Pedro Martínez]]`), including a leading
  elision that is part of the same word token (`[[d'Iñigo Zubiri]]`, because
  `d'Iñigo` is one word token).
- Companies, products, projects, places: the proper name only. `Hotel [[Miramar]]`,
  `Proyecto [[Delta]]`, `[[Harbor]] Logistics`, `Via [[Torino]]`. The generic
  head noun (hotel, project, logistics, group) is not marked.
- Emails, phones, URLs, ids: the whole value.
- Dates: the number and month words that identify the date. Function words
  (de, the, of, to, al) are never marked: `[[14]] de [[noviembre]]`,
  `the [[third]] of [[March]]`. A weekday that is part of an exact date is marked
  (`[[jueves 5]] de [[febrero]]`); a bare weekday ("el viernes", "Tuesday
  morning") is not a secret. A bare month or weekday passes the masker, so it is
  not a secret ("cierre de septiembre", "until June", "los lunes", "Tuesday
  morning"); it is marked only when adjacent to a number or number word, as
  in the examples above. A bare year in digits (`spring [[2027]]`) is marked.
- Amounts: the number words and the currency word, never function words:
  `[[doce mil quinientos euros]]`, `[[treinta]] y [[dos mil euros]]`.

`evaluate` counts leaks as a multiset: a secret token is a leak only when more
copies survive in the output than the non-secret input explains, so a perfect
masker (one that replaces exactly the marked spans) scores zero leaks.

`holdout/` is written blind (without looking at the masker, its lexicon or `tune/`) and must never be used for tuning.

Spoken clock times ("a las diez", "before five") are not secrets, because a single number word passes the masker; clock times in digits (`16:30`) stay marked. Percent: mark only the number word (`[[quince]] por ciento`, `[[fifteen]] percent`). Titles (Don, Sir, senyor, DJ) are never marked; only the name is.
