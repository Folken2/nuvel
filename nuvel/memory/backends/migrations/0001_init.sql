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
