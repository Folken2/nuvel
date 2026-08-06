---
name: adk-cron-isolation
description: Blast-radius control for scheduled (cron) runs of an ADK agent — scoped secrets, the headless tool-approval policy, and HITL-gated job creation. Read when an agent runs jobs on a schedule with no human present, when a cron job needs credentials but shouldn't see every env var, when a scheduled tool call is being denied unexpectedly, or when tuning NUVEL_CRON_HEADLESS_POLICY / NUVEL_CRON_SCOPE_SECRETS / NUVEL_CRON_HITL_CREATE.
---

# Cron isolation for ADK agents

## Why scheduled runs need their own rules

A cron job runs unattended: nobody is at the keyboard to click "approve" when the agent asks to call a tool, and the job's secrets sit in the same process as every other credential the agent holds. Ordinary interactive turns rely on a human being present to approve risky actions; a scheduled run has no such backstop. `nuvel/backends/adk/templates/{{agent_package}}/cron/isolation.py` exists to give every scheduled run a bounded blast radius instead of inheriting the full trust of an interactive session.

## The three markers

`cron_isolation(job_id, secrets=..., headless=True)` is a context manager installed by the scheduler (`cron/scheduler.py:86`) around a single job invocation. It sets three async-local `ContextVar` markers and resets all three on exit, even on error:

1. **Cron-run marker** — records which `job_id` is currently running. Read via `active_cron_run()`, which returns `None` on ordinary (non-cron) turns and a `CronRun` dataclass during a scheduled run.
2. **Secret scope** — the env-var names the job's manifest declared, read via `active_secret_scope()`.
3. **Headless flag** — `is_headless()` reports `True` for the duration of the run, since no user is present to approve tool calls.

This is roughly what the scheduler does for every due job:

```python
# cron/scheduler.py — run_one_job()
with cron_isolation(job_id, secrets=job.get("secrets")):
    response = await invoker(job_id, prompt)
# markers are reset here, even if invoker() raised
```

Because these are `ContextVar`s (not process globals or `os.environ` mutation), concurrent web turns and overlapping cron jobs in the same process never see each other's scope — a `ContextVar` set inside one `asyncio` task's context is invisible to a sibling task's context, even though both run in the same process and the same event loop. That's what makes it safe to run several cron jobs concurrently (`asyncio.gather` in `_do_tick`) without one job's secret scope or headless flag leaking into another's.

## Scoped secrets

A job can declare `secrets=["SLACK_TOKEN"]` when it's created (the `cronjob` tool's `secrets` arg, comma-separated). With `NUVEL_CRON_SCOPE_SECRETS=1`, `resolve_cron_env(declared)` computes an env mapping containing only the names the job declared, and `active_cron_env()` returns that mapping for the duration of the run — everything else is masked. Without a declared `secrets` list, or with scoping disabled (the default), a job sees the full environment, matching pre-existing behavior for back-compat.

The threat model: a job that only needs `SLACK_TOKEN` to post a summary should not be able to read `STRIPE_SECRET_KEY` or `DATABASE_URL` just because they're in the same process. Scoping doesn't grant access to anything the job wouldn't already have — it narrows what a single unattended run can *see*, on the assumption that a compromised or misdirected prompt inside that run is the risk you're defending against, not the process boundary itself.

Declaring a scope, end to end:

```python
# 1. Create the job with a declared secrets list (cronjob tool, "create" action)
cronjob(
    action="create", name="slack-digest", schedule="every 1h",
    prompt="Summarize #eng-alerts and post a digest.",
    secrets="SLACK_TOKEN",   # comma-separated env-var names
)

# 2. With NUVEL_CRON_SCOPE_SECRETS=1, whatever the job's tools call sees only:
active_cron_env()  # -> {"SLACK_TOKEN": "..."}  (STRIPE_SECRET_KEY etc. absent)
```

`resolve_cron_env` distinguishes "declared nothing" (`None` → full env, back-compat) from "declared an empty scope" (`[]` → no env vars at all) on purpose — a job that never set a `secrets` field isn't accidentally locked out of credentials it always had, but a job that explicitly declares `[]` genuinely gets none.

## The headless policy — read this before deploying

**`NUVEL_CRON_HEADLESS_POLICY` defaults to `allow-shell` — and under that default, every non-shell tool is auto-denied.** This is the single most common surprise operators hit: a cron job that makes an HTTP call, writes to a database, or calls any tool that isn't on the shell allowlist will be silently denied at the moment it tries to run, with the denial only visible in the logged reason.

The three policies (`cron_isolation_plugin.py`):

- **`allow-shell`** (default) — shell/bin tools run inside the isolated cron scope and are auto-allowed; every other tool is auto-denied.
- **`deny-all`** — every tool is denied, no exceptions.
- **`allow-all`** — every tool is allowed; opts out of the gate entirely.

`evaluate_headless_tool(tool_name)` is the decision function — it returns `(allowed, reason)` and is only meaningful once a cron run is active. What counts as "shell" is `shell_tool_names()`, defaulting to `{shell, bash, sh, run_shell, run_command, execute, exec, terminal, process, bin}` (case-insensitive) and overridable via `NUVEL_CRON_SHELL_TOOLS` (comma-separated). `is_shell_tool(tool_name)` checks membership. If your agent's actual shell tool is named something else, add it to `NUVEL_CRON_SHELL_TOOLS` or the policy will deny it too.

