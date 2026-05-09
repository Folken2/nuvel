# Gateway Artifacts (Multimodal) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** End-to-end multimodal support across the Slack, Telegram, and Teams gateways: inbound user uploads become ADK `Part`s; outbound agent artifacts (event parts + ArtifactService deltas) become platform uploads. ADK backend only.

**Architecture:** Centralize attachment plumbing in the in-process `gateways/_common.py` (Slack + Telegram). Teams sidecar keeps its self-contained implementation; only env-var aliases are added there. New dataclasses (`InboundAttachment`, `OutboundAttachment`, `AgentReply`) carry bytes/URI metadata across the boundary. `invoke_agent` now returns `AgentReply` and reads `inline_data`, `file_data`, and `actions.artifact_delta` from runner events.

**Tech Stack:** Python 3.11+, FastAPI, httpx, pytest/unittest, Composio Python SDK (Slack), Telegram Bot API, Microsoft 365 Agents SDK (Teams sidecar), Google ADK (`google.adk.runners`, `google.genai.types`).

**Spec:** [`docs/superpowers/specs/2026-05-09-gateway-artifacts-design.md`](../specs/2026-05-09-gateway-artifacts-design.md)

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `nuvel/backends/adk/templates_overlays/gateway-base/{{agent_package}}/gateways/_common.py` | Shared types + helpers + `invoke_agent` | Major rewrite |
| `nuvel/backends/adk/templates_overlays/gateway-slack/{{agent_package}}/gateways/slack.py` | Slack inbound/outbound adapter | Add file download + upload paths |
| `nuvel/backends/adk/templates_overlays/gateway-telegram/{{agent_package}}/gateways/telegram.py` | Telegram inbound/outbound adapter | Add `getFile` + multipart sends |
| `nuvel/backends/adk/templates_overlays/gateway-teams/{{agent_package}}/gateways/teams_bridge.py` | Teams sidecar | Env-var alias only |
| `tests/test_gateway_common.py` | `_common` unit tests | Extend with attachment tests |
| `tests/test_gateway_slack.py` | Slack router tests | Extend with file in/out tests |
| `tests/test_gateway_telegram.py` | Telegram router tests | Extend with file in/out tests |
| `tests/test_gateway_teams_bridge.py` | Teams sidecar tests | One env-alias test |
| `nuvel/backends/adk/templates_overlays/gateway-{slack,telegram,teams}/.../README*` | Per-overlay docs | Add Multimodal section |
| `README.md` (repo root) | Channels section | Mention image/file in/out |

**Conventions you must follow:**

- All gateway code lives inside *templates*: paths contain literal `{{agent_package}}` (Cookiecutter-style) — keep them. Tests scaffold a fresh agent in tmpdir via `nuvel.backends.adk.scaffold.scaffold_agent` and import its generated modules dynamically (see existing `tests/test_gateway_*.py`).
- `from __future__ import annotations` at the top of every module.
- Use `logger = logging.getLogger(__name__)`. Prefer `logger.exception(...)` inside `except` blocks for unhandled errors, `logger.warning(...)` for expected user-misconfig conditions.
- Tests use `unittest.TestCase` + `unittest.mock.{AsyncMock, MagicMock, patch}`, mirroring the established style in `tests/test_gateway_*.py`. Do not introduce pytest fixtures — keep the file structure consistent.
- Run tests via `source .venv/bin/activate && pytest -q tests/test_gateway_<name>.py` from repo root. Activate the venv at `/Users/albertfolch/Documents/Cursor/nuvel/.venv` (the worktree shares the parent venv).
- Commit after each task with a `feat(gateway-<name>):` or `feat(gateway-common):` prefix; messages should focus on the *why*. Use `git -c commit.gpgsign=false commit -m ...` to avoid signing prompts.

---

## Task 1: `_common` types and pure helpers

**Goal:** Introduce dataclasses + `attachments_to_parts` + `enforce_attachment_limits` with no behavior change to `invoke_agent` yet. Pure functions, easy to test.

**Files:**
- Modify: `nuvel/backends/adk/templates_overlays/gateway-base/{{agent_package}}/gateways/_common.py`
- Test: `tests/test_gateway_common.py`

### Step 1.1 — Write the failing test class

Append a new `TestAttachmentHelpers` class to `tests/test_gateway_common.py`. It scaffolds the gateway-base overlay (any flag works; `with_telegram=True` is the cheapest), imports `_common`, and exercises both helpers.

- [ ] **Step 1.1: Add the failing test class**

```python
# tests/test_gateway_common.py — append below TestSessionKey

class TestAttachmentHelpers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp()
        cls.pkg = _scaffold_with(cls.tmpdir, with_telegram=True)
        cls.common = _import_module(cls.pkg, "gateways._common")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def test_inline_data_path(self):
        items = [self.common.InboundAttachment(
            mime_type="image/png", display_name="x.png", data=b"\x89PNG\x00" * 10,
        )]
        parts = self.common.attachments_to_parts(items, inline_max_bytes=10_000)
        self.assertEqual(len(parts), 1)
        self.assertIsNotNone(getattr(parts[0], "inline_data", None))
        self.assertEqual(parts[0].inline_data.mime_type, "image/png")

    def test_file_data_fallback_when_bytes_too_large(self):
        items = [self.common.InboundAttachment(
            mime_type="application/pdf", display_name="big.pdf",
            data=b"x" * 100, file_uri="https://example.com/big.pdf",
        )]
        parts = self.common.attachments_to_parts(items, inline_max_bytes=10)
        self.assertEqual(len(parts), 1)
        self.assertIsNotNone(getattr(parts[0], "file_data", None))
        self.assertEqual(parts[0].file_data.file_uri, "https://example.com/big.pdf")

    def test_text_skip_part_when_no_bytes_no_uri(self):
        items = [self.common.InboundAttachment(
            mime_type="image/png", display_name="orphan.png",
        )]
        parts = self.common.attachments_to_parts(items, inline_max_bytes=10_000)
        self.assertEqual(len(parts), 1)
        self.assertTrue(getattr(parts[0], "text", "").startswith("[attachment "))
        self.assertIn("orphan.png", parts[0].text)

    def test_enforce_count_cap_trims_excess(self):
        items = [
            self.common.InboundAttachment(mime_type="text/plain", display_name=f"f{i}.txt", data=b"hi")
            for i in range(7)
        ]
        kept, notes = self.common.enforce_attachment_limits(items, max_count=5, max_bytes=1024)
        self.assertEqual(len(kept), 5)
        self.assertEqual(len(notes), 2)
        self.assertIn("f5.txt", notes[0])

    def test_enforce_size_cap_drops_oversize(self):
        items = [
            self.common.InboundAttachment(mime_type="text/plain", display_name="ok.txt", data=b"hi"),
            self.common.InboundAttachment(mime_type="application/pdf", display_name="big.pdf", data=b"x" * 1000),
        ]
        kept, notes = self.common.enforce_attachment_limits(items, max_count=10, max_bytes=100)
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0].display_name, "ok.txt")
        self.assertEqual(len(notes), 1)
        self.assertIn("big.pdf", notes[0])
```

- [ ] **Step 1.2: Run the new tests and confirm they fail**

Run:
```
source .venv/bin/activate && pytest -q tests/test_gateway_common.py::TestAttachmentHelpers -v
```
Expected: All 5 tests fail with `AttributeError: module … has no attribute 'InboundAttachment'` (or similar) — the symbols don't exist yet.

- [ ] **Step 1.3: Implement the helpers in `_common.py`**

Add these to `nuvel/backends/adk/templates_overlays/gateway-base/{{agent_package}}/gateways/_common.py`. Place them between the imports and the existing `session_key` function.

Add to imports at the top:
```python
from dataclasses import dataclass, field
```

Add new dataclasses + helpers (insert directly after `logger = logging.getLogger(__name__)`):

```python
@dataclass
class InboundAttachment:
    """A platform-side file inbound to the agent.

    Either `data` (preferred) or `file_uri` should be set.
    """
    mime_type: str
    display_name: str
    data: bytes | None = None
    file_uri: str | None = None


@dataclass
class OutboundAttachment:
    """An agent-side artifact outbound to the platform."""
    mime_type: str
    display_name: str
    data: bytes | None = None
    file_uri: str | None = None


@dataclass
class AgentReply:
    text: str
    attachments: list[OutboundAttachment] = field(default_factory=list)


def _humanize_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def attachments_to_parts(
    items: list[InboundAttachment],
    *,
    inline_max_bytes: int,
) -> list[genai_types.Part]:
    """Convert inbound attachments to ADK Parts.

    - bytes ≤ inline_max_bytes → Part(inline_data=Blob)
    - else if file_uri set → Part(file_data=FileData)
    - else → Part(text=…) skip note (so the agent has a hint)
    """
    parts: list[genai_types.Part] = []
    for item in items:
        if item.data is not None and len(item.data) <= inline_max_bytes:
            parts.append(genai_types.Part(
                inline_data=genai_types.Blob(mime_type=item.mime_type, data=item.data)
            ))
            continue
        if item.file_uri:
            parts.append(genai_types.Part(
                file_data=genai_types.FileData(
                    file_uri=item.file_uri,
                    mime_type=item.mime_type,
                    display_name=item.display_name,
                )
            ))
            continue
        size_hint = _humanize_bytes(len(item.data)) if item.data is not None else "no bytes available"
        parts.append(genai_types.Part(
            text=f'[attachment "{item.display_name}" ({size_hint}) skipped: no usable representation]'
        ))
    return parts


def enforce_attachment_limits(
    items: list[InboundAttachment],
    *,
    max_count: int,
    max_bytes: int,
) -> tuple[list[InboundAttachment], list[str]]:
    """Trim list to max_count and drop items whose `data` exceeds max_bytes.

    Returns (kept_items, skip_notes). Each skip_note is a single-line string
    suitable for appending to the user's prompt.
    """
    kept: list[InboundAttachment] = []
    notes: list[str] = []
    for i, item in enumerate(items):
        if i >= max_count:
            notes.append(
                f'[attachment "{item.display_name}" skipped: exceeds GATEWAY_MAX_ATTACHMENT_COUNT ({max_count})]'
            )
            continue
        if item.data is not None and len(item.data) > max_bytes:
            notes.append(
                f'[attachment "{item.display_name}" ({_humanize_bytes(len(item.data))}) '
                f'skipped: exceeds GATEWAY_MAX_ATTACHMENT_BYTES ({_humanize_bytes(max_bytes)})]'
            )
            continue
        kept.append(item)
    return kept, notes
```

