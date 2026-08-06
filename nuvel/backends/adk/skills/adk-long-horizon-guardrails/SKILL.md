---
name: adk-long-horizon-guardrails
description: >-
  Halt guards and command-safety guardrails for ADK agents — the shared halt
  latch, NoProgressGuard, RepeatedFailureGuard, the structural argv-level
  shell command classifier, and exfil_guard. Read when an agent will run
  unattended or for a long time, when a run needs a backstop against response
  loops or a tool retried identically forever, when an agent can execute
  shell commands, or when tool arguments might carry secrets. Also read when
  tuning EXFIL_GUARD_STRICT or debugging a "[halted: ...]" response.
---

# Long-horizon guardrails for ADK agents

## The two families

`nuvel/guardrails` ships two families of protection for agents that run for a long time or unattended (`guardrails/__init__.py:3-11`). This skill is the "stopping" half of long-horizon operation — for the "surviving" half (resumability across interruptions, event compaction so a growing session doesn't exhaust the context window), load `adk-long-horizon-sessions`.

- **Halt guards** — detect runaway loops (no progress, a tool failing identically) and latch a shared halt signal that `GuardrailsPlugin` consumes to short-circuit the model.
- **Command / exfiltration guards** — structurally classify shell commands for destructive operations and scan tool arguments for leaked secrets.

`GuardrailsPlugin` is wired unconditionally into every generated agent's plugin chain — instantiated at `plugins/__init__.py.tmpl:54` and registered in both chain listings, `PLUGIN_PATHS` (line 84) and `PLUGIN_INSTANCES` (line 107). This is not an opt-in feature you turn on for risky agents — every scaffolded ADK agent ships with the halt guards active from the first run. You can raise thresholds or change strictness (see "When NOT to use" below), but the chain itself is always there.

## The halt latch

Every halt guard funnels through one shared primitive, so start here.

`latch_halt(state, reason)` writes a reason into session state, but only if no halt is already set — the first guard to trip wins, and every later call is a no-op that returns `False`. This matters: the earliest, most specific reason is what the user sees, rather than whichever guard happened to run last.

`halt_consumer_callback` is a `before_model_callback`. While `state[HALT_REASON_STATE_KEY]` is set, it returns the canonical envelope from `halt_content(reason)` — `[halted: <reason>]` — instead of letting the model actually run, and it stamps `state[HALT_HANDOFF_DELIVERED_STATE_KEY] = True` on the way out.

```python
async def halt_consumer_callback(*, callback_context, llm_request=None):
    reason = callback_context.state.get(HALT_REASON_STATE_KEY)
    if not reason:
        return None                      # not halted — model call proceeds
    callback_context.state[HALT_HANDOFF_DELIVERED_STATE_KEY] = True
    return LlmResponse(content=halt_content(reason))
```

Two state keys carry the whole mechanism:

- `HALT_REASON_STATE_KEY` (`"halt_reason"`) — falsy/absent means not halted; a truthy string is the latched reason.
- `HALT_HANDOFF_DELIVERED_STATE_KEY` (`"__halt_handoff_delivered__"`) — set once, the first time the envelope is actually handed back, so a wrapper can distinguish a fresh halt from one already surfaced to the user.

`acknowledge_halt(state)` clears the reason so the next model call runs again, and `reset_halt_handoff(state)` clears the once-per-halt flag — both intended to run at a user-turn boundary, once the user has seen the halted response. Neither is called anywhere in the shipped plugin chain: wiring them into your turn boundary is on you. See `references/halt-latch-internals.md` for exactly why that matters and what breaks if you skip it.

## NoProgressGuard

`after_model_callback`. Tracks the model's text output turn over turn; if it emits byte-identical text `window` times in a row (default `5`), that's a response loop burning tokens with nothing to show, and the guard calls `latch_halt` with a `"no progress: ..."` reason. `window` must be `>= 2` — a window of 1 would halt on every single response.

## RepeatedFailureGuard

`after_tool_callback`. Each failing call is fingerprinted as `tool_name` plus a SHA-256 hash of its canonical (sorted-key) JSON-encoded arguments, so two calls with the same tool and same arguments — regardless of key order — collide on the same signature. When the same signature fails `threshold` consecutive times (default `3`), the guard latches a halt: the model is stuck retrying the exact same call. A success on that signature clears its streak, and stale streaks (older than 10 minutes) are pruned so an old transient failure doesn't count toward a halt hours later. The last failure's text lands in `state[LAST_ERROR_STATE_KEY]` (`"last_error"`) so a downstream system-reminder can show the model *why* its last call failed.

## GuardrailsPlugin

`GuardrailsPlugin` is one `BasePlugin` that binds the halt consumer, `NoProgressGuard`, and `RepeatedFailureGuard` to a single turn boundary, so all three observe the same notion of "turn":

- `before_model_callback` → `halt_consumer_callback`
- `after_model_callback` → `NoProgressGuard`
- `after_tool_callback` → `RepeatedFailureGuard`

Construct it with `GuardrailsPlugin(failure_threshold=3, no_progress_window=5)` to override either default; it's instantiated once per agent at `plugins/__init__.py.tmpl:54` and registered in the chain at `PLUGIN_PATHS` (line 84) and `PLUGIN_INSTANCES` (line 107).

## Command safety is structural, not textual

`command_safety.classify(command)` is the transferable insight in this subsystem: it lexes each shell segment into an argv list with `shlex` and inspects *tokens*, not the raw string. Substring/regex matching on the raw command is easy to defeat with quoting or spacing (`r'm -rf /'` vs `rm  -rf /` vs `rm -r -f /`); working on parsed tokens is not.

`segments(command)` splits a command line on `|`, `||`, `&&`, `;`, and bare `&`, and unwraps a single `bash -c '<inner>'` wrapper by recursing on the inner string — so a command smuggled through a shell launcher is judged on what it actually runs, not on the launcher.

`classify` returns the strongest verdict across every segment:

- `("deny", reason)` — catastrophic, block outright (e.g. `rm -rf /`, a recursive force-delete of a system or home root).
- `("ask", reason)` — risky, prompt for confirmation (e.g. `git push -f`, `git reset --hard`, a cloud CLI's destructive delete verb, piping into an interpreter).
- `None` — no opinion; the normal permission flow decides.

`command_classify` supplies the string-level helpers `classify` builds on: `strip_wrapper` (unwrap one `bash -c` shell wrapper), `split_segments` (quote-aware split on control operators), `command_prefix` (binary + subcommand, for auto-scoping approvals), `has_redirection`, and `has_command_substitution`. `lex` returns `None` on a parse error, and `classify` treats an unparseable command conservatively as `("ask", "command could not be parsed")` rather than silently allowing it.

## exfil_guard

`before_tool_callback`. Scans every tool call's arguments for high-confidence secret patterns — cloud access keys, private-key PEM blocks, provider API tokens (GitHub, Slack, OpenAI, Anthropic, Stripe), JWTs — before the tool runs. This catches the shape where the model reads a credential from the environment (or from an earlier tool result) and pastes it into an outbound call, like an HTTP request or a message send.

`EXFIL_GUARD_STRICT` controls the response: it defaults to `1` (strict). In strict mode the call is blocked outright and the model gets back an error result instead of the tool ever running. In lax mode the call proceeds, but a warning is stamped into `tool_context.state["exfil_warning"]` for a downstream logger or plugin to surface.

`exfil_guard` ships wired unconditionally, exactly like the halt guards: `agent.py.tmpl` imports it and registers it as `before_tool_callback=[exfil_guard]` on the agent, so every generated agent scans every tool call's arguments from the first run. This is a deliberate asymmetry within the "command / exfiltration guards" family: `exfil_guard` is auto-wired, but `command_safety.classify` (above) is **not** — it's a library function you call from your own tool's guard, not something the scaffold registers for you. Don't assume the two share a wiring status just because they live in the same module family.

## When NOT to use

These guards are default-on, but "default-on" doesn't mean "never tune":

- **Short interactive agents.** A latched halt is user-visible friction — the model stops producing new text and the user sees `[halted: ...]`. For a short back-and-forth chat agent where a human is watching every turn anyway, the halt guards add little and a false trip is annoying. They earn their keep on unattended or long-running agents.
- **An agent whose legitimate output is genuinely repetitive** — e.g. a monitoring agent that's supposed to report "no change" every cycle. Raise `no_progress_window` rather than disabling `NoProgressGuard`; disabling it removes your only backstop against an actual loop.
- **A tool that legitimately retries identical calls** — e.g. a flaky network call your own retry logic re-issues with the same arguments on purpose. Raise `failure_threshold` rather than disabling `RepeatedFailureGuard`, for the same reason.
- **`exfil_guard` strictness is a security decision, not a convenience one.** Setting `EXFIL_GUARD_STRICT=0` because a false positive is blocking a legitimate call trades a real security backstop for smoother development. Prefer narrowing the false positive (or accepting the friction) over disabling strict mode in anything that isn't a sandboxed dev environment.

## Quick reference

```bash
# Env vars
EXFIL_GUARD_STRICT=1        # default; 0/false/no = lax (flag instead of block)
```

| State key | Meaning |
|---|---|
| `HALT_REASON_STATE_KEY` (`halt_reason`) | Latched halt reason; falsy/absent = not halted |
| `HALT_HANDOFF_DELIVERED_STATE_KEY` (`__halt_handoff_delivered__`) | Set once the halt envelope has been handed back |
| `LAST_ERROR_STATE_KEY` (`last_error`) | Text of the most recent tool failure |

| Guard | Callback hook | Trips on |
|---|---|---|
| `halt_consumer_callback` | `before_model_callback` | any latched halt reason |
| `NoProgressGuard` | `after_model_callback` | `window` identical model responses in a row |
| `RepeatedFailureGuard` | `after_tool_callback` | `threshold` identical-signature tool failures in a row |
| `command_safety.classify` | (consulted by your tool's own guard, not auto-wired) | destructive/risky shell argv |
| `exfil_guard` | `before_tool_callback` (auto-wired) | secret-shaped tool arguments |

Internals of the halt latch — turn ordering, why first-write-wins, the handoff flag's once-per-halt semantics, and a worked example of authoring a custom guard — are in `references/halt-latch-internals.md`.
