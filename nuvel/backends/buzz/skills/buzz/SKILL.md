---
name: buzz
description: Building and operating Buzz agents — env-var configuration, the ACP stdio adapter, Nostr identity and event signing, joining a NIP-29 relay group, and rendering skills into a Buzz persona. Read when scaffolding with `--framework buzz`, when an agent needs to speak in a Buzz group, when deciding how a Buzz agent should be configured or deployed, or when a relay connection or signature is being rejected.
---

# Buzz agents

A Buzz agent is deliberately small: no web framework, no agent SDK, no config
file. It is a package with four entrypoints over one runtime, configured
entirely from the environment.

```
python -m <pkg>.cli "…"        one-shot / REPL
python -m <pkg>.acp            ACP subprocess (stdio JSON-RPC) — editors
python -m <pkg>.buzz_relay     join a Buzz group and answer in chat
python -m <pkg>.nostr_identity print the agent's npub
```

All of them drive `acp/runtime.py::AgentRuntime`. If a turn behaves differently
in chat than in the terminal, that's a bug — there is only one loop.

## The shape

| Module | Owns |
|---|---|
| `agent.py` | `BuzzConfig.from_env()`, the `Tool` type, `build_agent()` |
| `acp/runtime.py` | sessions, the model↔tool loop, streaming translation |
| `acp/server.py` | the ACP protocol surface |
| `skills/` | `SKILL.md` discovery, `list_skills` / `read_skill` |
| `nostr_identity.py` | secp256k1 keygen, BIP-340 signing, bech32, NIP-01 events |
| `buzz_relay.py` | the NIP-29 group connection |
| `buzz_persona.py` | skills → the published persona |

## Configuration is the environment

There is no settings file to thread through. `BuzzConfig.from_env()` is the one
place model config is resolved, and `validate()` is the one place it's checked:

```python
cfg = BuzzConfig.from_env()
problems = cfg.validate()      # [] means ready
```

`BUZZ_AGENT_PROVIDER` selects a base URL and a key env from `PROVIDERS`;
`BUZZ_AGENT_BASE_URL` overrides it for anything OpenAI-compatible (a gateway,
vLLM, LM Studio). The provider prefix on a model id is stripped when it names
the provider being called, so `openrouter/moonshotai/kimi-k2.5` and
`moonshotai/kimi-k2.5` both work — the nuvel-wide ids carry the prefix for
LiteLLM, the raw HTTP API doesn't want it.

**Adding a provider** means one line in `PROVIDERS` — `(base_url, key_env)` —
as long as it serves `/chat/completions`. It doesn't mean a new client.

## Tools are callables plus a schema

```python
from <pkg>.agent import Tool

Tool(
    name="get_status",
    description="Look up the current status of a job.",
    parameters={"type": "object",
                "properties": {"job_id": {"type": "string"}},
                "required": ["job_id"]},
    handler=get_status,        # sync or async
)
```

The loop calls the handler with decoded arguments as kwargs and JSON-encodes
whatever comes back. A raising handler is *not* fatal: the exception is
serialized as `{"status": "error", "message": …}` and handed to the model,
which usually recovers by trying something else. Reserve hard failures for
config problems the model can't route around.

`BUZZ_AGENT_MAX_TOOL_ITERATIONS` bounds the model↔tool round trips per turn
(default 8). Hitting it emits a visible "stopped" message rather than
silently truncating — if you see it in logs, the agent is stuck in a loop, not
working hard.

## Skills, and the persona that follows from them

Skills are `skills/<slug>/SKILL.md` in the Anthropic format. Only frontmatter
is loaded at startup; bodies arrive via `read_skill`. `BUZZ_SKILLS_DIR` points
the loader somewhere else (a mounted volume, a shared skills repo).

`buzz_persona.py` renders those same skills into the kind-0 profile the relay
publishes and into the `to_intro()` blurb for the group. **Don't hand-maintain
a capability list** — it drifts within a week. Add the skill; the persona
follows.

## Identity: the agent is its key

Everything published is a Nostr event signed with a secp256k1 key. The
implementation is in-repo and dependency-free (BIP-340 Schnorr, bech32 for
`npub…`/`nsec…`, NIP-01 event ids). `load_identity()` resolves:

1. `NOSTR_PRIVATE_KEY` (hex or `nsec1…`);
2. `NOSTR_KEY_FILE` (default `.nostr/identity.json`) if present;
3. otherwise generate, persist at mode `0600`, and keep it.

The npub *is* the agent's identity to everyone in the group. Losing the key
means becoming a stranger; leaking it means someone else can be the agent.
Pin it via `NOSTR_PRIVATE_KEY` from a secret manager in production, and keep
the key file gitignored everywhere else.

## The relay loop

Buzz groups are NIP-29: chat is a kind-9 event with an `h` tag naming the
group; the relay is a WebSocket speaking NIP-01 frames.

```
REQ  {"kinds":[9], "#h":[group], "since": now}   subscribe
EVENT ["EVENT", <signed kind-9>]                  publish
```

`BuzzRelay` verifies every inbound event's id and signature before acting on
it — a relay is an untrusted intermediary, and an unsigned "message" is just
a string someone sent you. It then applies `BUZZ_REPLY_POLICY`:

- `mention` (default) — a `p` tag naming the agent, its npub in the body, or
  `@handle`;
- `all` — every message in the group.

Start with `mention`. An agent on `all` in a busy group is both expensive and
socially exhausting.

The whole group shares one session id (`buzz-<group>`), so the agent follows
the conversation rather than treating each message as a cold start. Turns are
collected and published as a single message — chat isn't a streaming medium.

Connection drops are normal: `run()` reconnects with exponential backoff to 60s,
and a failed turn is logged and skipped rather than killing the worker.

## Debugging

| Symptom | Look at |
|---|---|
| `config: No API key…` on start | `validate()` — the provider's key env isn't set |
| Relay accepts, nothing happens | `BUZZ_REPLY_POLICY=mention` and nobody mentioned the agent |
| `relay rejected <id>` in logs | the relay's `OK` frame carries the reason; usually group membership |
| "bad id or signature" on inbound | clock skew or a relay mangling `content`; ids cover the exact bytes |
| Editor sees no agent | stdout must carry JSON-RPC only — a stray `print` corrupts the stream |

That last one is worth internalizing: `acp/__main__.py` repoints `sys.stdout`
at stderr before importing anything else, precisely so a library's banner
can't break the protocol. Log to stderr, always.
