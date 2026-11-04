-- platform_sync storage, version 1. {schema} is replaced with the quoted schema.
-- Row shapes are documented in docs/reference/platform-sync.md.

-- One completed run: an agent invocation (kind 'agent', run_id is ADK's
-- invocation id) or an evalv2 suite (kind 'eval', run_id derived from the suite).
CREATE TABLE {schema}.runs (
    run_id             text PRIMARY KEY,
    kind               text NOT NULL CHECK (kind IN ('agent', 'eval')),
    agent              text NOT NULL,
    session_id         text,
    user_id            text,
    app_name           text,
    model              text,
    started_at         timestamptz NOT NULL,
    duration_ms        bigint,
    llm_calls          integer NOT NULL DEFAULT 0,
    tool_calls         integer NOT NULL DEFAULT 0,
    error_count        integer NOT NULL DEFAULT 0,
    prompt_tokens      bigint NOT NULL DEFAULT 0,
    completion_tokens  bigint NOT NULL DEFAULT 0,
    cost_usd           double precision,
    synced_at          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX runs_started_idx ON {schema}.runs (started_at DESC);
CREATE INDEX runs_agent_idx ON {schema}.runs (agent, started_at DESC);

-- seq is the event's position in its run, so a re-sync hits the primary key.
CREATE TABLE {schema}.trace_events (
    run_id       text NOT NULL REFERENCES {schema}.runs (run_id) ON DELETE CASCADE,
    seq          integer NOT NULL,
    event        text NOT NULL,
    agent        text,
    occurred_at  timestamptz NOT NULL,
    data         jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (run_id, seq)
);
CREATE INDEX trace_events_event_idx ON {schema}.trace_events (event);

-- One row per example of an evalv2 suite run.
CREATE TABLE {schema}.eval_results (
    run_id             text NOT NULL REFERENCES {schema}.runs (run_id) ON DELETE CASCADE,
    example_id         text NOT NULL,
    skill              text NOT NULL,
    suite              text NOT NULL,
    input              text NOT NULL,
    score              double precision,
    passed             boolean,
    cache_hit          boolean NOT NULL DEFAULT false,
    cost_usd           double precision NOT NULL DEFAULT 0,
    evaluator_results  jsonb NOT NULL DEFAULT '[]'::jsonb,
    notes              jsonb NOT NULL DEFAULT '[]'::jsonb,
    PRIMARY KEY (run_id, example_id)
);
CREATE INDEX eval_results_skill_idx ON {schema}.eval_results (skill, suite);

-- USD per token, as in pricing.json. updated_at moves only when a price does.
CREATE TABLE {schema}.model_prices (
    model             text PRIMARY KEY,
    input_per_token   double precision NOT NULL,
    output_per_token  double precision NOT NULL,
    updated_at        timestamptz NOT NULL DEFAULT now()
);