- [ ] **Step 1.4: Run the new tests and confirm they pass**

Run:
```
source .venv/bin/activate && pytest -q tests/test_gateway_common.py::TestAttachmentHelpers -v
```
Expected: 5 passed.

- [ ] **Step 1.5: Run the full gateway test set to confirm no regression**

Run:
```
source .venv/bin/activate && pytest -q tests/test_gateway_common.py tests/test_gateway_slack.py tests/test_gateway_telegram.py
```
Expected: all pre-existing tests still pass.

- [ ] **Step 1.6: Commit**

```
git add nuvel/backends/adk/templates_overlays/gateway-base/{{agent_package}}/gateways/_common.py tests/test_gateway_common.py
git -c commit.gpgsign=false commit -m "feat(gateway-common): add attachment dataclasses and pure helpers

Introduce InboundAttachment, OutboundAttachment, AgentReply and the
pure helpers attachments_to_parts and enforce_attachment_limits. No
caller changes yet — invoke_agent still text-only. Lays the groundwork
for multimodal support across Slack and Telegram."
```

---

## Task 2: `invoke_agent` returns `AgentReply` and reads multimodal output

**Goal:** Change `invoke_agent` to accept inbound attachments, build a multimodal `Content`, walk the event stream collecting text + `inline_data` + `file_data` + `actions.artifact_delta`, and return an `AgentReply`. Update the three current callers in this same task so the tree compiles.

**Files:**
- Modify: `nuvel/backends/adk/templates_overlays/gateway-base/{{agent_package}}/gateways/_common.py`
- Modify: `nuvel/backends/adk/templates_overlays/gateway-slack/{{agent_package}}/gateways/slack.py`
- Modify: `nuvel/backends/adk/templates_overlays/gateway-telegram/{{agent_package}}/gateways/telegram.py`
- Test: `tests/test_gateway_common.py`

### Step 2.1 — Write the failing tests for the new `invoke_agent`

Append a new `TestInvokeAgent` class to `tests/test_gateway_common.py`:

- [ ] **Step 2.1: Add the failing tests**

```python
# tests/test_gateway_common.py — append below TestAttachmentHelpers

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock


def _event(parts, *, author="agent", artifact_delta=None):
    """Build a minimal event-shaped object the runner emits."""
    content = SimpleNamespace(parts=parts)
    actions = SimpleNamespace(artifact_delta=artifact_delta or {})
    return SimpleNamespace(author=author, content=content, actions=actions)


def _async_iter(items):
    async def gen(*args, **kwargs):
        for x in items:
            yield x
    return gen


class TestInvokeAgent(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp()
        cls.pkg = _scaffold_with(cls.tmpdir, with_telegram=True)
        cls.common = _import_module(cls.pkg, "gateways._common")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def _runner(self, events, artifact_service=None):
        runner = MagicMock()
        runner.run_async = _async_iter(events)
        runner.app_name = "app-test"
        runner.artifact_service = artifact_service
        return runner

    def test_text_only_reply(self):
        Part = self.common.genai_types.Part
        events = [_event([Part(text="hello back")])]
        reply = asyncio.run(self.common.invoke_agent(
            self._runner(events), "u", "s", "hi"
        ))
        self.assertEqual(reply.text, "hello back")
        self.assertEqual(reply.attachments, [])

    def test_inline_data_in_event_becomes_outbound_attachment(self):
        Part = self.common.genai_types.Part
        Blob = self.common.genai_types.Blob
        events = [_event([
            Part(text="here is your image"),
            Part(inline_data=Blob(mime_type="image/png", data=b"\x89PNG")),
        ])]
        reply = asyncio.run(self.common.invoke_agent(
            self._runner(events), "u", "s", "draw me"
        ))
        self.assertEqual(reply.text, "here is your image")
        self.assertEqual(len(reply.attachments), 1)
        self.assertEqual(reply.attachments[0].mime_type, "image/png")
        self.assertEqual(reply.attachments[0].data, b"\x89PNG")

    def test_file_data_in_event_becomes_outbound_attachment(self):
        Part = self.common.genai_types.Part
        FileData = self.common.genai_types.FileData
        events = [_event([
            Part(file_data=FileData(
                file_uri="https://x/y.pdf", mime_type="application/pdf", display_name="y.pdf",
            )),
            Part(text="see attached"),
        ])]
        reply = asyncio.run(self.common.invoke_agent(
            self._runner(events), "u", "s", "give me y"
        ))
        self.assertEqual(reply.text, "see attached")
        self.assertEqual(len(reply.attachments), 1)
        self.assertEqual(reply.attachments[0].file_uri, "https://x/y.pdf")
        self.assertEqual(reply.attachments[0].display_name, "y.pdf")

    def test_artifact_delta_loaded_via_artifact_service(self):
        Part = self.common.genai_types.Part
        Blob = self.common.genai_types.Blob
        # Event with no inline parts but an artifact_delta entry
        events = [_event([Part(text="saved chart")], artifact_delta={"chart.png": 1})]

        loaded_part = Part(inline_data=Blob(mime_type="image/png", data=b"chartbytes"))
        artifact_service = MagicMock()
        artifact_service.load_artifact = AsyncMock(return_value=loaded_part)

        reply = asyncio.run(self.common.invoke_agent(
            self._runner(events, artifact_service=artifact_service), "u", "s", "make a chart"
        ))
        self.assertEqual(reply.text, "saved chart")
        self.assertEqual(len(reply.attachments), 1)
        self.assertEqual(reply.attachments[0].data, b"chartbytes")
        self.assertEqual(reply.attachments[0].display_name, "chart.png")
        artifact_service.load_artifact.assert_awaited_once()

    def test_artifact_delta_without_service_is_silently_skipped(self):
        Part = self.common.genai_types.Part
        events = [_event([Part(text="saved chart")], artifact_delta={"chart.png": 1})]
        reply = asyncio.run(self.common.invoke_agent(
            self._runner(events, artifact_service=None), "u", "s", "make a chart"
        ))
        self.assertEqual(reply.text, "saved chart")
        self.assertEqual(reply.attachments, [])

    def test_attachments_are_passed_to_runner_as_parts(self):
        Part = self.common.genai_types.Part
        events = [_event([Part(text="ok")])]
        runner = MagicMock()
        runner.app_name = "app-test"
        runner.artifact_service = None

        captured = {}
        async def fake_run_async(*, user_id, session_id, new_message):
            captured["new_message"] = new_message
            for ev in events:
                yield ev
        runner.run_async = fake_run_async

        items = [self.common.InboundAttachment(
            mime_type="image/png", display_name="x.png", data=b"\x89PNG",
        )]
        asyncio.run(self.common.invoke_agent(
            runner, "u", "s", "see this", attachments=items, inline_max_bytes=4_194_304,
        ))
        msg = captured["new_message"]
        self.assertEqual(msg.role, "user")
        # Expect one text part + one inline_data part
        self.assertEqual(len(msg.parts), 2)
        self.assertEqual(msg.parts[0].text, "see this")
        self.assertIsNotNone(getattr(msg.parts[1], "inline_data", None))
```

- [ ] **Step 2.2: Run the new tests and confirm they fail**

Run:
```
source .venv/bin/activate && pytest -q tests/test_gateway_common.py::TestInvokeAgent -v
```
Expected: failures — `invoke_agent` still returns `str`, doesn't take `attachments`, doesn't read inline/file/artifact_delta.

### Step 2.3 — Implement the new `invoke_agent`

- [ ] **Step 2.3: Replace `invoke_agent` in `_common.py`**

Locate the existing `invoke_agent` in `_common.py` (the function returning `str`) and replace it with the version below. Keep `genai_types` and `Runner` imports — they're already there.

