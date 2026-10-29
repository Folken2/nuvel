-- nuvel-mcp storage, version 1. {schema} is replaced with the quoted schema.
-- tenant is the caller's OAuth client_id (NULL for stdio / unauthenticated).
-- It is plain text, not a foreign key: history outlives a deleted client.

-- OAuth: one shape per table. record is exactly what the file store keeps for
-- the row (hashes only, never a secret); id is the client_id for clients and
-- the credential's hash otherwise.
CREATE TABLE {schema}.oauth_clients (
    id          text PRIMARY KEY,
    client_id   text,
    record      jsonb NOT NULL,
    updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE {schema}.oauth_codes (LIKE {schema}.oauth_clients INCLUDING ALL);
CREATE TABLE {schema}.oauth_access_tokens (LIKE {schema}.oauth_clients INCLUDING ALL);
CREATE TABLE {schema}.oauth_refresh_tokens (LIKE {schema}.oauth_clients INCLUDING ALL);
CREATE TABLE {schema}.oauth_url_keys (LIKE {schema}.oauth_clients INCLUDING ALL);
CREATE INDEX oauth_codes_client_idx ON {schema}.oauth_codes (client_id);
CREATE INDEX oauth_access_tokens_client_idx ON {schema}.oauth_access_tokens (client_id);
CREATE INDEX oauth_refresh_tokens_client_idx ON {schema}.oauth_refresh_tokens (client_id);
CREATE INDEX oauth_url_keys_client_idx ON {schema}.oauth_url_keys (client_id);

-- Change counter for the OAuth tables: readers compare it to their snapshot,
-- writers lock this row FOR UPDATE and bump it.
CREATE TABLE {schema}.oauth_state (
    id       smallint PRIMARY KEY CHECK (id = 1),
    version  bigint NOT NULL
);
INSERT INTO {schema}.oauth_state (id, version) VALUES (1, 0);

-- nuvel.skill_run/1, as normalised by traces.normalize.
CREATE TABLE {schema}.skill_runs (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant          text,
    run_id          text NOT NULL,
    install_id      text NOT NULL,
    plugin_name     text NOT NULL,
    plugin_version  text,
    skill_name      text NOT NULL,
    skill_version   text,
    host            text NOT NULL,
    mode            text NOT NULL,
    started_at      timestamptz NOT NULL,
    duration_s      integer NOT NULL,
    outcome         text NOT NULL,
    user_corrected  boolean,
    record          jsonb NOT NULL,
    received_at     timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX skill_runs_run_uq ON {schema}.skill_runs ((coalesce(tenant, '')), run_id);
CREATE INDEX skill_runs_skill_idx ON {schema}.skill_runs (plugin_name, skill_name, started_at);
CREATE INDEX skill_runs_tenant_idx ON {schema}.skill_runs (tenant, started_at);

-- nuvel.recipe_signal/1
CREATE TABLE {schema}.recipe_signals (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant       text,
    signal_id    text NOT NULL,
    recipe_id    text NOT NULL,
    install_id   text NOT NULL,
    plugin_name  text NOT NULL,
    category     text NOT NULL,
    event        text NOT NULL,
    at           timestamptz NOT NULL,
    uses         integer NOT NULL,
    successes    integer NOT NULL,
    corrections  integer NOT NULL,
    record       jsonb NOT NULL,
    received_at  timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX recipe_signals_signal_uq ON {schema}.recipe_signals ((coalesce(tenant, '')), signal_id);
CREATE INDEX recipe_signals_recipe_idx ON {schema}.recipe_signals (recipe_id, at);

-- nuvel.recipe/2, one row per version. The masked free text stays in record.
CREATE TABLE {schema}.recipes (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant       text,
    recipe_id    text NOT NULL,
    version      integer NOT NULL,
    install_id   text NOT NULL,
    plugin_name  text NOT NULL,
    category     text NOT NULL,
    language     text NOT NULL,
    record       jsonb NOT NULL,
    received_at  timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX recipes_version_uq ON {schema}.recipes ((coalesce(tenant, '')), recipe_id, version);

-- One row per JSON-RPC request (notifications excluded). method and tool are
-- names the server knows, or 'unknown': never caller-supplied text.
CREATE TABLE {schema}.usage_events (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    at           timestamptz NOT NULL,
    tenant       text,
    transport    text NOT NULL CHECK (transport IN ('http', 'stdio')),
    method       text NOT NULL,
    tool         text,
    outcome      text NOT NULL CHECK (outcome IN ('ok', 'tool_error', 'error')),
    error_code   integer,
    duration_ms  integer NOT NULL
);
CREATE INDEX usage_events_at_idx ON {schema}.usage_events (at);
CREATE INDEX usage_events_tenant_idx ON {schema}.usage_events (tenant, at);

CREATE VIEW {schema}.usage_daily AS
SELECT date_trunc('day', at) AS day,
       tenant,
       method,
       tool,
       count(*) AS calls,
       count(*) FILTER (WHERE outcome <> 'ok') AS errors,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY duration_ms) AS p50_ms,
       percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_ms) AS p95_ms
FROM {schema}.usage_events
GROUP BY 1, 2, 3, 4;

CREATE VIEW {schema}.skill_runs_daily AS
SELECT date_trunc('day', started_at) AS day,
       tenant,
       plugin_name,
       skill_name,
       count(*) AS runs,
       count(*) FILTER (WHERE outcome = 'success') AS success,
       count(*) FILTER (WHERE outcome = 'partial') AS partial,
       count(*) FILTER (WHERE outcome = 'failure') AS failure,
       count(*) FILTER (WHERE outcome IN ('blocked', 'skipped')) AS not_run,
       count(*) FILTER (WHERE user_corrected) AS corrected
FROM {schema}.skill_runs
GROUP BY 1, 2, 3, 4;
