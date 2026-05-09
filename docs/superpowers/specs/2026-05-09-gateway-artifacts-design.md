# Gateway Artifacts (Multimodal) — Design Spec

> **Date:** 2026-05-09
> **Status:** Approved
> **Goal:** End-to-end multimodal support across the Slack, Telegram, and Teams gateways: user-uploaded images/files reach the ADK agent as proper `Part` objects, and agent-emitted artifacts (inline blobs and `ArtifactService`-saved files) are uploaded back to the originating chat.
> **Scope:** ADK backend only.

---

## Context

The messaging gateways landed in [`2026-05-09-messaging-gateways-design.md`](2026-05-09-messaging-gateways-design.md) and are text-only on Slack and Telegram. The shared helper `gateways/_common.py::invoke_agent` accepts a `text: str` and returns a `str` — it never builds non-text `Part`s and never reads non-text parts from agent events.

Teams (sidecar over the ADK HTTP `/run` endpoint) is the exception: `teams_bridge.py` already (a) forwards Bot Framework attachments as `inline_data`/`file_data` parts when `TEAMS_FORWARD_RAW_ATTACHMENTS=true`, and (b) extracts inline/file parts from agent output and emits them as Bot Framework attachments. It does **not** read `actions.artifact_delta`, so `tool_context.save_artifact(...)` outputs are silently dropped. Teams is intentionally isolated from `_common.py` because the sidecar must remain importable without Google ADK dependencies.

Multimodal is the table-stakes feature for chat agents: users want to drop an image into Slack and ask "what's wrong with this chart"; agents that generate plots, PDFs, or audio need to send those back without copy-pasting URLs. This spec adds that.

### Non-goals (explicit)

- Voice/audio transcription. We forward bytes; the agent's model decides what to do.
- Streaming partial artifacts mid-conversation.
- Slack message threads with multi-file gallery composition beyond the per-message count cap.
- Microsoft Graph / SharePoint download for Teams attachments — we keep using `contentUrl`.
- Channels for `claude-agent-sdk` or `anthropic-managed-agents` backends.
- A new ADK HTTP endpoint for downloading artifacts (would be needed for Teams `artifact_delta` outbound; out of scope here).
- Built-in retry queues for outbound upload failures.

---

## Design

### 1. Architecture overview

Multimodal handling is a translation layer at the gateway boundary:

```
┌────────────┐    inbound   ┌─────────────┐   ADK Part    ┌─────────────┐
│  Platform  │ ───────────▶ │   Gateway   │ ────────────▶ │   Runner    │
│  (Slack/   │              │  adapter    │               │   (LlmAgent)│
│  Telegram/ │ ◀─────────── │ (download + │ ◀──────────── │             │
│  Teams)    │   outbound   │   upload)   │  Event parts  │             │
└────────────┘              └─────────────┘  + artifact_  └─────────────┘
                                              delta
```

Slack and Telegram run **in-process** and share `_common.py`. Teams runs in its **sidecar** and keeps its self-contained implementation, but is updated for parity on the inbound contract and improved on the outbound side.

### 2. Shared core (`gateways/_common.py`)

New dataclasses and helpers in `_common.py`:

```python
@dataclass
class InboundAttachment:
    mime_type: str
    display_name: str
    data: bytes | None = None      # preferred — bytes available
    file_uri: str | None = None    # fallback — URL only

@dataclass
class OutboundAttachment:
    mime_type: str
    display_name: str
    data: bytes | None = None
    file_uri: str | None = None

@dataclass
class AgentReply:
    text: str
    attachments: list[OutboundAttachment]
```

Helper functions:

- `attachments_to_parts(items: list[InboundAttachment]) -> list[Part]`
  - For each item: if `data` is not None and `len(data) <= GATEWAY_INLINE_DATA_MAX_BYTES`, emit `Part(inline_data=Blob(mime_type=..., data=...))`.
  - Else if `file_uri` is set, emit `Part(file_data=FileData(file_uri=..., mime_type=..., display_name=...))`.
  - Else (oversize bytes, no URI), emit a `Part(text="[attachment \"name\" skipped: <reason>]")` so the agent has a hint.
