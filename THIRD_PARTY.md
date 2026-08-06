# Third-party attributions

nuvel is MIT licensed (see `LICENSE`). It also carries work derived from the
following MIT-licensed projects.

## gbrain

- **Project:** [garrytan/gbrain](https://github.com/garrytan/gbrain) — "Garry's Opinionated OpenClaw/Hermes Agent Brain"
- **Licence:** MIT, Copyright (c) 2026 Garry Tan
- **Language:** TypeScript

nuvel's org-memory retrieval stack is an independent Python/SQL reimplementation of
algorithm designs originating in gbrain. No gbrain source is vendored or copied; these
are Python modules written against the same algorithmic ideas, adapted to nuvel's
scope-hierarchy memory model (which gbrain does not have).

| nuvel module | Derived design |
|---|---|
| `nuvel/memory/hybrid.py` | RRF fusion, cosine blend, floor-gated boost cascade, autocut, dedup (`hybrid.ts`) |
| `nuvel/memory/relational.py` | Relational intent detection and typed-edge recall (`relational-intent.ts`, `relational-recall.ts`) |
| `nuvel/memory/extraction.py` | Verb-regex link-type inference and bare-mention scanning (`link-extraction.ts`) |
| `nuvel/memory/synthesis.py` | Answer synthesis over ranked rows rather than returning raw pages |

Reciprocal Rank Fusion itself is published prior art — Cormack, Clarke & Büttcher,
*Reciprocal Rank Fusion outperforms Condorcet and individual Rank Learning Methods*
(SIGIR 2009) — and is used here on those terms.