```python
async def invoke_agent(
    runner: Runner,
    user_id: str,
    session_id: str,
    text: str,
    attachments: list[InboundAttachment] | None = None,
    *,
    inline_max_bytes: int = 4_194_304,
) -> AgentReply:
    """Run the agent in-process and return text + collected outbound artifacts.

    Reads three sources for outbound attachments on each non-user event:
      - `inline_data` parts (Blob)
      - `file_data` parts (FileData with file_uri)
      - `actions.artifact_delta` (loaded via runner.artifact_service if set)

    Inbound attachments are converted via `attachments_to_parts` and prepended
    after the user-text part.
    """
    parts: list[genai_types.Part] = [genai_types.Part(text=text)]
    if attachments:
        parts.extend(attachments_to_parts(attachments, inline_max_bytes=inline_max_bytes))
    new_message = genai_types.Content(role="user", parts=parts)

    texts: list[str] = []
    out_attachments: list[OutboundAttachment] = []
    seen_keys: set[tuple[str, int]] = set()

    artifact_service = getattr(runner, "artifact_service", None)
    app_name = getattr(runner, "app_name", "")

    async for event in runner.run_async(
        user_id=user_id, session_id=session_id, new_message=new_message
    ):
        if getattr(event, "author", None) == "user":
            continue

        # Walk content parts.
        content = getattr(event, "content", None)
        for part in (getattr(content, "parts", None) or []):
            piece = getattr(part, "text", None)
            if piece:
                texts.append(piece)
            inline = getattr(part, "inline_data", None)
            if inline is not None and getattr(inline, "data", None):
                key = (inline.mime_type, len(inline.data))
                if key not in seen_keys:
                    seen_keys.add(key)
                    out_attachments.append(OutboundAttachment(
                        mime_type=inline.mime_type,
                        display_name="agent-output",
                        data=inline.data,
                    ))
            fdata = getattr(part, "file_data", None)
            if fdata is not None and getattr(fdata, "file_uri", None):
                out_attachments.append(OutboundAttachment(
                    mime_type=getattr(fdata, "mime_type", "") or "application/octet-stream",
                    display_name=getattr(fdata, "display_name", "") or "agent-file",
                    file_uri=fdata.file_uri,
                ))

        # Walk artifact_delta entries (saved via tool_context.save_artifact).
        actions = getattr(event, "actions", None)
        delta = getattr(actions, "artifact_delta", None) or {}
        if delta and artifact_service is None:
            logger.info(
                "Gateway: agent emitted %d artifact(s) but no artifact_service is configured; skipping.",
                len(delta),
            )
            continue
        for filename, version in delta.items():
            try:
                loaded = await artifact_service.load_artifact(
                    app_name=app_name,
                    user_id=user_id,
                    session_id=session_id,
                    filename=filename,
                    version=version,
                )
            except Exception:
                logger.exception("Gateway: load_artifact failed for %s@%s", filename, version)
                continue
            if loaded is None:
                continue
            inline = getattr(loaded, "inline_data", None)
            fdata = getattr(loaded, "file_data", None)
            if inline is not None and getattr(inline, "data", None):
                key = (inline.mime_type, len(inline.data))
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                out_attachments.append(OutboundAttachment(
                    mime_type=inline.mime_type,
                    display_name=filename,
                    data=inline.data,
                ))
            elif fdata is not None and getattr(fdata, "file_uri", None):
                out_attachments.append(OutboundAttachment(
                    mime_type=getattr(fdata, "mime_type", "") or "application/octet-stream",
                    display_name=filename,
                    file_uri=fdata.file_uri,
                ))

    return AgentReply(
        text=texts[-1] if texts else "Agent did not return text.",
        attachments=out_attachments,
    )
```

### Step 2.4 — Update Slack call site

The existing call site is in `slack.py::_process`:

```python
reply = await invoke_agent(runner, user_id, session_id, text)
```

returns a `str` today and is passed straight to `_send_reply`. Change to handle the new `AgentReply` return type. **Outbound files are not yet uploaded in this task** — that's Task 4. For now we only forward the text, and any outbound attachments are logged + their `file_uri`s are appended to the reply as markdown links so we don't lose them silently.

- [ ] **Step 2.4: Patch `slack.py::_process`**

Replace the relevant lines in `nuvel/backends/adk/templates_overlays/gateway-slack/{{agent_package}}/gateways/slack.py` (currently `_process` lines ~72-89). New body:

```python
async def _process(request: Request, payload: dict, *, in_thread: bool) -> None:
    runner = request.app.state.runner
    app_name = request.app.state.app_name
    composio = request.app.state.composio_client

    user_id, session_id = session_key("slack", payload)
    await ensure_session(runner.session_service, app_name, user_id, session_id)

    text = str(payload.get("text") or "")
    try:
        reply = await invoke_agent(runner, user_id, session_id, text)
    except Exception:
        logger.exception("Slack: agent invocation failed")
        reply_text = "Sorry, something went wrong."
        outbound: list = []
    else:
        reply_text = reply.text
        outbound = reply.attachments

    # Stub outbound: if the agent emitted artifacts, append URI links so they're
    # not lost. Real upload via SLACK_FILES_UPLOAD_V2 lands in a follow-up task.
    if outbound:
        link_lines = [
            f"\n• {a.display_name}: {a.file_uri}" for a in outbound if a.file_uri
        ]
        if link_lines:
            reply_text = f"{reply_text}\n\nAttached:" + "".join(link_lines)
        for a in outbound:
            if a.data and not a.file_uri:
                logger.info("Slack: outbound attachment with bytes (%s, %d bytes) — upload deferred", a.display_name, len(a.data))

    channel = payload.get("channel")
    thread_ts = payload.get("thread_ts") or payload.get("ts") if in_thread else None
    await _send_reply(composio, channel, reply_text, thread_ts=thread_ts)
```

### Step 2.5 — Update Telegram call site

- [ ] **Step 2.5: Patch `telegram.py::_process_message`**

Replace lines `100-114` in `nuvel/backends/adk/templates_overlays/gateway-telegram/{{agent_package}}/gateways/telegram.py`. New body:

```python
async def _process_message(request: Request, msg: dict) -> None:
    runner = request.app.state.runner
    app_name = request.app.state.app_name
    user_id, session_id = session_key("telegram", msg)
    await ensure_session(runner.session_service, app_name, user_id, session_id)

    chat_id = (msg.get("chat") or {}).get("id")
    thread_id = msg.get("message_thread_id")
    reply_to = msg.get("message_id") if (msg.get("chat") or {}).get("type") != "private" else None

    keepalive = asyncio.create_task(_typing_keepalive(chat_id))
    try:
        reply = await invoke_agent(runner, user_id, session_id, msg["text"])
        reply_text = reply.text
        outbound = reply.attachments
    except Exception:
        logger.exception("Telegram: agent invocation failed")
        reply_text = "Sorry, something went wrong."
        outbound = []
    finally:
        keepalive.cancel()
        try:
            await keepalive
        except asyncio.CancelledError:
            pass

    # Stub outbound: append URI links so they're not lost. Real sendPhoto/sendDocument
    # uploads land in a follow-up task.
    if outbound:
        link_lines = [f"\n• {a.display_name}: {a.file_uri}" for a in outbound if a.file_uri]
        if link_lines:
            reply_text = f"{reply_text}\n\nAttached:" + "".join(link_lines)
        for a in outbound:
            if a.data and not a.file_uri:
                logger.info("Telegram: outbound attachment with bytes (%s, %d bytes) — upload deferred", a.display_name, len(a.data))

    await _send_message(chat_id, reply_text, reply_to=reply_to, message_thread_id=thread_id)
```

### Step 2.6 — Run tests

- [ ] **Step 2.6: Run common tests; confirm pass**

Run:
```
source .venv/bin/activate && pytest -q tests/test_gateway_common.py -v
```
Expected: all tests in `TestSessionKey`, `TestAttachmentHelpers`, `TestInvokeAgent` pass.

- [ ] **Step 2.7: Run the full test suite; confirm no regression**

Run:
```
source .venv/bin/activate && pytest -q
```
Expected: all 213 + the new tests pass.

If a Slack/Telegram test fails because of the `AgentReply.text` access, double-check Step 2.4 / 2.5 patches were applied correctly.

- [ ] **Step 2.8: Commit**

```
git add nuvel/backends/adk/templates_overlays/gateway-base/{{agent_package}}/gateways/_common.py \
        nuvel/backends/adk/templates_overlays/gateway-slack/{{agent_package}}/gateways/slack.py \
        nuvel/backends/adk/templates_overlays/gateway-telegram/{{agent_package}}/gateways/telegram.py \
        tests/test_gateway_common.py
git -c commit.gpgsign=false commit -m "feat(gateway-common): invoke_agent returns AgentReply with multimodal output

Read inline_data, file_data, and actions.artifact_delta from runner
events. Inbound attachments are converted to ADK Parts and prepended
to the user message. Slack and Telegram call sites updated; outbound
upload is stubbed (URI links appended; bytes-only attachments logged)
and lands in the per-channel tasks that follow."
```

---

## Task 3: Slack inbound — download files via `SLACK_BOT_TOKEN`

**Goal:** When Slack delivers a message with `files: [...]`, fetch each `url_private` using `SLACK_BOT_TOKEN`, build `InboundAttachment`s, enforce limits, and pass to `invoke_agent`.

**Files:**
- Modify: `nuvel/backends/adk/templates_overlays/gateway-slack/{{agent_package}}/gateways/slack.py`
- Test: `tests/test_gateway_slack.py`

### Step 3.1 — Write the failing tests

- [ ] **Step 3.1: Append to `TestSlackRouter`**

Add these methods at the bottom of `TestSlackRouter` in `tests/test_gateway_slack.py`. The existing `_client` helper accepts an extra env-patch dict — extend it first:

Replace `_client` with:
```python
def _client(self, runner_mock, composio_mock=None, env_extra=None):
    app = FastAPI()
    app.state.runner = runner_mock
    app.state.app_name = "sl-test"
    app.state.composio_client = composio_mock or MagicMock()
    app.include_router(self.sl.router)
    env = {"COMPOSIO_WEBHOOK_SECRET": "s3cret", **(env_extra or {})}
    with patch.dict("os.environ", env, clear=False):
        yield TestClient(app)
```

Then add the new tests:

