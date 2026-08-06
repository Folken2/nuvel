# Knowledge graph schema

Two migrations build the graph: `nuvel/memory/backends/migrations/0001_init.sql`
creates the tables from scratch; `0002_entity_links.sql` is additive and idempotent,
and both are safe to re-run. **`0002` is not optional.** Its `create table ... if not
exists` statements are indeed no-ops on a `0001`-built database, but its
`alter table ... add column if not exists` statements are not — three columns exist
only because `0002` adds them (`entity_links.target_entity_name_raw`,
`entity_names.canonical_name`, `entity_names.aliases`), and `0002` also adds the
composite `entity_links_target_rel` index. Skipping `0002` leaves those out.

Migrations apply in filename-sorted order, so on any database where `0001` ran the
`create table` bodies in `0002` never take effect. Where the two files disagree on a
type, `0001` is what you get live: `entity_links.id` is `serial`, not `bigserial`.

## Tables

### `org_memories` (0001) — the base memory rows

The table the graph is *about*; `entity_links` rows reference it via
`source_memory_id`. Relevant columns: `id uuid`, `org_id text`, `scope_level text`,
`scope_id text`, `scope_chain text[]`, `content text`, `embedding vector(768)`,
`custom_metadata jsonb`, `created_at`/`updated_at timestamptz`. Indexed for hybrid
search: a GIN index on `scope_chain`, an `ivfflat` index on `embedding` (cosine
ops), a scope lookup index, a `pg_trgm` index on `content`, and a functional GIN
index on `to_tsvector('english', content)` for the keyword arm.

### `entity_links` — one directed typed edge per (memory, entity) association

