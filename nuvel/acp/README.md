# ACP — Agent Client Protocol v2 for Nuvel

## What is ACP?

[ACP](https://agentclientprotocol.com) (Agent Client Protocol) is a
standardised protocol for agent–editor communication defined by Block
(Jack Dorsey's Buzz).  It specifies **JSON-RPC 2.0 over NDJSON**
(newline-delimited JSON) over stdio — one JSON object per line on
stdin and stdout.

The protocol defines a lifecycle for agent sessions:

1. `initialize` — client and agent negotiate protocol version + capabilities
2. `session/new` — client creates a session (optionally with MCP servers)
3. `session/prompt` — client sends a prompt, agent streams updates
4. `session/cancel` — client cancels an in-flight prompt
5. `session/close` — client tears down the session

## Why we stole the actor pattern

Buzz discovered a subtle bug during development of their ACP
implementation ([Buzz issue #6671](https://github.com/jdorfman/awesome-json-datasets),
fixed in [PR #6675](https://github.com/jdorfman/awesome-json-datasets)):

**If a large NDJSON frame (>256 KiB) is written to the pipe and the
writer task is cancelled mid-write, the pipe contains a partial line.**
The next frame written by the next caller gets concatenated to that
partial line, corrupting the byte stream — the reader on the other
end tries to parse two concatenated (broken) lines as one JSON object
and fails.

The fix is the **cancel-safe stdin writer actor pattern** implemented
in `stdin_writer.py`:

- A single background actor task owns the write end exclusively.
- Callers submit complete frames via an `asyncio.Queue`.
- The actor dequeues frames one at a time and performs `write_all` +
  `flush` atomically.
- Callers can be cancelled safely — the actor either hasn't started
  the write yet or has already finished it.  **NDJSON framing is never
  truncated.**

## Architecture

```
┌──────────┐   stdin (NDJSON)   ┌─────────────────┐   stdout (NDJSON)   ┌──────────┐
│  Client   │ ─────────────────▶│   AcpServer      │ ─────────────────▶│  Client   │
│ (Buzz /   │                   │  ┌───────────┐   │                    │          │
│  editor)  │                   │  │ dispatch   │   │                    │          │
│           │                   │  │  loop      │──▶│  StdinWriter       │          │
│           │                   │  └───────────┘   │  ┌──────────────┐  │          │
│           │                   │                  │  │ Actor task   │──▶          │
│           │                   │  Handler          │  │ (Queue +     │  │          │
│           │                   │  callback         │  │  write_all)  │  │          │
└──────────┘                   └─────────────────┘  └──────────────┘  └──────────┘
```

- **`stdin_writer.py`** — Cancel-safe NDJSON writer actor.  Owns stdout
  exclusively.  All writes go through the actor so cancellation never
  truncates a frame.
- **`protocol.py`** — Python types mirroring the ACP v2 wire format:
  JSON-RPC messages, session updates, error codes, MCP server
  descriptors, NDJSON serialisation helpers.
- **`server.py`** — The main server skeleton: an `AcpServer` class
  that reads NDJSON from stdin, dispatches to typed method handlers
  (`initialize`, `session/new`, `session/prompt`, `session/cancel`,
  etc.), and writes responses through the `StdinWriter`.

## How it fits Nuvel

Nuvel-generated agents (Google ADK, Claude Agent SDK, Anthropic
Managed Agents) can run as ACP servers:

1. Scaffold an agent with `nuvel new --with-acp`
2. The generated project includes an `acp/` module that imports from
   `nuvel.acp` and wires the agent's prompt handler into the server.
3. Run the agent: `python -m my_agent.acp` (or via Buzz as a
   "custom harness").

This makes Nuvel agents **Buzz-compatible** — any editor that speaks
ACP (Buzz itself, VSCode with the Buzz extension, etc.) can drive a
Nuvel-generated agent as a subprocess.

## Next steps

- Wire `nuvel.acp` into the ADK scaffold overlay (`--with-acp` flag).
- Add the Claude Agent SDK and Anthropic Managed Agents ACP adapters.
- Register Nuvel agents as BYOH (Bring-Your-Own-Harness) presets in
  Buzz's runtime catalog.
- Add `session/resume` support for stateful sessions.
- Add MCP server lifecycle management inside the prompt handler.

## Files

| File | Purpose |
|------|---------|
| `__init__.py` | Public API exports |
| `stdin_writer.py` | Cancel-safe NDJSON writer actor (taken from Buzz #6675) |
| `protocol.py` | ACP v2 type definitions, enums, NDJSON helpers |
| `server.py` | ACP server skeleton (read/dispatch/write loop) |
| `README.md` | This document |