If a scheduled job needs to call an API or write to a store, either name its tool in `NUVEL_CRON_SHELL_TOOLS` (if it's genuinely a sandboxed shell/bin call), switch that job's policy expectations to `allow-all`, or reconsider whether the action belongs in a cron job at all.

What a denial looks like from inside the run — `CronIsolationPlugin.before_tool_callback` intercepts the tool call and returns an error result instead of letting it execute:

```python
{
    "status": "error",
    "error": "headless_denied",
    "message": "headless cron policy 'allow-shell' blocks non-shell tool "
                "'http_get': no user is present to approve it",
    "headless_denied": True,
}
```

The agent sees this as a normal tool-error response and may retry, rephrase, or give up depending on your prompt — it is not a crash, which is exactly why it's easy to miss in a quick smoke test that only exercises shell tools.

## Inert outside cron runs

```python
# plugins/cron_isolation_plugin.py — before_tool_callback()
run = active_cron_run()
if run is None:
    # Not a scheduled run — leave every other approval path untouched.
    return None
```

That early return is the whole story: outside a `cron_isolation()` block, `active_cron_run()` is always `None`, so the plugin is a no-op on every ordinary interactive turn. It only ever engages inside the scope the scheduler installs around a job invocation. This is exactly why the plugin ships wired in by default — imported and instantiated as `cron_isolation` in `plugins/__init__.py.tmpl`, and present in `PLUGIN_INSTANCES`, the list the runner actually consumes — with no per-project setup required: it costs nothing on the vast majority of turns, and only activates for the narrow, genuinely-unattended case it was built for.

## HITL-gated job creation

`NUVEL_CRON_HITL_CREATE=1` changes what `CronService.create_job` does: instead of a new job landing as `active` and ticking on its next scheduled time, it lands as `pending` and stays inert until a human calls `confirm_job` (via `/cron confirm <id>` or the `cronjob` tool's `confirm` action), which promotes it to `active`. Confirming an already-active job is a no-op. Default is off, so existing generated agents keep firing jobs immediately on create.

The point isn't to gate *running* a job — it's to gate the agent giving itself recurring unattended execution in the first place. Without this, an agent that decides mid-conversation "I should check this every hour" can quietly install that behavior; with it, a human has to see and approve the schedule before it starts.

Separately, the `cronjob` tool refuses all mutating actions (`create`, `update`, `pause`, `resume`, `run`, `remove`, `confirm`) whenever `NUVEL_CRON_RUNNING=1` — i.e. when the call originates from inside a cron-spawned run itself. This is a recursion guard, not the HITL gate: it stops a running job from rescheduling or spawning more jobs mid-run, regardless of the HITL setting.

## When NOT to use

- **Don't reach for `allow-all` in production** to make a stubborn denial go away. It doesn't fix an under-scoped tool — it removes the gate for every tool on every cron job in the process, defeating the whole mechanism for jobs that didn't ask for that.
- **Secret scoping is not a substitute for least-privilege credentials upstream.** If a job's API key can delete a whole database, scoping which env vars it can *see* doesn't limit what that key can *do*. Scope the credential's actual permissions first; treat `NUVEL_CRON_SCOPE_SECRETS` as a second layer, not the only one.
- **If a job genuinely needs broad tool access**, prefer narrowing it to a shell tool that calls a single vetted script over switching the whole policy to `allow-all`. A shell call into a script you control is auditable and bounded; `allow-all` is not.

## Quick reference

| Variable | Default | What it does |
|---|---|---|
| `NUVEL_CRON_ENABLED` | unset (off) | Starts the background tick loop. HTTP routes and the `cronjob` tool work either way — this only gates automatic execution. |
| `NUVEL_CRON_TICK_SECONDS` | `60` | Tick interval in seconds. |
| `NUVEL_CRON_DIR` | `~/.nuvel/cron` | Storage directory for `jobs.json` + run outputs. |
| `NUVEL_CRON_SCOPE_SECRETS` | unset (off) | Enables secret scoping — jobs with a declared `secrets` list see only those env vars. |
| `NUVEL_CRON_HEADLESS_POLICY` | `allow-shell` | `allow-shell` \| `deny-all` \| `allow-all` — see above. |
| `NUVEL_CRON_SHELL_TOOLS` | `shell,bash,sh,run_shell,run_command,execute,exec,terminal,process,bin` | Comma-separated override for what counts as a shell/bin tool. |
| `NUVEL_CRON_HITL_CREATE` | unset (off) | Requires human confirmation before a new job starts ticking. |
| `NUVEL_CRON_RUNNING` (constant name `NUVEL_CRON_RUNNING_ENV` in `cron/service.py`) | — | **Runtime marker, not a user knob.** Set by the scheduler itself for the duration of a job invocation; used as a recursion guard by the `cronjob` tool. Don't set this in `.env`. |

Related: `adk-long-horizon-guardrails` for the broader agent-runaway story; `adk-long-horizon-sessions` for session lifecycle across long-running work.
