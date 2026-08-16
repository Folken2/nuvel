# Fork lifecycle: trigger to drain

This is the precise sequence a memory fork goes through — using the after-turn judge
fork (`review_fork.py`) as the concrete example, since it's the only fork wired today.
The same shape (throttle → sibling spawn → write-back → drain) applies to any future
fork built on `fork_utils.py` / `sibling_runner.py`.

## 1. Trigger point

`review_fork_callback` is registered as an ADK `after_agent_callback`. ADK invokes it
**after** the parent agent has finished producing its response for the turn — the
callback receives the `callback_context` for a turn whose reply has already been
assembled and is on its way back to the user. Nothing a fork does can delay or alter
that reply; the fork starts *after* the response path is already committed.

The callback's first move is the enabled check: `_enabled()` reads
`NUVEL_MEMORY_REVIEW_FORK` and returns immediately (no-op) if it isn't truthy. Only
past that gate does throttling get consulted.

## 2. Throttle check order — cap, then cooldown

`throttle.try_claim(state, fork_type)` is called next, before anything else happens.
The two checks run in this order, and the order matters:

1. **Per-session cap first** (`NUVEL_MEMORY_FORK_CAP`, default 50). If this fork type
   has already claimed a slot `cap` times in this session, `try_claim` returns `False`
   immediately — the cooldown clock is never even consulted. The cap is the absolute
   ceiling; it always wins.
2. **Cooldown second** (`NUVEL_MEMORY_FORK_COOLDOWN`, default 120s). Only checked if
   the cap hasn't been hit. If less than `cooldown` seconds have elapsed since this
   fork type's last claimed run, `try_claim` returns `False`.

Both checks read from and (on success) write to one dict in session state under
`throttle.STATE_KEY` (`"_nuvel_fork_throttle"`), keyed per fork type — so a review
fork and a future fork type of a different name throttle independently of each other.
A `False` return means "skip this turn": no error, no retry, the callback returns
`None` and the turn proceeds exactly as if the fork didn't exist. `state=None` (no
session state reachable) always claims — there's nothing to throttle against.

## 3. What state a fork sees

Once a slot is claimed, the callback builds the fork's input from the **parent's own
invocation context**, not from a fresh blank slate:

- `session.events` — the full list of ADK events for the session so far, passed
  through `fork_utils.format_conversation_snapshot()`, which strips pure-tool-call
  events (no text) and renders the rest as a `<CONVERSATION>...</CONVERSATION>` block
  tagged with each event's author. The fork sees the conversation, not the tool-call
  plumbing.
- `app_name` / `user_id` from the parent's invocation context — carried through
  unchanged so the fork's own session (see step 4) is created under the *same*
  identifiers the parent used. This is what makes a fork's memory writes visible to
  the parent's *next* turn: they land under the same `(app_name, user_id)` a later
  `search_memory()` call will query against.
- `memory_service` — the same `BaseMemoryService` instance (e.g. `OrgMemoryService`)
  attached to the parent's context, if any, passed straight through so fork writes go
  to the same backing store the parent reads from.

The fork agent itself never receives raw session **state** (the mutable dict tool
calls read/write) — only the rendered conversation text and its own restricted
toolset. It cannot see or touch arbitrary session-state keys the parent turn used.

## 4. Toolset whitelist

The judge `LlmAgent` is built with `tools=[save_memory, update_memory, list_skills,
read_skill, propose_skill]` and a `before_tool_callback` from
`fork_utils.make_whitelist_callback(allowed, fork_name="review")`. Any tool call
outside that allow-list — by name — is short-circuited with an error result handed
back to the model (`{"error": "Tool '<name>' is not available in the review fork..."}`),
never executed. This is enforced structurally, at the tool-call boundary, not by
prompting the judge to behave.

## 5. How results are written back

