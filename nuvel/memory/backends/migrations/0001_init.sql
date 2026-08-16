create extension if not exists vector;
create extension if not exists pg_trgm;

create table if not exists org_memories (
  id              uuid primary key default gen_random_uuid(),
  org_id          text not null,
  scope_level     text not null,
  scope_id        text not null,
  scope_chain     text[] not null,
  content         text not null,
  embedding       vector(768),
  source_app      text,
  source_session  text,
  custom_metadata jsonb not null default '{}'::jsonb,
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now()
);

create index if not exists org_memories_chain_gin
  on org_memories using gin (scope_chain);

create index if not exists org_memories_embedding_ivf
  on org_memories using ivfflat (embedding vector_cosine_ops);

create index if not exists org_memories_scope
  on org_memories (org_id, scope_level, scope_id);

create index if not exists org_memories_content_trgm
  on org_memories using gin (content gin_trgm_ops);

-- Full-text arm for hybrid RRF search (keyword arm; the vector arm uses the
-- ivfflat index above). Functional index over to_tsvector so no extra column.
create index if not exists org_memories_content_fts
  on org_memories using gin (to_tsvector('english', content));

-- ============================================================
-- Knowledge graph: self-wiring typed edges extracted (zero-LLM) from memory
-- content on write. Powers the relational-recall arm of hybrid search — a
-- high-precision, low-recall signal fused alongside keyword + vector.
-- ============================================================

-- entity_links: one row per (memory, entity) association. Typed binary
-- relations ("Alice founded Acme") are stored as a directed edge whose
-- counterpart lives in metadata->>'counterpart' (normalized), so the graph is
-- walkable from either endpoint. Bare mentions use relationship_type='mentioned'
-- with a null counterpart. source_memory_id cascades so edges die with the
-- memory that authored them.
create table if not exists entity_links (
  id                  serial primary key,
  source_memory_id    uuid not null references org_memories(id) on delete cascade,
  org_id              text not null,
  target_entity_type  text not null default 'unknown',
  target_entity_name  text not null,
  target_entity_norm  text not null,          -- normalized key for resolution
  relationship_type   text not null,
  confidence          real not null default 0.5,
  metadata            jsonb not null default '{}'::jsonb,
  created_at          timestamptz not null default now()
);

create index if not exists entity_links_source_memory
  on entity_links (source_memory_id);
create index if not exists entity_links_relationship
  on entity_links (relationship_type);
create index if not exists entity_links_target_norm
  on entity_links (org_id, target_entity_norm);
create index if not exists entity_links_target_name_trgm
  on entity_links using gin (target_entity_name gin_trgm_ops);

-- entity_names: normalized-name lookup table for entity resolution (fuzzy match
-- of a query seed to a known canonical entity). One row per distinct normalized
-- entity per org; mention_count aids future ranking / disambiguation.
create table if not exists entity_names (
  org_id         text not null,
  entity_norm    text not null,
  display_name   text not null,
  entity_type    text not null default 'unknown',
  mention_count  integer not null default 1,
  updated_at     timestamptz not null default now(),
  primary key (org_id, entity_norm)
);

create index if not exists entity_names_norm_trgm
  on entity_names using gin (entity_norm gin_trgm_ops);