- `enforce_attachment_limits(items: list[InboundAttachment]) -> tuple[list[InboundAttachment], list[str]]`
  - Trims to `GATEWAY_MAX_ATTACHMENT_COUNT` and drops items with `data` over `GATEWAY_MAX_ATTACHMENT_BYTES` (kept items keep their bytes; the URI fallback is **not** auto-applied for oversize unless the platform pre-fetched a usable URL — caller's choice).
  - Returns the kept list plus a list of human-readable skip notes to splice into the prompt.

Updated signature:

```python
async def invoke_agent(
    runner: Runner,
    user_id: str,
    session_id: str,
    text: str,
    attachments: list[InboundAttachment] | None = None,
) -> AgentReply: ...
```

Behavior:

1. Build `Content(role="user", parts=[Part(text=text), *attachments_to_parts(attachments or [])])`.
2. Iterate `runner.run_async(...)` events. For each non-user event content part:
   - `text` → append to `texts`.
   - `inline_data` → `OutboundAttachment(data=blob.data, mime_type=blob.mime_type, display_name="agent-output")`.
   - `file_data` → `OutboundAttachment(file_uri=fd.file_uri, mime_type=fd.mime_type, display_name=fd.display_name or "agent-file")`.
3. For each event with `actions.artifact_delta` (a dict of `filename -> version`), if `runner.artifact_service` is configured: `await runner.artifact_service.load_artifact(app_name, user_id, session_id, filename, version)` and convert the resulting `Part` to an `OutboundAttachment`. If `artifact_service` is None, log once at INFO and skip.
4. Return `AgentReply(text=texts[-1] if texts else fallback, attachments=collected)`.

**Backwards compatibility:** the three existing callers are updated. We do not keep a string-returning shim. The function is internal to overlays and lives in scaffolded user repos, but those copies are regenerated on re-scaffold; the existing test suite's expectations are updated alongside the function.

**De-duplication of outbound:** if the same artifact appears both as an inline part and an `artifact_delta` (rare; only happens if a tool both returns and saves it), prefer the saved version (deterministic name) and drop the inline duplicate keyed on `(mime_type, len(data))`.

### 3. Slack adapter (`gateways/slack.py`)

**Inbound.**
Composio's `SLACKBOT_*_MESSAGE_RECEIVED` payloads include a `files: [...]` array. Each file has at minimum `id`, `mimetype`, `name`, `url_private` (an authenticated Slack CDN URL — bytes require a bot token).

- New env: `SLACK_BOT_TOKEN` — optional. If set, the gateway fetches each `url_private` with `Authorization: Bearer <token>` to get bytes → `InboundAttachment(data=...)`.
- If `SLACK_BOT_TOKEN` is unset and `files` are present, the gateway logs a warning once per startup and falls back to `InboundAttachment(file_uri=url_private)`. The agent will only be able to use it if its model can fetch authenticated URLs (most can't), so users will see a degraded experience until they add the token.
- Apply `enforce_attachment_limits`. Skipped attachments produce a prompt suffix: `[attachment "X.png" (12.3 MB) skipped: exceeds limit]`.

**Outbound.**
Use Composio's `SLACK_FILES_UPLOAD_V2` (or the equivalent current slug — verified at impl time via `composio search`) to upload bytes. For each `OutboundAttachment`:

- `data` is set → upload bytes; pass `channels=<channel>`, `thread_ts=<thread_ts>` (if in thread), `filename=<display_name>`, `filetype` from mime, optional `initial_comment` only on the **first** attachment when there is no companion text.
- `file_uri` only → append a markdown link line to the reply text rather than a separate API call.
- Errors: log + continue. The text reply still goes through `SLACKBOT_SEND_MESSAGE`.

### 4. Telegram adapter (`gateways/telegram.py`)

**Inbound.**
Telegram messages may contain any of: `photo` (array of resolutions, pick largest), `document`, `voice`, `audio`, `video`, `video_note`, `sticker` (only static — animated stickers are TGS, skip with note). For each:

1. Get `file_id` (largest size for `photo`).
2. `getFile` → `file_path`.
3. Download from `https://api.telegram.org/file/bot<TELEGRAM_BOT_TOKEN>/<file_path>`.
4. Map to mime: photos → `image/jpeg`, documents → `mime_type` from payload, voice → `audio/ogg`, video → `video/mp4`, etc.
5. `InboundAttachment(data=..., mime_type=..., display_name=file_name or f"{type}.{ext}")`.

Apply `enforce_attachment_limits`.

**Outbound.**
For each `OutboundAttachment`:

- Image mime + `data` → `sendPhoto` (multipart, field `photo`).
- Other + `data` → `sendDocument` (multipart, field `document`).
- `file_uri` only → `sendPhoto`/`sendDocument` with the URL as the `photo`/`document` form field (Telegram supports this for HTTPS URLs).
- The first call carries the reply text via `caption=`; subsequent calls have no caption.
- Reply target: `chat_id`, `message_thread_id` (if forum topic), and `reply_to_message_id` (in groups). Same logic as the existing text path.

If only text (no attachments), behavior is unchanged from today.

### 5. Teams adapter (`gateways/teams_bridge.py`)

The sidecar already handles inbound (`_extract_raw_attachment_parts`) and outbound for inline/file parts. Changes:

- **Rename envs to the unified set** while keeping backward-compat aliases for one release:
  - `TEAMS_MAX_ATTACHMENT_COUNT` → also accepts new `GATEWAY_MAX_ATTACHMENT_COUNT`.
  - `TEAMS_MAX_INLINE_B64_CHARS` stays Teams-specific (Bot Framework data-URI sizing is its own constraint — distinct from `GATEWAY_INLINE_DATA_MAX_BYTES`).
- **Inbound contract alignment:** when the inbound attachment has bytes (data URL), we emit `inline_data`; when only `contentUrl` is set, `file_data`. Already correct — no change.
- **Outbound `artifact_delta`:** **out of scope** for this spec. Reading saved artifacts requires an HTTP path through ADK that we don't want to add here. Teams continues to surface inline/file parts only. Documented limitation.

The sidecar continues to **not** import `_common.py`.

### 6. Limits and policy (env)

| Env | Default | Applies to |
|---|---|---|
| `GATEWAY_MAX_ATTACHMENT_COUNT` | `5` | per inbound message, all gateways |
| `GATEWAY_MAX_ATTACHMENT_BYTES` | `10485760` (10 MiB) | per inbound file, all gateways |
| `GATEWAY_INLINE_DATA_MAX_BYTES` | `4194304` (4 MiB) | bytes ≤ this → `inline_data`; else `file_data` |
| `SLACK_BOT_TOKEN` | unset | required for Slack inbound bytes; without it falls back to URI |
| `TEAMS_MAX_INLINE_B64_CHARS` | unchanged | Teams sidecar only |

When a file is dropped (oversize, count cap, fetch failure), append to the user prompt a one-line note per skip:

```
[attachment "report.pdf" (12.3 MB) skipped: exceeds GATEWAY_MAX_ATTACHMENT_BYTES (10 MB)]
```

This keeps the agent informed and the user model honest.

### 7. Failure handling

- **Inbound download failure** (network, 403): log at WARNING with file id/name, drop that attachment, append a skip note to the prompt. Other attachments and the message itself proceed.
- **ADK `artifact_service` unset** but `artifact_delta` present: log once at INFO. Reply text still flows.
- **Outbound upload failure**: log at ERROR with platform + file name. The text reply continues to be sent. We do not retry in this spec.
- **`SLACK_BOT_TOKEN` missing with files present**: log a startup-time warning the first time it occurs. We do not fail the message.

### 8. Tests

Extend the existing pytest suite. All tests mock `httpx`, Composio, and the ADK runner.

`tests/test_gateway_common.py`:
- `attachments_to_parts` — bytes under inline cap → `inline_data`; bytes over inline cap with `file_uri` → `file_data`; bytes over inline cap without `file_uri` → text-skip part.
- `enforce_attachment_limits` — count cap trims; oversize bytes dropped with notes.
- `invoke_agent` — text-only event sequence (existing); event with `inline_data` → reply.attachments includes it; event with `file_data` → ditto; event with `actions.artifact_delta` and a mock `artifact_service.load_artifact` → loaded bytes appear; same with `artifact_service=None` → skipped silently.
- Inbound + outbound de-dup rule.

`tests/test_gateway_slack.py`:
- Payload with `files[]` and `SLACK_BOT_TOKEN` set → `httpx` called with auth, parts created with bytes, runner invoked with multimodal `Content`.
- Same payload, `SLACK_BOT_TOKEN` unset → fallback `file_uri` part, warning logged.
- Agent emits inline image → `SLACK_FILES_UPLOAD_V2` invoked with channel, thread_ts, filename, bytes.
- Oversize file → skip note appears in prompt; runner sees only the trimmed part list.

`tests/test_gateway_telegram.py`:
- Payload with `photo` → `getFile` + download → multipart `Part(inline_data, image/jpeg)`.
- Payload with `document` → same flow with the document mime type.
- Agent emits inline image part → `sendPhoto` multipart with caption=text.
- Agent emits two attachments → first carries caption, second does not.
- Agent emits `OutboundAttachment(file_uri=..., data=None)` → `sendDocument` with URL form field.

`tests/test_gateway_teams_bridge.py`:
- Existing tests still pass.
- New env-alias test: `GATEWAY_MAX_ATTACHMENT_COUNT` is honored when `TEAMS_MAX_ATTACHMENT_COUNT` is unset.

### 9. Documentation

- Update each overlay's README section (`README.md` block in `gateway-{slack,telegram,teams}/{{agent_package}}/...`) with a "Multimodal" sub-section: supported types, env vars, size limits, known limitations.
- Update root `README.md` channels section: bullet about images/files in/out.
- Update the messaging-gateways spec with a short "See also" link to this design.

---

## File touch list

```
nuvel/backends/adk/templates_overlays/
  gateway-base/{{agent_package}}/gateways/_common.py        # major
  gateway-slack/{{agent_package}}/gateways/slack.py         # medium
  gateway-telegram/{{agent_package}}/gateways/telegram.py   # medium
  gateway-teams/{{agent_package}}/gateways/teams_bridge.py  # small (env aliases)
  gateway-{slack,telegram,teams}/.../README*.md             # docs
README.md                                                    # docs

tests/
  test_gateway_common.py    # extend
  test_gateway_slack.py     # extend
  test_gateway_telegram.py  # extend
  test_gateway_teams_bridge.py  # extend (env aliases)

docs/superpowers/specs/
  2026-05-09-gateway-artifacts-design.md  # this file
```

---

## Open questions

None at design approval. Implementation-time decisions (verified at code time, not in advance):

- Exact Composio Slack file-upload tool slug (`SLACK_FILES_UPLOAD_V2` vs current — confirm with `composio search` at impl time).
- Whether `Blob` and `FileData` import from `google.genai.types` are spelled the same in the project's pinned ADK version (already confirmed by reading existing `_common.py` imports).