The judge writes durable facts via `save_memory` / `update_memory` — the same
function tools the parent agent's own tool layer exposes — so a fork's writes go
through the identical markdown-append (or memory-service-write) path a normal
tool call would use. There is no separate "fork memory" format. Skill proposals go
through `propose_skill`, which writes a markdown file into the human-review queue
(`NUVEL_SKILL_PROPOSALS_DIR`, default `~/.nuvel/skill-proposals`) — never into the
live skill catalog directly.

**Interaction rule — the stable prompt tier is off-limits.** A generated agent's
system prompt is assembled in three tiers (`prompt/instructions.py.tmpl`): a
**stable tier** (static identity/persona, meant to stay byte-identical across turns
so its prefix can be cached), a **session tier** (slow-changing, e.g. the structured
user-profile block `profile.py` renders), and a **volatile tier** (per-turn
reminders). No fork may write anything that ends up rendered into the stable tier —
doing so would invalidate the prompt-cache prefix on every subsequent turn and
defeat the reason that tier exists. Consolidation's structured profile
(`profile.render_profile_block`) is loaded into the **session** tier precisely to
respect this boundary; any future fork or curator output must be routed the same
way. The full tier contract — what belongs in each tier and why the boundary is
enforced — is documented in `adk-prompt-engineering`; treat that skill as the source
of truth for the contract, this rule as the specific consequence for forks.

## 6. Fire-and-forget dispatch

`SIBLING_RUNNER.spawn(agent=judge, prompt=..., app_name=..., user_id=...,
memory_service=..., log_prefix="review_fork")` is called inside a `try`/`except` in
`review_fork_callback` — a failure to even *schedule* the spawn (e.g. `spawn()`
itself raising) is caught and logged, never propagated. `spawn()` returns an
`asyncio.Task` immediately; the callback returns `None` right after, without
awaiting it. The parent agent's turn is fully done at this point from the caller's
perspective — the fork now runs independently on the event loop.

## 7. Drain at shutdown

`SiblingRunner` is registered as an ADK `BasePlugin` (name `"sibling_runner"`) purely
so ADK calls its `close()` hook during shutdown. `close()`:

1. Snapshots `self._pending` (the set of not-yet-done sibling tasks).
2. `await asyncio.wait(pending, timeout=self._drain_timeout)` — waits for tasks to
   finish, up to `NUVEL_MEMORY_SIBLING_DRAIN_TIMEOUT` (default 4.0s, hard-capped at
   4.5s regardless of what's configured, to stay under ADK's 5s plugin-close budget).
3. Any task still in `not_done` after the timeout is logged as dropped — its
   in-flight memory writes never complete. This is a silent data loss, by design: the
   alternative is blocking shutdown indefinitely.
4. If the wait itself is cancelled (e.g. the process is being killed harder than a
   graceful `close()` expects), the same outcome is logged and `close()` returns.

There is no persistence, retry, or replay for a dropped sibling task. A fork that was
mid-write when shutdown fired simply loses that write.

## 8. Fork failure must never fail the user's turn

Every layer in this chain treats fork failure as strictly isolated from the parent
turn:

- `review_fork_callback` wraps `SIBLING_RUNNER.spawn(...)` in `try`/`except Exception`
  and logs (`logger.exception`) rather than raising.
- `SiblingRunner._run_one` wraps the entire sibling `Runner.run_async(...)` loop in
  `try`/`except Exception` and logs rather than raising — a judge agent that errors,
  times out internally, or produces a malformed tool call never surfaces as an
  exception anywhere the parent could see.
- The tool-level whitelist callback returns an error *result* (a dict), not an
  exception, when the judge tries a disallowed tool — the judge's own turn continues
  gracefully rather than crashing.
- `close()`'s drain wraps its wait in `try`/`except (TimeoutError,
  asyncio.CancelledError)` so a stuck drain degrades to a warning log, not a crashed
  shutdown.

The invariant across all of it: **the user always gets their reply, on time, whether
or not the fork behind it ever runs, finishes, or fails.** A fork can lose work; it
must never cost the user anything they were waiting on.