```python
def test_dm_with_files_downloads_and_passes_attachments(self):
    """Files in payload are fetched with SLACK_BOT_TOKEN and forwarded to the runner."""
    runner = AsyncMock()
    runner.session_service = AsyncMock()
    runner.session_service.get_session = AsyncMock(return_value=None)
    runner.session_service.create_session = AsyncMock()

    captured = {}

    async def fake_invoke(_runner, _u, _s, text, attachments=None, **_kw):
        captured["text"] = text
        captured["attachments"] = attachments
        from types import SimpleNamespace
        return SimpleNamespace(text="ok", attachments=[])

    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.content = b"\x89PNG\x00fakebytes"
    fake_resp.headers = {"Content-Type": "image/png"}
    fake_resp.raise_for_status = MagicMock()

    with patch.object(self.sl, "invoke_agent", side_effect=fake_invoke), \
         patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=fake_resp):
        for client in self._client(runner, env_extra={"SLACK_BOT_TOKEN": "xoxb-test"}):
            r = client.post(
                "/gateways/slack/composio?secret=s3cret",
                json={
                    "trigger_slug": "SLACKBOT_DIRECT_MESSAGE_RECEIVED",
                    "payload": {
                        "team_id": "T01", "channel": "D456", "user": "U012",
                        "text": "what's this?", "ts": "1700000000.001", "channel_type": "im",
                        "files": [{
                            "id": "F1", "mimetype": "image/png", "name": "x.png",
                            "url_private": "https://files.slack.com/x.png", "size": 14,
                        }],
                    },
                },
            )
            self.assertEqual(r.status_code, 200)

    # Background task may run after the response — give the loop a tick:
    import asyncio, time
    for _ in range(50):
        if "attachments" in captured:
            break
        time.sleep(0.02)
    self.assertIn("attachments", captured)
    self.assertEqual(len(captured["attachments"]), 1)
    self.assertEqual(captured["attachments"][0].mime_type, "image/png")
    self.assertEqual(captured["attachments"][0].data, b"\x89PNG\x00fakebytes")
    self.assertEqual(captured["attachments"][0].display_name, "x.png")

def test_files_without_bot_token_fall_back_to_uri(self):
    runner = AsyncMock()
    runner.session_service = AsyncMock()
    runner.session_service.get_session = AsyncMock(return_value=None)
    runner.session_service.create_session = AsyncMock()
    captured = {}

    async def fake_invoke(_r, _u, _s, text, attachments=None, **_kw):
        captured["attachments"] = attachments
        from types import SimpleNamespace
        return SimpleNamespace(text="ok", attachments=[])

    with patch.object(self.sl, "invoke_agent", side_effect=fake_invoke):
        for client in self._client(runner):  # no SLACK_BOT_TOKEN
            r = client.post(
                "/gateways/slack/composio?secret=s3cret",
                json={
                    "trigger_slug": "SLACKBOT_DIRECT_MESSAGE_RECEIVED",
                    "payload": {
                        "team_id": "T01", "channel": "D456", "user": "U012",
                        "text": "look", "ts": "1700000000.001", "channel_type": "im",
                        "files": [{"id": "F1", "mimetype": "image/png", "name": "x.png",
                                   "url_private": "https://files.slack.com/x.png", "size": 14}],
                    },
                },
            )
            self.assertEqual(r.status_code, 200)

    import time
    for _ in range(50):
        if "attachments" in captured:
            break
        time.sleep(0.02)
    self.assertIn("attachments", captured)
    self.assertEqual(len(captured["attachments"]), 1)
    self.assertIsNone(captured["attachments"][0].data)
    self.assertEqual(captured["attachments"][0].file_uri, "https://files.slack.com/x.png")
```

- [ ] **Step 3.2: Run new tests; confirm they fail**

Run:
```
source .venv/bin/activate && pytest -q tests/test_gateway_slack.py -v
```
Expected: the two new tests fail (no file-downloading code yet), the rest pass.

### Step 3.3 — Implement Slack inbound file fetching

- [ ] **Step 3.3: Add `_collect_inbound_files` and update `_process` in `slack.py`**

In `nuvel/backends/adk/templates_overlays/gateway-slack/{{agent_package}}/gateways/slack.py`:

Add `httpx` import at the top (alongside the existing imports):
```python
import httpx
```

Update the `_common` import to include the attachment types:
```python
from {{agent_package}}.gateways._common import (
    InboundAttachment,
    enforce_attachment_limits,
    ensure_session,
    invoke_agent,
    session_key,
)
```

Add a module-level helper above `_process`:

```python
async def _download_slack_file(url: str, token: str) -> bytes | None:
    """Download a Slack file via its url_private with bot-token auth."""
    headers = {"Authorization": f"Bearer {token}"}
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(url, headers=headers)
            r.raise_for_status()
            return r.content
    except Exception:
        logger.exception("Slack: failed to download file %s", url)
        return None


async def _collect_inbound_files(payload: dict) -> tuple[list[InboundAttachment], list[str]]:
    """Build InboundAttachment list for Slack `files[]`.

    Returns (kept_attachments, skip_notes_for_prompt).
    """
    files = payload.get("files") or []
    if not isinstance(files, list) or not files:
        return [], []

    bot_token = os.environ.get("SLACK_BOT_TOKEN")
    if not bot_token:
        logger.warning(
            "Slack: payload contains %d file(s) but SLACK_BOT_TOKEN is unset — "
            "falling back to URL forwarding (most agent models cannot fetch authenticated URLs).",
            len(files),
        )

    items: list[InboundAttachment] = []
    for f in files:
        if not isinstance(f, dict):
            continue
        url = str(f.get("url_private") or f.get("url_private_download") or "")
        mime = str(f.get("mimetype") or "application/octet-stream")
        name = str(f.get("name") or "slack-file")
        data: bytes | None = None
        if bot_token and url:
            data = await _download_slack_file(url, bot_token)
        items.append(InboundAttachment(
            mime_type=mime, display_name=name, data=data, file_uri=url or None,
        ))

    max_count = int(os.environ.get("GATEWAY_MAX_ATTACHMENT_COUNT", "5"))
    max_bytes = int(os.environ.get("GATEWAY_MAX_ATTACHMENT_BYTES", str(10 * 1024 * 1024)))
    return enforce_attachment_limits(items, max_count=max_count, max_bytes=max_bytes)
```

Then update `_process` to use it:

```python
async def _process(request: Request, payload: dict, *, in_thread: bool) -> None:
    runner = request.app.state.runner
    app_name = request.app.state.app_name
    composio = request.app.state.composio_client

    user_id, session_id = session_key("slack", payload)
    await ensure_session(runner.session_service, app_name, user_id, session_id)

    text = str(payload.get("text") or "")
    attachments, skip_notes = await _collect_inbound_files(payload)
    if skip_notes:
        text = text + ("\n" + "\n".join(skip_notes) if text else "\n".join(skip_notes))

    inline_max_bytes = int(os.environ.get("GATEWAY_INLINE_DATA_MAX_BYTES", str(4 * 1024 * 1024)))
    try:
        reply = await invoke_agent(
            runner, user_id, session_id, text,
            attachments=attachments, inline_max_bytes=inline_max_bytes,
        )
    except Exception:
        logger.exception("Slack: agent invocation failed")
        reply_text = "Sorry, something went wrong."
        outbound: list = []
    else:
        reply_text = reply.text
        outbound = reply.attachments

    # Outbound upload comes in Task 4; for now pass through URI-only attachments as links.
    if outbound:
        link_lines = [f"\n• {a.display_name}: {a.file_uri}" for a in outbound if a.file_uri]
        if link_lines:
            reply_text = f"{reply_text}\n\nAttached:" + "".join(link_lines)
        for a in outbound:
            if a.data and not a.file_uri:
                logger.info("Slack: outbound attachment with bytes (%s, %d bytes) — upload deferred", a.display_name, len(a.data))

    channel = payload.get("channel")
    thread_ts = payload.get("thread_ts") or payload.get("ts") if in_thread else None
    await _send_reply(composio, channel, reply_text, thread_ts=thread_ts)
```

- [ ] **Step 3.4: Run Slack tests; confirm pass**

Run:
```
source .venv/bin/activate && pytest -q tests/test_gateway_slack.py -v
```
Expected: all tests pass.

- [ ] **Step 3.5: Run full suite; confirm no regression**

Run:
```
source .venv/bin/activate && pytest -q
```
Expected: green.

- [ ] **Step 3.6: Commit**

```
git add nuvel/backends/adk/templates_overlays/gateway-slack/{{agent_package}}/gateways/slack.py tests/test_gateway_slack.py
git -c commit.gpgsign=false commit -m "feat(gateway-slack): inbound file download via SLACK_BOT_TOKEN

Slack messages with files[] now fetch each url_private with bot-token
auth and forward bytes to the agent as inline_data. Without the token
we fall back to file_data with the URL and emit a startup-time warning.
GATEWAY_MAX_ATTACHMENT_{COUNT,BYTES} are honored; oversize/over-cap
files are dropped with a note appended to the user prompt."
```

---

## Task 4: Slack outbound — upload via Composio file-upload tool

**Goal:** When the agent reply has `attachments` with bytes, upload each via Composio's Slack file-upload tool. URI-only attachments stay as link lines (status quo).

**First action of this task:** verify the exact Composio tool slug. Run `composio search slack files upload` (or check the Composio docs page if `composio` CLI is unavailable). The most recent slug at time of writing is `SLACK_FILES_UPLOAD_V2`. Adjust the constant if it has changed.

**Files:**
- Modify: `nuvel/backends/adk/templates_overlays/gateway-slack/{{agent_package}}/gateways/slack.py`
- Test: `tests/test_gateway_slack.py`

### Step 4.1 — Verify Composio slug

- [ ] **Step 4.1: Verify the Composio Slack file-upload tool slug**

