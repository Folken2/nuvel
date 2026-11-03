# Platform sync

`platform_sync` writes structured rows to Postgres (Neon) so that an internal console can read them. `nuvel` only writes; the console only reads.

It covers the meta-agent (`nuvel run`) and the `nuvel evalv2` and `nuvel pricing` commands. Generated agents do not carry the plugin.

## Enabling it

Off by default. The plugin is always in the meta-agent's chain and does nothing until enabled.

| Var | Default | Description |
|---|---|---|
| `NUVEL_PLATFORM_SYNC` | off | `1` to enable. |
| `NUVEL_PLATFORM_DATABASE_URL` | none | Postgres connection string. Neon's default strings are accepted: `channel_binding` is dropped because asyncpg does not implement it, and `sslmode` is honoured. Statements are not cached, as a `-pooler` endpoint requires. |

The tables live in the `nuvel_platform` schema. The role needs `CREATE` on the database, because the schema is created and migrated on the first write.

`trace_events` stores user input, model responses, tool arguments and tool results, each truncated, exactly as the JSONL traces do. Treat the database as you treat the `traces/` directory.

## What writes what

| Trigger | Rows |
|---|---|
| A meta-agent run completes | one `runs` row (`kind = 'agent'`), its `trace_events` |
| `nuvel evalv2 run` finishes a suite | one `runs` row (`kind = 'eval'`), its `eval_results` |
| `nuvel pricing sync` or `nuvel pricing add`, except `--dry-run` | `model_prices`, one row per entry in the resolved `pricing.json` |

## Failure behaviour

Telemetry never fails what it observes.

- **Unconfigured or unreachable:** the run completes normally and the JSONL traces stay the only record. One warning is logged per outage, naming the exception type only, never the connection string.
- **Timing:** a run's write happens in the background after the run, so the database never delays a reply. Shutdown waits up to 4 seconds for writes in flight.
- **Recovery:** later runs try again and the warning re-arms once a write succeeds. Runs that completed during an outage are not backfilled; their record is in the JSONL.

## Idempotency

Each write is one transaction of upserts on a natural key, so writing the same run again leaves the row counts unchanged.

| Table | Key | On re-sync |
|---|---|---|
| `runs` | `run_id` | columns overwritten, `synced_at` moves |
| `trace_events` | `run_id, seq` | left as is |
| `eval_results` | `run_id, example_id` | columns overwritten |
| `model_prices` | `model` | overwritten; `updated_at` moves only if a price changed |

## Schema

Versioned SQL lives in `nuvel/platform_sync/migrations/`, applied in order under a transaction-scoped advisory lock and recorded in `nuvel_platform.schema_migrations`. Add a schema change as a new numbered file; never edit an applied one.

### `runs`

| Column | Type | Description |
|---|---|---|
| `run_id` | `text` PK | `kind = 'agent'`: ADK's invocation id, also in `run_start.invocation_id` in the JSONL. `kind = 'eval'`: `eval-` plus a hash of skill, suite, timestamp and model. |
| `kind` | `text` | `agent` or `eval`. |
| `agent` | `text` | Root agent name, or the skill for an eval. |
| `session_id`, `user_id`, `app_name` | `text` null | ADK identifiers. Null for evals. |
| `model` | `text` null | First model that answered, or the suite's model. |
| `started_at` | `timestamptz` | Run start. For evals, the suite's timestamp. |
| `duration_ms` | `bigint` null | Null for evals. |
| `llm_calls`, `tool_calls` | `integer` | Calls started (LLM) and finished (tool). |
| `error_count` | `integer` | LLM errors, tool exceptions and tool results with `status: "error"`. |
| `prompt_tokens`, `completion_tokens` | `bigint` | Totals for the run. |
| `cost_usd` | `double precision` null | Null when no price matched. For evals, the sum over examples. |
| `synced_at` | `timestamptz` | Last write. |

### `trace_events`

| Column | Type | Description |
|---|---|---|
| `run_id` | `text` PK, FK | Cascades when the run is deleted. |
| `seq` | `integer` PK | Position in the run, from 0. |
| `event` | `text` | See below. |
| `agent` | `text` null | Agent active at the event. |
| `occurred_at` | `timestamptz` | |
| `data` | `jsonb` | Payload, by event. |

The event names match the JSONL traces. The JSONL stays the full record: `trace_events` leaves out system prompts and message history.

| `event` | `data` keys |
|---|---|
| `run_start` | `user_input` |
| `llm_request` | `call_index`, `model`, `message_count`, `tools_available` |
| `llm_response` | `call_index`, `model_version`, `latency_ms`, `finish_reason`, `usage`, `cost_usd`, `response_text`, `function_calls` |
| `llm_error` | `call_index`, `model`, `error_type`, `error_message` |
| `tool_start` | `tool`, `args` |
| `tool_end` | `tool`, `status`, `duration_ms`, `result` |
| `tool_exception` | `tool`, `error_type`, `error_message`, `args` |
| `run_end` | `duration_ms`, `llm_calls`, `tool_calls`, `total_cost_usd` |

### `eval_results`

One row per example of an evalv2 suite run.

| Column | Type | Description |
|---|---|---|
| `run_id` | `text` PK, FK | The suite's `runs` row. |
| `example_id` | `text` PK | The example's id in the suite. |
| `skill`, `suite` | `text` | |
| `input` | `text` | The example's input. |
| `score` | `double precision` null | Composite 0..1. Null if never scored. |
| `passed` | `boolean` null | |
| `cache_hit` | `boolean` | The result came from the eval cache. |
| `cost_usd` | `double precision` | |
| `evaluator_results` | `jsonb` | List of `{evaluator, name, score, passed, details}`. |
| `notes` | `jsonb` | List of strings. |

### `model_prices`

| Column | Type | Description |
|---|---|---|
| `model` | `text` PK | The `pricing.json` key, for example `moonshotai/kimi-k2.5`. |
| `input_per_token`, `output_per_token` | `double precision` | USD per token. |
| `updated_at` | `timestamptz` | When a price last changed. |