Final shape (0001, columns preserved as-is by 0002's additive statements):

| column | type | purpose |
|---|---|---|
| `id` | `serial` primary key | row identity — `0001` declares `serial`; `0002`'s `bigserial` never applies, since `0001` already created the table |
| `source_memory_id` | `uuid`, FK → `org_memories(id)`, `on delete cascade` | the memory this edge was extracted from — edges die with their memory |
| `org_id` | `text` | denormalized from the memory so recall can filter without a join |
| `target_entity_type` | `text`, default `'unknown'` | best-effort role heuristic. The live write path (`extraction.py`'s `_PATTERNS`, `extraction.py:99-121`) only ever emits `person`, `company` and `org`; bare mentions emit `unknown`, which is both the column default and the most common value. (The wider vocabulary `institution` / `product` / `technology` appears only in the spec-facing `PATTERNS` dict at `extraction.py:254`, which the write path doesn't use.) |
| `target_entity_name` | `text` | display-cased name of *this* edge's endpoint |
| `target_entity_norm` | `text` | normalized resolution key (see below) |
| `relationship_type` | `text` | the edge kind, e.g. `founded`, `works_at`, or `mentioned` for a bare mention |
| `confidence` | `real`, default `0.5` | `0.9` typed verb match, `0.85` title-form employment, `0.4` bare mention |
| `metadata` | `jsonb`, default `'{}'` | for a typed edge: `{"role": "subject"|"object", "counterpart_norm": ..., "counterpart_name": ...}`; for `works_at` via the title form, also `{"position": "CTO"}` |
| `created_at` | `timestamptz` | |

0002 **adds** `target_entity_name_raw text` (nullable, spec-facing display field) — this
column does not exist on a `0001`-only database, so `0002` is required, not optional.
Its two other `add column if not exists` statements (`org_id`, `target_entity_norm`,
both with an empty-string default) *are* no-ops on a fresh 0001-built database: 0001
already declares both. They exist to backfill a links table that predates them.

**A binary typed relation is stored as two rows, one per endpoint** —
`_expand_links` in `nuvel/memory/backends/postgres_store.py:296-338` emits a subject-side row (`role:
"subject"`, counterpart is the object) and an object-side row (`role: "object"`,
counterpart is the subject) for every edge with an `obj`. This is what makes the
graph walkable from either direction: "who did Alice found?" walks from Alice's
subject-side row; "who founded Acme?" walks from Acme's object-side row. A bare
mention (`obj is None`) is a single row with no counterpart.

Indexes: `source_memory_id` (cascade lookups), `relationship_type` (edge-type
scans), `target_entity_norm` combined with `org_id` (org-scoped resolution — the
hot path for relational recall), a composite `(target_entity_norm,
relationship_type)` from 0002, and a `pg_trgm` GIN index on
`target_entity_name` for fuzzy display-name search.

### `entity_names` — org-scoped normalized-name lookup

One row per distinct normalized entity **per org** — resolution is always
org-scoped; a global-name table would leak entities across tenants.

| column | type | purpose |
|---|---|---|
| `org_id` | `text` | part of the primary key |
| `entity_norm` | `text` | part of the primary key; the normalized resolution key |
| `display_name` | `text` | canonical/display casing |
| `entity_type` | `text`, default `'unknown'` | upgraded from `'unknown'` the first time a typed classification arrives (see upsert below) |
| `mention_count` | `integer`, default `1` | incremented on every write that touches this entity; used as a tie-break in resolution |
| `updated_at` | `timestamptz` | |

Primary key: `(org_id, entity_norm)`. Upserted via `on conflict ... do update`
(`nuvel/memory/backends/postgres_store.py:96-113`): `mention_count` always increments; `entity_type`
and `display_name` only overwrite the stored `'unknown'` row when the new write
carries a real classification, so a name first seen as a bare mention (`unknown`)
gets upgraded once a typed edge later identifies it as a `person` or `company`,
but a typed classification is never demoted back to `unknown`.

0002 **adds** two spec-facing columns that `0001` does not create — `canonical_name
text` (nullable) and `aliases text[] not null default '{}'` (not nullable; an
omitted value becomes an empty array, never `NULL`) — for future
canonical-name/alias rendering; neither is populated by the current write path.
Because `0001` omits them, skipping `0002` leaves both columns missing. Indexed with a
`pg_trgm` GIN index on `entity_norm` for fuzzy seed resolution.

## Typed edge kinds and their precedence

Precedence is **confidence-based, not order-based**. `extract_entity_links`'s inner
`add()` (`extraction.py:167-178`) dedups on the key
`(subject_norm, relationship, obj_norm)` and replaces an existing entry only when the
new link's `confidence` is *strictly higher*. Three consequences:

- Two patterns yielding **different** relationships for the same entity pair produce
  **different** dedup keys, so both rows are emitted. There is no suppression — "first
  match wins" would be wrong here.
- For the **same** relationship on the same pair, the highest confidence wins.
- Source order breaks ties only incidentally: all seven entries in `_PATTERNS` share
  `CONF_TYPED = 0.9`, so equal-confidence duplicates keep the first-seen link.

`_PATTERNS` (`extraction.py:99-121`), in declaration order:

1. `founded` — "X founded/co-founded/started Y"
2. `invested_in` — "X invested in / funded / backed / led the round in Y"
3. `advises` — "X advises/advised Y"
4. `partner_of` — "X partnered with / partners with / teamed up with Y"
5. `competitor_of` — "X competes with / is a competitor of / rivals Y"
6. `attended` — "X attended / studied at / graduated from Y"
7. `works_at` — "X works at / worked at / works for / joined Y"

A separate pattern, `_WORKS_AT_TITLE` (`extraction.py:125-127`, "X is \<Title\> at
Y"), runs before the list above (`extraction.py:191-196`, ahead of the `_PATTERNS` loop at
`extraction.py:199`) and emits `works_at` with a
`position` in `metadata` at `CONF_TITLE = 0.85`. Note what protects it: `0.85` is
*lower* than the plain `works_at` verb form's `CONF_TYPED = 0.9`, so running first
would **not** save it from being overwritten. It survives because "X is \<Title\> at
Y" contains none of the plain `works_at` verbs (`works at` / `worked at` /
`works for` / `joined`), so the higher-confidence pattern simply never matches that
sentence and never produces a competing entry under the same dedup key. A sentence
that somehow contained both forms *would* end up with the `CONF_TYPED` version, and
would lose the `position` metadata.

`KNOWN_RELATIONSHIPS` (`extraction.py:33-49`) is the full vocabulary the graph
understands, including kinds with no dedicated extraction pattern yet: `built`,
`uses`, `reports_to`, `acquired`, and `funded`. (`funded` appears only as a *verb*
inside the `invested_in` pattern, which emits `invested_in`, not `funded`.)
`mentioned` is **not** in that list — the bare-mention scan produces it. The query
side (`relational.py`) only ever asks for a subset of this set.