Run (best-effort, do not fail the task if the CLI isn't installed):
```
composio search slack files upload 2>/dev/null || echo "composio CLI not available — assuming SLACK_FILES_UPLOAD_V2 from spec"
```
Use whichever slug the search returns (or `SLACK_FILES_UPLOAD_V2` as the documented fallback) for the constant `SLACK_FILES_UPLOAD_TOOL` introduced below.

### Step 4.2 — Failing test

- [ ] **Step 4.2: Add a failing test**

Append to `TestSlackRouter`:

```python
def test_outbound_inline_image_uploads_via_composio(self):
    runner = AsyncMock()
    runner.session_service = AsyncMock()
    runner.session_service.get_session = AsyncMock(return_value=None)
    runner.session_service.create_session = AsyncMock()

    # Build an AgentReply with one inline outbound attachment.
    common = self.sl  # import sibling
    Reply = self.sl.AgentReply if hasattr(self.sl, "AgentReply") else None
    # Fall back to importing from _common via the existing module path:
    if Reply is None:
        from sl_test.gateways._common import AgentReply, OutboundAttachment
    else:
        from sl_test.gateways._common import OutboundAttachment

    reply = AgentReply(text="here you go", attachments=[
        OutboundAttachment(mime_type="image/png", display_name="chart.png", data=b"\x89PNGdata"),
    ])

    async def fake_invoke(*_a, **_kw):
        return reply

    composio = MagicMock()
    composio.tools.execute = MagicMock(return_value={"ok": True})

    with patch.object(self.sl, "invoke_agent", side_effect=fake_invoke):
        for client in self._client(runner, composio_mock=composio):
            r = client.post(
                "/gateways/slack/composio?secret=s3cret",
                json={
                    "trigger_slug": "SLACKBOT_DIRECT_MESSAGE_RECEIVED",
                    "payload": {
                        "team_id": "T01", "channel": "D456", "user": "U012",
                        "text": "draw", "ts": "1700000000.001", "channel_type": "im",
                    },
                },
            )
            self.assertEqual(r.status_code, 200)

    import time
    for _ in range(50):
        if composio.tools.execute.called:
            break
        time.sleep(0.02)

    # Find the upload call (there may also be a SLACKBOT_SEND_MESSAGE call).
    upload_calls = [c for c in composio.tools.execute.call_args_list
                    if c.args and c.args[0] == self.sl.SLACK_FILES_UPLOAD_TOOL]
    self.assertEqual(len(upload_calls), 1)
    args = upload_calls[0].kwargs.get("arguments") or upload_calls[0].args[1]
    self.assertEqual(args["channel"], "D456")
    self.assertEqual(args["filename"], "chart.png")
    self.assertEqual(args["filetype"], "png")
    # data was b64-encoded
    import base64
    self.assertEqual(base64.b64decode(args["content_b64"]), b"\x89PNGdata")
```

- [ ] **Step 4.3: Run; confirm fail**

Run:
```
source .venv/bin/activate && pytest -q tests/test_gateway_slack.py::TestSlackRouter::test_outbound_inline_image_uploads_via_composio -v
```
Expected: AttributeError on `self.sl.SLACK_FILES_UPLOAD_TOOL` — not implemented yet.

### Step 4.4 — Implement outbound upload

- [ ] **Step 4.4: Add upload helper and wire it into `_process`**

In `slack.py`, add module-level constant and helper:

```python
import base64

# Verified at impl time (Task 4.1). Adjust if Composio slug has changed.
SLACK_FILES_UPLOAD_TOOL = "SLACK_FILES_UPLOAD_V2"


def _filetype_from_mime(mime: str) -> str:
    """Map mime to Slack `filetype` shortcut."""
    if "/" in mime:
        return mime.split("/", 1)[1].split(";")[0] or "auto"
    return "auto"


async def _upload_attachment(
    composio_client,
    *,
    channel: str,
    thread_ts: str | None,
    attachment,  # OutboundAttachment
    initial_comment: str | None,
) -> None:
    if not attachment.data:
        return  # URI-only handled by caller.
    args = {
        "channel": channel,
        "filename": attachment.display_name or "agent-output",
        "filetype": _filetype_from_mime(attachment.mime_type),
        "content_b64": base64.b64encode(attachment.data).decode("ascii"),
    }
    if thread_ts:
        args["thread_ts"] = thread_ts
    if initial_comment:
        args["initial_comment"] = initial_comment
    try:
        await asyncio.to_thread(
            composio_client.tools.execute, SLACK_FILES_UPLOAD_TOOL, arguments=args,
        )
    except Exception:
        logger.exception("Slack: %s failed for %s", SLACK_FILES_UPLOAD_TOOL, attachment.display_name)
```

Then update `_process` to call it. Replace the "Outbound upload comes in Task 4" stub block with:

```python
    # Outbound: upload attachments with bytes; URI-only become link lines.
    uri_only = [a for a in outbound if a.file_uri and not a.data]
    bytes_attachments = [a for a in outbound if a.data]

    if uri_only:
        link_lines = [f"\n• {a.display_name}: {a.file_uri}" for a in uri_only]
        reply_text = f"{reply_text}\n\nAttached:" + "".join(link_lines)

    channel = payload.get("channel")
    thread_ts = payload.get("thread_ts") or payload.get("ts") if in_thread else None

    if bytes_attachments:
        # First file carries the reply text as initial_comment; the text send
        # below is suppressed when we used initial_comment.
        first, *rest = bytes_attachments
        await _upload_attachment(
            composio, channel=channel, thread_ts=thread_ts,
            attachment=first, initial_comment=reply_text or None,
        )
        for a in rest:
            await _upload_attachment(
                composio, channel=channel, thread_ts=thread_ts,
                attachment=a, initial_comment=None,
            )
        # Skip the duplicate text send.
        return

    await _send_reply(composio, channel, reply_text, thread_ts=thread_ts)
```

Make sure to also import `OutboundAttachment` from `_common` if you want type hints; not strictly required for the runtime.

- [ ] **Step 4.5: Run new test; confirm pass**

Run:
```
source .venv/bin/activate && pytest -q tests/test_gateway_slack.py -v
```
Expected: all pass.

- [ ] **Step 4.6: Run full suite**

Run:
```
source .venv/bin/activate && pytest -q
```
Expected: green.

- [ ] **Step 4.7: Commit**

```
git add nuvel/backends/adk/templates_overlays/gateway-slack/{{agent_package}}/gateways/slack.py tests/test_gateway_slack.py
git -c commit.gpgsign=false commit -m "feat(gateway-slack): outbound artifact upload via Composio

Inline-byte outbound attachments are uploaded with SLACK_FILES_UPLOAD_V2,
the first file carrying the reply text as initial_comment so the channel
gets a single coherent post. URI-only attachments continue to surface as
markdown link lines. Failures log and continue rather than swallow the
text reply."
```

---

## Task 5: Telegram inbound — accept photos and documents

**Goal:** Accept Telegram updates that contain `photo`, `document`, `voice`, `audio`, `video`, or `video_note`, fetch bytes via `getFile`, and forward to `invoke_agent`. Today the gateway short-circuits non-text updates with a 200 noop — that gate must change.

**Files:**
- Modify: `nuvel/backends/adk/templates_overlays/gateway-telegram/{{agent_package}}/gateways/telegram.py`
- Test: `tests/test_gateway_telegram.py`

### Step 5.1 — Failing test

- [ ] **Step 5.1: Append a `_with_files` test method to `TestTelegramRouter`**

Add at the bottom of `TestTelegramRouter`:

```python
def test_message_with_photo_downloads_and_invokes(self):
    runner = AsyncMock()
    runner.session_service = AsyncMock()
    runner.session_service.get_session = AsyncMock(return_value=None)
    runner.session_service.create_session = AsyncMock()

    captured = {}

    async def fake_invoke(_runner, _u, _s, text, attachments=None, **_kw):
        captured["text"] = text
        captured["attachments"] = attachments
        from types import SimpleNamespace
        return SimpleNamespace(text="ok", attachments=[])

    # Two httpx.AsyncClient.post calls: getFile, then sendMessage. Use side_effect.
    get_file_resp = MagicMock()
    get_file_resp.status_code = 200
    get_file_resp.json = MagicMock(return_value={"ok": True, "result": {"file_path": "photos/x.jpg"}})
    get_file_resp.raise_for_status = MagicMock()

    send_msg_resp = MagicMock()
    send_msg_resp.status_code = 200
    send_msg_resp.text = "{}"

    download_resp = MagicMock()
    download_resp.status_code = 200
    download_resp.content = b"\xff\xd8\xff\xe0fakejpg"
    download_resp.raise_for_status = MagicMock()

    async def fake_post(self_client, url, *args, **kwargs):
        if "/getFile" in url:
            return get_file_resp
        if "/sendChatAction" in url:
            return MagicMock(status_code=200)
        return send_msg_resp

    async def fake_get(self_client, url, *args, **kwargs):
        return download_resp

    with patch.object(self.tg, "invoke_agent", side_effect=fake_invoke), \
         patch("httpx.AsyncClient.post", new=fake_post), \
         patch("httpx.AsyncClient.get", new=fake_get):
        for client in self._client(runner):
            r = client.post(
                "/gateways/telegram",
                json={
                    "update_id": 1,
                    "message": {
                        "message_id": 1,
                        "chat": {"id": 999, "type": "private"},
                        "from": {"id": 555},
                        "caption": "what's this?",
                        "photo": [
                            {"file_id": "small", "file_size": 100, "width": 100, "height": 100},
                            {"file_id": "big", "file_size": 5000, "width": 800, "height": 600},
                        ],
                    },
                },
                headers={"X-Telegram-Bot-Api-Secret-Token": "testsecret"},
            )
            self.assertEqual(r.status_code, 200)

    import time
    for _ in range(50):
        if "attachments" in captured:
            break
        time.sleep(0.02)
    self.assertIn("attachments", captured)
    self.assertEqual(len(captured["attachments"]), 1)
    self.assertEqual(captured["attachments"][0].mime_type, "image/jpeg")
    self.assertEqual(captured["attachments"][0].data, b"\xff\xd8\xff\xe0fakejpg")
    self.assertEqual(captured["text"], "what's this?")

def test_message_with_document_passes_mime(self):
    runner = AsyncMock()
    runner.session_service = AsyncMock()
    runner.session_service.get_session = AsyncMock(return_value=None)
    runner.session_service.create_session = AsyncMock()
    captured = {}

    async def fake_invoke(_r, _u, _s, text, attachments=None, **_kw):
        captured["attachments"] = attachments
        from types import SimpleNamespace
        return SimpleNamespace(text="ok", attachments=[])

    gf = MagicMock(); gf.status_code = 200
    gf.json = MagicMock(return_value={"ok": True, "result": {"file_path": "docs/y.pdf"}})
    gf.raise_for_status = MagicMock()
    sm = MagicMock(); sm.status_code = 200; sm.text = "{}"
    dl = MagicMock(); dl.status_code = 200; dl.content = b"%PDF-fake"
    dl.raise_for_status = MagicMock()

    async def fake_post(self_client, url, *a, **kw):
        return gf if "/getFile" in url else sm
    async def fake_get(self_client, url, *a, **kw):
        return dl

    with patch.object(self.tg, "invoke_agent", side_effect=fake_invoke), \
         patch("httpx.AsyncClient.post", new=fake_post), \
         patch("httpx.AsyncClient.get", new=fake_get):
        for client in self._client(runner):
            r = client.post(
                "/gateways/telegram",
                json={
                    "update_id": 2,
                    "message": {
                        "message_id": 1,
                        "chat": {"id": 999, "type": "private"},
                        "from": {"id": 555},
                        "caption": "read this",
                        "document": {"file_id": "FILE", "file_name": "y.pdf",
                                     "mime_type": "application/pdf", "file_size": 8},
                    },
                },
                headers={"X-Telegram-Bot-Api-Secret-Token": "testsecret"},
            )
            self.assertEqual(r.status_code, 200)

    import time
    for _ in range(50):
        if "attachments" in captured:
            break
        time.sleep(0.02)
    self.assertEqual(captured["attachments"][0].mime_type, "application/pdf")
    self.assertEqual(captured["attachments"][0].display_name, "y.pdf")
```

- [ ] **Step 5.2: Run; confirm fail**

Run:
```
source .venv/bin/activate && pytest -q tests/test_gateway_telegram.py -v
```
Expected: the two new tests fail because the existing `_is_text_message` gate returns 200 noop for non-text updates, and there is no file-fetching code.

### Step 5.3 — Implement Telegram inbound files

- [ ] **Step 5.3: Modify `telegram.py`**

In `nuvel/backends/adk/templates_overlays/gateway-telegram/{{agent_package}}/gateways/telegram.py`:

Update the `_common` import:
```python
from {{agent_package}}.gateways._common import (
    InboundAttachment,
    enforce_attachment_limits,
    ensure_session,
    invoke_agent,
    session_key,
)
```

Replace `_is_text_message` and add file-fetching helpers. Insert after `_send_chat_action`:

```python
def _is_invokable_message(update: dict) -> bool:
    """Return True if the message has either text/caption or a supported file part."""
    msg = update.get("message")
    if not isinstance(msg, dict):
        return False
    if isinstance(msg.get("text"), str) and msg["text"]:
        return True
    if isinstance(msg.get("caption"), str) and msg["caption"]:
        return True
    return any(k in msg for k in ("photo", "document", "voice", "audio", "video", "video_note"))


_TELEGRAM_FILE_KINDS: tuple[tuple[str, str, str], ...] = (
    # (msg key, default mime, fallback display name template)
    ("document", "", "{kind}"),
    ("photo", "image/jpeg", "photo.jpg"),
    ("voice", "audio/ogg", "voice.ogg"),
    ("audio", "", "audio"),
    ("video", "video/mp4", "video.mp4"),
    ("video_note", "video/mp4", "video_note.mp4"),
)


def _select_file_descriptor(msg: dict) -> tuple[str, str, str] | None:
    """Pick (file_id, mime_type, display_name) for the first supported file part.

    For `photo`, picks the largest size.
    """
    for key, default_mime, default_name in _TELEGRAM_FILE_KINDS:
        item = msg.get(key)
        if not item:
            continue
        if key == "photo" and isinstance(item, list):
            largest = max(item, key=lambda p: p.get("file_size") or 0)
            return largest["file_id"], default_mime, default_name
        if isinstance(item, dict):
            file_id = item.get("file_id")
            if not file_id:
                continue
            mime = str(item.get("mime_type") or default_mime or "application/octet-stream")
            name = str(item.get("file_name") or default_name.format(kind=key))
            return file_id, mime, name
    return None


async def _fetch_telegram_file(file_id: str) -> tuple[bytes | None, str | None]:
    """Resolve file_id via getFile and download the bytes.

    Returns (bytes, file_path) or (None, None) on failure.
    """
    token = _bot_token()
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(
                f"{TELEGRAM_API_BASE}/bot{token}/getFile",
                json={"file_id": file_id},
            )
            r.raise_for_status()
            data = r.json()
            file_path = (data.get("result") or {}).get("file_path")
            if not file_path:
                return None, None
            url = f"{TELEGRAM_API_BASE}/file/bot{token}/{file_path}"
            dl = await client.get(url)
            dl.raise_for_status()
            return dl.content, file_path
    except Exception:
        logger.exception("Telegram: failed to fetch file_id=%s", file_id)
        return None, None


async def _collect_inbound_files(msg: dict) -> tuple[list[InboundAttachment], list[str]]:
    desc = _select_file_descriptor(msg)
    if desc is None:
        return [], []
    file_id, mime, name = desc
    data, _path = await _fetch_telegram_file(file_id)
    item = InboundAttachment(mime_type=mime, display_name=name, data=data)
    max_count = int(os.environ.get("GATEWAY_MAX_ATTACHMENT_COUNT", "5"))
    max_bytes = int(os.environ.get("GATEWAY_MAX_ATTACHMENT_BYTES", str(10 * 1024 * 1024)))
    return enforce_attachment_limits([item], max_count=max_count, max_bytes=max_bytes)
```

Update the webhook handler. Replace the `_is_text_message` block with `_is_invokable_message`:

```python
@router.post("/telegram")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
):
    _verify_secret(x_telegram_bot_api_secret_token)
    update = await request.json()

    if not _is_invokable_message(update):
        return JSONResponse(content={"ok": True, "skipped": "no text or supported file"})

    msg = update["message"]
    bot_username = os.environ.get("TELEGRAM_BOT_USERNAME") or None
    if not _should_invoke_in_group(msg, bot_username):
        return JSONResponse(content={"ok": True, "skipped": "group: no mention/command/reply"})

    asyncio.create_task(_process_message(request, msg))
    return JSONResponse(content={"ok": True})
```

`_should_invoke_in_group` currently does `msg.get("text", "")` — extend it to also use caption:

Replace:
```python
text = msg.get("text", "")
```
with:
```python
text = msg.get("text") or msg.get("caption") or ""
```

Update `_process_message` to derive prompt text and fetch attachments:

```python
async def _process_message(request: Request, msg: dict) -> None:
    runner = request.app.state.runner
    app_name = request.app.state.app_name
    user_id, session_id = session_key("telegram", msg)
    await ensure_session(runner.session_service, app_name, user_id, session_id)

    chat_id = (msg.get("chat") or {}).get("id")
    thread_id = msg.get("message_thread_id")
    reply_to = msg.get("message_id") if (msg.get("chat") or {}).get("type") != "private" else None

    text = (msg.get("text") or msg.get("caption") or "").strip()
    attachments, skip_notes = await _collect_inbound_files(msg)
    if skip_notes:
        text = (text + ("\n" if text else "") + "\n".join(skip_notes)).strip()
    if not text and not attachments:
        # Nothing to do.
        return

    inline_max_bytes = int(os.environ.get("GATEWAY_INLINE_DATA_MAX_BYTES", str(4 * 1024 * 1024)))

    keepalive = asyncio.create_task(_typing_keepalive(chat_id))
    try:
        reply = await invoke_agent(
            runner, user_id, session_id, text or "(file attached)",
            attachments=attachments, inline_max_bytes=inline_max_bytes,
        )
        reply_text = reply.text
        outbound = reply.attachments
    except Exception:
        logger.exception("Telegram: agent invocation failed")
        reply_text = "Sorry, something went wrong."
        outbound = []
    finally:
        keepalive.cancel()
        try:
            await keepalive
        except asyncio.CancelledError:
            pass

    # Outbound upload comes in Task 6; pass URI-only as link lines for now.
    if outbound:
        link_lines = [f"\n• {a.display_name}: {a.file_uri}" for a in outbound if a.file_uri]
        if link_lines:
            reply_text = f"{reply_text}\n\nAttached:" + "".join(link_lines)
        for a in outbound:
            if a.data and not a.file_uri:
                logger.info("Telegram: outbound attachment with bytes (%s, %d bytes) — upload deferred", a.display_name, len(a.data))

    await _send_message(chat_id, reply_text, reply_to=reply_to, message_thread_id=thread_id)
```

- [ ] **Step 5.4: Run; confirm pass**

Run:
```
source .venv/bin/activate && pytest -q tests/test_gateway_telegram.py -v
```
Expected: all tests pass, including the two new ones.

- [ ] **Step 5.5: Run full suite**

Run:
```
source .venv/bin/activate && pytest -q
```
Expected: green.

- [ ] **Step 5.6: Commit**

```
git add nuvel/backends/adk/templates_overlays/gateway-telegram/{{agent_package}}/gateways/telegram.py tests/test_gateway_telegram.py
git -c commit.gpgsign=false commit -m "feat(gateway-telegram): inbound photo/document/voice/audio/video

The webhook gate now accepts messages with supported file parts in
addition to text-only updates. Files are resolved via Bot API getFile
and downloaded over the file CDN with the bot token, then forwarded
to the agent as inline_data (within size caps) or skipped with a note.
Caption is treated as the prompt text when the message has no text."
```

---

## Task 6: Telegram outbound — `sendPhoto` / `sendDocument` for agent artifacts

**Goal:** When the reply has byte-bearing attachments, send them via multipart `sendPhoto` (image mimes) or `sendDocument` (everything else). The first file carries the reply text as `caption`; subsequent files have no caption. URI-only attachments use the URL form-field path.

**Files:**
- Modify: `nuvel/backends/adk/templates_overlays/gateway-telegram/{{agent_package}}/gateways/telegram.py`
- Test: `tests/test_gateway_telegram.py`

### Step 6.1 — Failing test

- [ ] **Step 6.1: Append outbound test**

```python
def test_outbound_inline_image_calls_send_photo(self):
    runner = AsyncMock()
    runner.session_service = AsyncMock()
    runner.session_service.get_session = AsyncMock(return_value=None)
    runner.session_service.create_session = AsyncMock()

    from tg_test.gateways._common import AgentReply, OutboundAttachment

    async def fake_invoke(*_a, **_kw):
        return AgentReply(
            text="here you go",
            attachments=[OutboundAttachment(
                mime_type="image/png", display_name="chart.png", data=b"\x89PNGdata",
            )],
        )

    posted = []
    async def capture_post(self_client, url, *args, **kwargs):
        posted.append({"url": url, "kwargs": kwargs})
        m = MagicMock()
        m.status_code = 200
        m.text = "{}"
        return m

    with patch.object(self.tg, "invoke_agent", side_effect=fake_invoke), \
         patch("httpx.AsyncClient.post", new=capture_post):
        for client in self._client(runner):
            r = client.post(
                "/gateways/telegram",
                json={
                    "update_id": 9,
                    "message": {
                        "message_id": 1,
                        "chat": {"id": 999, "type": "private"},
                        "from": {"id": 555},
                        "text": "draw it",
                    },
                },
                headers={"X-Telegram-Bot-Api-Secret-Token": "testsecret"},
            )
            self.assertEqual(r.status_code, 200)

    import time
    for _ in range(50):
        if any("/sendPhoto" in p["url"] for p in posted):
            break
        time.sleep(0.02)
    photo_calls = [p for p in posted if "/sendPhoto" in p["url"]]
    self.assertEqual(len(photo_calls), 1)
    files = photo_calls[0]["kwargs"].get("files") or {}
    data = photo_calls[0]["kwargs"].get("data") or {}
    self.assertIn("photo", files)
    self.assertEqual(data.get("caption"), "here you go")

def test_outbound_uri_only_calls_send_document_with_url(self):
    runner = AsyncMock()
    runner.session_service = AsyncMock()
    runner.session_service.get_session = AsyncMock(return_value=None)
    runner.session_service.create_session = AsyncMock()

    from tg_test.gateways._common import AgentReply, OutboundAttachment

    async def fake_invoke(*_a, **_kw):
        return AgentReply(
            text="here is the file",
            attachments=[OutboundAttachment(
                mime_type="application/pdf", display_name="report.pdf",
                file_uri="https://example.com/report.pdf",
            )],
        )

    posted = []
    async def capture_post(self_client, url, *args, **kwargs):
        posted.append({"url": url, "kwargs": kwargs})
        m = MagicMock(); m.status_code = 200; m.text = "{}"
        return m

    with patch.object(self.tg, "invoke_agent", side_effect=fake_invoke), \
         patch("httpx.AsyncClient.post", new=capture_post):
        for client in self._client(runner):
            r = client.post(
                "/gateways/telegram",
                json={
                    "update_id": 10,
                    "message": {
                        "message_id": 1,
                        "chat": {"id": 999, "type": "private"},
                        "from": {"id": 555},
                        "text": "send it",
                    },
                },
                headers={"X-Telegram-Bot-Api-Secret-Token": "testsecret"},
            )
            self.assertEqual(r.status_code, 200)

    import time
    for _ in range(50):
        if any("/sendDocument" in p["url"] for p in posted):
            break
        time.sleep(0.02)
    doc_calls = [p for p in posted if "/sendDocument" in p["url"]]
    self.assertEqual(len(doc_calls), 1)
    body = doc_calls[0]["kwargs"].get("json") or {}
    self.assertEqual(body.get("document"), "https://example.com/report.pdf")
    self.assertEqual(body.get("caption"), "here is the file")
```

- [ ] **Step 6.2: Run; confirm fail**

Run:
```
source .venv/bin/activate && pytest -q tests/test_gateway_telegram.py -v
```
Expected: the two new tests fail.

### Step 6.3 — Implement outbound sends

- [ ] **Step 6.3: Add outbound senders to `telegram.py`**

Insert below `_send_chat_action`:

```python
async def _send_photo(chat_id: int | str, *, data: bytes | None, file_uri: str | None,
                     caption: str | None, reply_to: int | None,
                     message_thread_id: int | None, filename: str) -> None:
    url = f"{TELEGRAM_API_BASE}/bot{_bot_token()}/sendPhoto"
    fields: dict = {"chat_id": str(chat_id)}
    if caption:
        fields["caption"] = caption
    if reply_to is not None:
        fields["reply_to_message_id"] = str(reply_to)
    if message_thread_id is not None:
        fields["message_thread_id"] = str(message_thread_id)

    async with httpx.AsyncClient(timeout=60) as client:
        if data is not None:
            files = {"photo": (filename, data, "application/octet-stream")}
            r = await client.post(url, data=fields, files=files)
        else:
            body = dict(fields)
            body["photo"] = file_uri
            r = await client.post(url, json=body)
        if r.status_code != 200:
            logger.warning("Telegram sendPhoto failed: %s %s", r.status_code, r.text[:200])


async def _send_document(chat_id: int | str, *, data: bytes | None, file_uri: str | None,
                        mime_type: str, caption: str | None, reply_to: int | None,
                        message_thread_id: int | None, filename: str) -> None:
    url = f"{TELEGRAM_API_BASE}/bot{_bot_token()}/sendDocument"
    fields: dict = {"chat_id": str(chat_id)}
    if caption:
        fields["caption"] = caption
    if reply_to is not None:
        fields["reply_to_message_id"] = str(reply_to)
    if message_thread_id is not None:
        fields["message_thread_id"] = str(message_thread_id)

    async with httpx.AsyncClient(timeout=60) as client:
        if data is not None:
            files = {"document": (filename, data, mime_type or "application/octet-stream")}
            r = await client.post(url, data=fields, files=files)
        else:
            body = dict(fields)
            body["document"] = file_uri
            r = await client.post(url, json=body)
        if r.status_code != 200:
            logger.warning("Telegram sendDocument failed: %s %s", r.status_code, r.text[:200])


def _is_image(mime: str) -> bool:
    return mime.lower().startswith("image/")
```

Update `_process_message` to use the senders. Replace the URI-only / log-only stub block at the bottom with:

```python
    if outbound:
        first_caption = reply_text or None
        for i, a in enumerate(outbound):
            cap = first_caption if i == 0 else None
            if i == 0:
                first_caption = None  # only first carries it
            try:
                if _is_image(a.mime_type):
                    await _send_photo(
                        chat_id, data=a.data, file_uri=a.file_uri,
                        caption=cap, reply_to=reply_to,
                        message_thread_id=thread_id, filename=a.display_name,
                    )
                else:
                    await _send_document(
                        chat_id, data=a.data, file_uri=a.file_uri,
                        mime_type=a.mime_type, caption=cap,
                        reply_to=reply_to, message_thread_id=thread_id,
                        filename=a.display_name,
                    )
            except Exception:
                logger.exception("Telegram: outbound send failed for %s", a.display_name)
        return  # text already delivered as caption on the first attachment

    await _send_message(chat_id, reply_text, reply_to=reply_to, message_thread_id=thread_id)
```

If `reply_text` is empty and there are no attachments, return early. Add at the start of the outbound block:

```python
    if not reply_text and not outbound:
        return
```

- [ ] **Step 6.4: Run new tests; confirm pass**

Run:
```
source .venv/bin/activate && pytest -q tests/test_gateway_telegram.py -v
```
Expected: all pass.

- [ ] **Step 6.5: Run full suite**

Run:
```
source .venv/bin/activate && pytest -q
```
Expected: green.

- [ ] **Step 6.6: Commit**

```
git add nuvel/backends/adk/templates_overlays/gateway-telegram/{{agent_package}}/gateways/telegram.py tests/test_gateway_telegram.py
git -c commit.gpgsign=false commit -m "feat(gateway-telegram): outbound sendPhoto/sendDocument

Inline-byte attachments are uploaded via multipart sendPhoto for image
mimes and sendDocument otherwise. URI-only attachments use Telegram's
URL-form-field path (HTTPS only). The first attachment carries the
reply text as caption; subsequent files have no caption. Failures log
and continue."
```

---

## Task 7: Teams sidecar — env-var aliases for `GATEWAY_*`

**Goal:** Honor `GATEWAY_MAX_ATTACHMENT_COUNT` and `GATEWAY_MAX_ATTACHMENT_BYTES` in the Teams sidecar as fallbacks for the existing `TEAMS_*` envs, so a single env-var set can configure all three gateways uniformly. No behavior change when only the new envs are set; back-compat preserved.

**Files:**
- Modify: `nuvel/backends/adk/templates_overlays/gateway-teams/{{agent_package}}/gateways/teams_bridge.py`
- Test: `tests/test_gateway_teams_bridge.py`

### Step 7.1 — Find the Teams test file

- [ ] **Step 7.1: Read the existing Teams tests**

Open `tests/test_gateway_teams_bridge.py` to find a suitable test class to extend. We just need a minimal env-alias check: when `GATEWAY_MAX_ATTACHMENT_COUNT=3` is set and `TEAMS_MAX_ATTACHMENT_COUNT` is unset, `AgentClient().max_attachment_count == 3`.

### Step 7.2 — Failing test

- [ ] **Step 7.2: Add env-alias test**

Append to whichever test class instantiates `AgentClient` (or create a new one). Add an import for the dynamically scaffolded `AgentClient` if missing, then:

```python
def test_agent_client_honors_gateway_max_count_env_alias(self):
    with patch.dict("os.environ", {
        "GATEWAY_MAX_ATTACHMENT_COUNT": "3",
    }, clear=False):
        # Make sure TEAMS_MAX_ATTACHMENT_COUNT is unset for this case:
        os.environ.pop("TEAMS_MAX_ATTACHMENT_COUNT", None)
        client = self.bridge.AgentClient()
        self.assertEqual(client.max_attachment_count, 3)

def test_teams_specific_env_takes_precedence_over_gateway_alias(self):
    with patch.dict("os.environ", {
        "GATEWAY_MAX_ATTACHMENT_COUNT": "3",
        "TEAMS_MAX_ATTACHMENT_COUNT": "7",
    }, clear=False):
        client = self.bridge.AgentClient()
        self.assertEqual(client.max_attachment_count, 7)
```

If the existing test file doesn't use `self.bridge`, follow whatever import pattern it uses to load `teams_bridge.py`. The point is: instantiate `AgentClient()` and assert on `max_attachment_count`.

- [ ] **Step 7.3: Run; confirm fail**

Run:
```
source .venv/bin/activate && pytest -q tests/test_gateway_teams_bridge.py -v
```
Expected: the new tests fail because `AgentClient.__init__` only reads `TEAMS_MAX_ATTACHMENT_COUNT`.

### Step 7.4 — Implement env aliases

- [ ] **Step 7.4: Add `_first_env` helper + use it in `AgentClient.__init__`**

Insert near the top of `teams_bridge.py` (after the imports):

```python
def _first_env(*keys: str, default: str = "") -> str:
    for k in keys:
        v = os.getenv(k)
        if v is not None and v != "":
            return v
    return default
```

In `AgentClient.__init__`, replace:
```python
self.max_attachment_count = int(os.getenv("TEAMS_MAX_ATTACHMENT_COUNT", "5"))
```
with:
```python
self.max_attachment_count = int(_first_env("TEAMS_MAX_ATTACHMENT_COUNT", "GATEWAY_MAX_ATTACHMENT_COUNT", default="5"))
```

And replace:
```python
self.max_attachment_bytes = int(os.getenv("TEAMS_MAX_ATTACHMENT_BYTES", "500000"))
```
with:
```python
self.max_attachment_bytes = int(_first_env("TEAMS_MAX_ATTACHMENT_BYTES", "GATEWAY_MAX_ATTACHMENT_BYTES", default="500000"))
```

- [ ] **Step 7.5: Run; confirm pass**

Run:
```
source .venv/bin/activate && pytest -q tests/test_gateway_teams_bridge.py -v
```
Expected: green.

- [ ] **Step 7.6: Run full suite**

Run:
```
source .venv/bin/activate && pytest -q
```
Expected: green.

- [ ] **Step 7.7: Commit**

```
git add nuvel/backends/adk/templates_overlays/gateway-teams/{{agent_package}}/gateways/teams_bridge.py tests/test_gateway_teams_bridge.py
git -c commit.gpgsign=false commit -m "feat(gateway-teams): honor GATEWAY_MAX_ATTACHMENT_* env aliases

Fall back to GATEWAY_MAX_ATTACHMENT_{COUNT,BYTES} when the Teams-specific
TEAMS_MAX_ATTACHMENT_{COUNT,BYTES} envs are unset, so the same env-var
set configures Slack, Telegram, and the Teams sidecar uniformly. The
TEAMS_* names continue to take precedence when both are set."
```

---

## Task 8: Documentation

**Goal:** Document the new envs, supported file types, and known limits in (a) each gateway overlay's README and (b) the repo-root `README.md`. No tests.

**Files:**
- Modify: `nuvel/backends/adk/templates_overlays/gateway-slack/{{agent_package}}/README*` (find the file in that subtree)
- Modify: `nuvel/backends/adk/templates_overlays/gateway-telegram/{{agent_package}}/README*`
- Modify: `nuvel/backends/adk/templates_overlays/gateway-teams/{{agent_package}}/README*`
- Modify: `README.md` (repo root)

### Step 8.1 — Locate per-overlay README files

- [ ] **Step 8.1: List the README locations**

Run:
```
find nuvel/backends/adk/templates_overlays/gateway-* -type f \( -iname 'README*' -o -iname '*.md' \)
```

For each gateway overlay, identify the README that has a "Channel" or "Setup" section. If an overlay has no README, look for whichever doc snippet is concatenated into the agent's root README during scaffolding (often a `_readme.md` partial). Use that.

### Step 8.2 — Add Multimodal sections

- [ ] **Step 8.2: Append a "Multimodal" section to each overlay's docs**

The block to add, with platform-specific tweaks where noted:

````markdown
### Multimodal (images and files)

The gateway forwards user-uploaded images and files to the agent and surfaces agent-emitted artifacts back to the chat.

**Supported envs:**

| Env | Default | Purpose |
|---|---|---|
| `GATEWAY_MAX_ATTACHMENT_COUNT` | `5` | per inbound message |
| `GATEWAY_MAX_ATTACHMENT_BYTES` | `10485760` (10 MiB) | per inbound file |
| `GATEWAY_INLINE_DATA_MAX_BYTES` | `4194304` (4 MiB) | bytes ≤ this go inline; else the URI is forwarded |
| `SLACK_BOT_TOKEN` | unset | **Slack only** — required to download user-uploaded files; without it, only the URL is forwarded |

**Outbound:** the agent can send images and files back via either of:
- emitting a `Part(inline_data=…)` or `Part(file_data=…)` in its event content;
- saving an artifact via `tool_context.save_artifact(...)` (read from `actions.artifact_delta`).

**Limitations (this release):**
- Slack: requires `SLACK_BOT_TOKEN` for inbound bytes.
- Telegram: animated stickers (TGS) are not parsed.
- Teams sidecar: outbound `actions.artifact_delta` is not read (only inline parts).
````

For Slack, keep the `SLACK_BOT_TOKEN` line. For Telegram, drop that row and add: `Telegram has its own 50 MB Bot API limit on inbound files; the gateway honors the smaller of that and `GATEWAY_MAX_ATTACHMENT_BYTES`.` For Teams, drop the `SLACK_BOT_TOKEN` line and keep the cap rows; mention the artifact-delta limitation prominently.

- [ ] **Step 8.3: Update repo-root `README.md`**

In the existing "Channels" section of `README.md`, add a one-line bullet under each of Slack, Telegram, and Teams:
- "Multimodal: forwards user images/files (size and count caps via `GATEWAY_MAX_ATTACHMENT_*`) and uploads agent artifacts back to chat. See the per-channel README for details."

For Teams, qualify it: "Inline agent artifacts only; saved artifacts via `tool_context.save_artifact(...)` are surfaced on Slack and Telegram but not yet on the Teams sidecar."

### Step 8.4 — Verify scaffolding still works end-to-end

- [ ] **Step 8.4: Smoke-test scaffolding**

Run:
```
source .venv/bin/activate && pytest -q tests/test_scaffold_gateways.py
```
Expected: green. (This catches templating-syntax errors that pure unit tests on the generated modules would miss.)

- [ ] **Step 8.5: Commit**

```
git add nuvel/backends/adk/templates_overlays/gateway-* README.md
git -c commit.gpgsign=false commit -m "docs(gateways): document multimodal support per channel and root

Adds a Multimodal section to each gateway overlay's README explaining
inbound/outbound flow, env-var caps, and per-channel limitations
(Slack needs SLACK_BOT_TOKEN, Teams sidecar reads inline parts only,
Telegram skips animated stickers). Updates the repo-root channels
section to mention image/file support."
```

---

## Final verification

- [ ] **Final: Full test suite**

Run:
```
source .venv/bin/activate && pytest -q
```
Expected: 213 pre-existing + new tests, all green.

- [ ] **Final: Manual sanity for templating**

Run:
```
source .venv/bin/activate && python -c "from nuvel.backends.adk.scaffold import scaffold_agent; \
import tempfile; out = tempfile.mkdtemp(); \
print(scaffold_agent('demo', output_dir=out, with_slack=True, with_telegram=True, with_teams=True))"
```
Expected: `status: ok`. The scaffolded agent should have the new helpers in `gateways/_common.py`.

- [ ] **Final: Compare against spec**

Open `docs/superpowers/specs/2026-05-09-gateway-artifacts-design.md` and skim each section:
- §2 Shared core: types, helpers, `invoke_agent` updated → Tasks 1, 2 ✓
- §3 Slack: inbound + outbound → Tasks 3, 4 ✓
- §4 Telegram: inbound + outbound → Tasks 5, 6 ✓
- §5 Teams: env aliases (artifact_delta out of scope) → Task 7 ✓
- §6 Limits/policy envs → Tasks 1–6 (read in inbound paths) ✓
- §7 Failure handling → covered as `try/except + logger.exception` in each task
- §8 Tests → Tasks 1–7 ✓
- §9 Docs → Task 8 ✓

If anything is missing, add a follow-up task before declaring done.
