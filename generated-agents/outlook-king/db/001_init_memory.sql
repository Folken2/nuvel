-- outlook-king memory schema — applied once per Neon database.

CREATE SCHEMA IF NOT EXISTS nuvel_memory;

CREATE TABLE IF NOT EXISTS nuvel_memory.users (
    user_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email        TEXT NOT NULL UNIQUE,
    display_name TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS nuvel_memory.memories (
    id          BIGSERIAL PRIMARY KEY,
    user_id     UUID NOT NULL REFERENCES nuvel_memory.users(user_id) ON DELETE CASCADE,
    app_name    TEXT NOT NULL,
    topic       TEXT NOT NULL DEFAULT 'core',
    content     TEXT NOT NULL,
    fts         tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS memories_user_app_topic_idx
    ON nuvel_memory.memories (user_id, app_name, topic);
CREATE INDEX IF NOT EXISTS memories_fts_idx
    ON nuvel_memory.memories USING GIN (fts);