Anything not captured by a typed pattern and not endpoint of one becomes a
`mentioned` bare edge at `CONF_MENTION = 0.4` (`extraction.py:203-211`) — a
low-confidence recall signal, not a relationship claim.

## Basename normalisation

`normalize_entity_name` (`extraction.py:130-137`) is the canonical key used
everywhere an entity is compared, stored, or resolved: strip a leading article
(`the`/`a`/`an`), trim surrounding punctuation and whitespace, collapse internal
whitespace, lowercase. `"The Acme Corp."` → `"acme corp"`. This mirrors gbrain's
`normalizeBasename` role: it's the join key between `entity_links.target_entity_norm`
and `entity_names.entity_norm`, and the thing `_PgGraphView.resolve_entity`
(`nuvel/memory/backends/postgres_store.py:354-372`) matches a query seed against — exact match first,
then a `pg_trgm` similarity match above `_ENTITY_RESOLVE_SIM = 0.45`
(`nuvel/memory/backends/postgres_store.py:282`), ordered by exact-match, then similarity, then
`mention_count` as a final tie-break.

## Stopword seeding — why precision-first

`_STOPWORDS` (`extraction.py:58-66`) excludes generic capitalized words that
grammar, not proper-noun-hood, put at a capital: articles and demonstratives
(`the`, `this`, `these`), pronouns (`it`, `he`, `they`, `you`), question words
(`when`, `where`, `who`), conjunctions (`and`, `but`, `so`), possessives (`our`,
`their`, `its`), and calendar words (`today`, `yesterday`, `tomorrow`). A phrase
that reduces to one of these after stripping a leading article is dropped
entirely (`_clean_entity`, `extraction.py:140-148`) rather than emitted as a
low-confidence mention.

The design is deliberately precision-first: a graph that occasionally misses a
real entity (false negative) degrades gracefully — that entity just doesn't get
a relational-recall boost, and the keyword/vector arms still find its memories.
A graph polluted with "The", "It", or "Yesterday" as first-class entities
degrades *worse* — every query mentioning one of those words spuriously resolves
against noise, and `entity_names.mention_count` fills up with garbage that
skews resolution tie-breaks for real entities.

## Adding a new edge type

1. **Choose the relationship name** and add it to `KNOWN_RELATIONSHIPS`
   (`extraction.py:33-49`) if it isn't already listed there (five kinds —
   `built`, `uses`, `reports_to`, `acquired`, `funded` — are pre-declared with no
   extraction pattern yet; check first).
2. **Write the verb regex** and insert it into `_PATTERNS`
   (`extraction.py:99-121`). Precedence is by *confidence*, not position — the
   dedup in `add()` (`extraction.py:167-178`) only replaces an entry when the new
   link's confidence is strictly higher, and position breaks ties only because
   every current entry shares `CONF_TYPED`. So if your new pattern could match the
   same (subject, object) pair as an existing one under the *same* relationship
   name, give it the confidence you want to win with, and don't rely on ordering.
   Reuse the `_ENTITY` capitalized-phrase pattern for `subj`/`obj` capture groups;
   pick a confidence tier (`CONF_TYPED = 0.9` for an unambiguous verb, lower for
   fuzzier forms).
3. **No migration is required for the edge itself** — `relationship_type` is a
   free-text column, not an enum, so a new value just starts appearing in
   existing rows once the pattern extracts it. Only add a migration if the new
   edge needs its own index (e.g. a dedicated lookup path) or additional
   metadata columns that don't fit the existing `jsonb metadata` column.
4. **Extend the query side** if the new relationship should be askable in
   natural language: add a pattern to `relational.py`'s `_PATTERNS`
   (`relational.py:47-56`) mapping a phrasing ("who acquired X") to the new
   relationship and walk direction (`in`/`out`/`both`).
5. **Add a test** exercising `extract_entity_links` for the new pattern and, if
   query-side support was added, `parse_relational_query` for the new phrasing
   — both are pure functions with no DB dependency, so this is a fast,
   in-process test.
