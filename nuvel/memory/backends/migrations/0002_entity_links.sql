-- 0002_entity_links.sql — self-wiring typed knowledge graph (Part 1/2).
--
-- Idempotent and additive. The canonical entity_links / entity_names schema is
-- the *org-scoped* one: an entity name is only ever resolved within its own org
-- (a global-name variant would leak entities across tenants), and the relational
-- recall arm in ``postgres_store.py`` walks it via ``org_id`` + ``*_norm`` +
-- ``metadata->>'counterpart_norm'``.
--
-- On a database where 0001 has already run, most statements here are no-ops
-- (``if not exists``). Three columns genuinely added by this migration:
--   entity_links.target_entity_name_raw   (line 39 — nullable, spec display)
--   entity_names.canonical_name           (line 69 — nullable, canonical form)
--   entity_names.aliases                  (line 70 — text[], default '{}')
-- Re-running the whole sequence is safe either way (idempotent).
--
-- Note: 0001 declares entity_links.id as ``serial``. The ``bigserial`` at line 26
-- below is unreachable on any database where 0001 ran — migrations apply
-- filename-sorted, so 0001's ``serial`` is always the live type.

create extension if not exists pg_trgm;

-- ── entity_links: one directed typed edge per (memory, entity) association ──
-- Binary relations ("Alice founded Acme") are stored as a directed edge whose
-- counterpart lives in metadata->>'counterpart_norm' (normalized), so the graph
-- is walkable from either endpoint; bare mentions use relationship_type=
-- 'mentioned' with a null counterpart. source_memory_id cascades so edges die
-- with their authoring memory.
create table if not exists entity_links (
  id                    bigserial primary key,
  source_memory_id      uuid not null references org_memories(id) on delete cascade,
  org_id                text not null,
  target_entity_type    text not null default 'unknown',
  target_entity_name    text not null,           -- display / original casing
  target_entity_norm    text not null,           -- normalized key for resolution
  relationship_type     text not null,
  confidence            real not null default 0.5,
  metadata              jsonb not null default '{}'::jsonb,
  created_at            timestamptz not null default now()
);

-- Additive column for a legacy links table that lacks it (spec display field).
alter table entity_links add column if not exists target_entity_name_raw text;
-- Backfill the org-scoped resolution columns on any pre-existing links table.
alter table entity_links add column if not exists org_id text not null default '';
alter table entity_links add column if not exists target_entity_norm text not null default '';

create index if not exists entity_links_source_memory
  on entity_links (source_memory_id);
create index if not exists entity_links_relationship
  on entity_links (relationship_type);
create index if not exists entity_links_target_rel
  on entity_links (target_entity_norm, relationship_type);
create index if not exists entity_links_target_norm
  on entity_links (org_id, target_entity_norm);
create index if not exists entity_links_target_name_trgm
  on entity_links using gin (target_entity_name gin_trgm_ops);

-- ── entity_names: org-scoped normalized-name lookup for fuzzy resolution ────
-- Resolves a query seed to a known canonical entity within one org. One row per
-- distinct normalized entity per org; mention_count aids ranking / tie-breaks.
create table if not exists entity_names (
  org_id         text not null,
  entity_norm    text not null,             -- normalized resolution key
  display_name   text not null,             -- canonical / display form
  entity_type    text not null default 'unknown',
  mention_count  integer not null default 1,
  updated_at     timestamptz not null default now(),
  primary key (org_id, entity_norm)
);

-- Spec-facing additive columns (nullable): canonical/display form + aliases.
alter table entity_names add column if not exists canonical_name text;
alter table entity_names add column if not exists aliases text[] not null default '{}';

create index if not exists entity_names_norm_trgm
  on entity_names using gin (entity_norm gin_trgm_ops);
