# Messaging Gateways Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add three CLI flags (`--with-slack`, `--with-telegram`, `--with-teams`) to `nuvel new --framework adk` so a scaffolded ADK agent ships with the inbound webhook handlers, signature verification, session mapping, and outbound API plumbing for those platforms.

**Architecture:** Three overlays under `nuvel/backends/adk/templates_overlays/` (`gateway-base/`, `gateway-slack/`, `gateway-telegram/`, `gateway-teams/`) that drop a `gateways/` package into the scaffolded agent. Slack and Telegram are FastAPI sub-routers mounted on `run_adk.py`; Teams is a standalone aiohttp sidecar (port of an existing v1). Configuration is `.env`-only.

**Tech Stack:** Python 3.11+, FastAPI, `httpx`, Composio Python SDK (Slack), Telegram Bot API (Telegram), Microsoft 365 Agents SDK (`microsoft-agents-*`, aiohttp-based) for Teams.

**Spec:** `docs/superpowers/specs/2026-05-09-messaging-gateways-design.md` — read it before starting. The spec is the source of truth for design decisions; this plan is the source of truth for execution order.

**Reference (v1 Teams bridge to port):** `reference/teams-v1/data-analysis-agent/run_m365_bridge.py`. After Task 5 completes, the `reference/` directory is deleted from the branch.

---

## File inventory

**New files (in nuvel CLI source — these are *templates*, not runtime code):**

```
nuvel/backends/adk/templates_overlays/gateway-base/
    {{agent_package}}/gateways/__init__.py
    {{agent_package}}/gateways/_common.py
nuvel/backends/adk/templates_overlays/gateway-slack/
    {{agent_package}}/gateways/slack.py
nuvel/backends/adk/templates_overlays/gateway-telegram/
    {{agent_package}}/gateways/telegram.py
nuvel/backends/adk/templates_overlays/gateway-teams/
    {{agent_package}}/gateways/teams_bridge.py
tests/test_scaffold_gateways.py
tests/test_gateway_common.py
tests/test_gateway_slack.py
tests/test_gateway_telegram.py
tests/test_gateway_teams_bridge.py
```

**Files modified:**

```
nuvel/cli.py                                              # 3 new flags, threading
nuvel/backends/adk/scaffold.py                            # accept new flags, apply overlays, populate placeholders, append requirements/env/readme
nuvel/backends/adk/templates/run_adk.py                   # {{gateway_imports}}, {{gateway_mounts}}, /gateways added to PUBLIC_PREFIXES
nuvel/backends/adk/templates/requirements.txt             # {{gateway_requirements}} placeholder
nuvel/backends/adk/templates/.env.example                 # {{gateway_env_block}} placeholder
nuvel/backends/adk/templates/README.md.tmpl               # {{gateway_readme_section}} placeholder
nuvel/backends/claude_agent_sdk/scaffold.py               # accept channel flags, error if any are set
nuvel/backends/anthropic_managed_agents/scaffold.py       # accept channel flags, error if any are set
README.md                                                 # link to spec under feature list
CONTRIBUTING.md                                           # note overlay convention
```

---

## Task 1: CLI flags + signature plumbing (no behavior yet)

**Goal:** Add `--with-slack`, `--with-telegram`, `--with-teams` flags to the CLI and thread them through `scaffold_agent()` calls in all three backends. ADK accepts them (no overlay logic yet — flags are stored). Other backends reject any of them set.

**Why first:** Tightens the contract before we have something to do with it. Later tasks layer behavior onto flags that already exist and validate.

**Files:**
- Modify: `nuvel/cli.py`
- Modify: `nuvel/backends/adk/scaffold.py`
- Modify: `nuvel/backends/claude_agent_sdk/scaffold.py`
- Modify: `nuvel/backends/anthropic_managed_agents/scaffold.py`
- Create: `tests/test_scaffold_gateways.py` (start the file)

- [ ] **Step 1: Write the failing tests**

Append to a new file `tests/test_scaffold_gateways.py`:

```python
"""Tests for the messaging-gateway flags on `nuvel new`."""

import shutil
import tempfile
import unittest
from pathlib import Path

from nuvel.backends.adk.scaffold import scaffold_agent as adk_scaffold
from nuvel.backends.claude_agent_sdk.scaffold import scaffold_agent as cas_scaffold
from nuvel.backends.anthropic_managed_agents.scaffold import scaffold_agent as ama_scaffold


class TestADKAcceptsChannelFlags(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_no_flags_returns_ok_and_no_channels(self):
        result = adk_scaffold("agent-a", output_dir=self.tmpdir)
        self.assertEqual(result["status"], "ok")
        self.assertFalse(result.get("with_slack"))
        self.assertFalse(result.get("with_telegram"))
        self.assertFalse(result.get("with_teams"))

    def test_with_telegram_flag_accepted_and_echoed(self):
        result = adk_scaffold("agent-b", output_dir=self.tmpdir, with_telegram=True)
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["with_telegram"])

    def test_with_teams_flag_accepted_and_echoed(self):
        result = adk_scaffold("agent-c", output_dir=self.tmpdir, with_teams=True)
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["with_teams"])

    def test_with_slack_auto_enables_composio(self):
        result = adk_scaffold("agent-d", output_dir=self.tmpdir, with_slack=True)
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["with_slack"])
        self.assertTrue(result["with_composio"], "with_slack must auto-enable with_composio")


class TestNonAdkBackendsRejectChannelFlags(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_claude_agent_sdk_rejects_with_slack(self):
        result = cas_scaffold("agent-e", output_dir=self.tmpdir, with_slack=True)
        self.assertEqual(result["status"], "error")
        self.assertIn("with-slack", result["message"].lower())

    def test_claude_agent_sdk_rejects_with_telegram(self):
        result = cas_scaffold("agent-f", output_dir=self.tmpdir, with_telegram=True)
        self.assertEqual(result["status"], "error")

    def test_claude_agent_sdk_rejects_with_teams(self):
        result = cas_scaffold("agent-g", output_dir=self.tmpdir, with_teams=True)
        self.assertEqual(result["status"], "error")

    def test_anthropic_managed_rejects_all_channel_flags(self):
        for kw in ("with_slack", "with_telegram", "with_teams"):
            result = ama_scaffold("agent-x", output_dir=self.tmpdir, **{kw: True})
            self.assertEqual(result["status"], "error", f"{kw} should be rejected")


class TestCLIParsing(unittest.TestCase):
    def test_parser_accepts_channel_flags(self):
        from nuvel.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(
            ["new", "agent-y", "--with-slack", "--with-telegram", "--with-teams"]
        )
        self.assertTrue(args.with_slack)
        self.assertTrue(args.with_telegram)
        self.assertTrue(args.with_teams)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_scaffold_gateways.py -v`
Expected: tests fail with `TypeError: scaffold_agent() got an unexpected keyword argument 'with_slack'` or similar.

- [ ] **Step 3: Add the three flags to ADK `scaffold_agent()`**

In `nuvel/backends/adk/scaffold.py`, change the `scaffold_agent` signature and the result dict:

```python
def scaffold_agent(
    name: str,
    output_dir: str | None = None,
    description: str = "",
    system_prompt: str = "",
    persona: bool = False,
    with_composio: bool = False,
    with_slack: bool = False,
    with_telegram: bool = False,
    with_teams: bool = False,
) -> dict:
    """... (extend docstring) ...

    Args:
        ...
        with_slack: Activate the Slack messaging-gateway overlay.
                    Implies with_composio (Slack uses Composio Slackbot).
        with_telegram: Activate the Telegram messaging-gateway overlay.
        with_teams: Activate the MS Teams messaging-gateway overlay
                    (sidecar process; runs separately from the agent server).
    """
    # --with-slack uses Composio Slackbot end-to-end; auto-enable composio.
    if with_slack and not with_composio:
        print("[nuvel] --with-slack uses Composio Slackbot — enabling --with-composio.")
        with_composio = True

    # ... existing validation, replacements, stamping ...

    return {
        "status": "ok",
        "path": str(target),
        "agent_name": name,
        "package_name": package,
        "files_created": len(files_created),
        "files": files_created,
        "persona": persona,
        "with_composio": with_composio,
        "with_slack": with_slack,
        "with_telegram": with_telegram,
        "with_teams": with_teams,
    }
```

Don't apply any new overlays yet — that's Task 2-onward. The flags are just *accepted* and *echoed* for now.

- [ ] **Step 4: Reject channel flags in the other two backends**

In `nuvel/backends/claude_agent_sdk/scaffold.py`, replace the existing `scaffold_agent` rejection block with one that also handles the three channel flags. Add to the signature: `with_slack: bool = False, with_telegram: bool = False, with_teams: bool = False`. Then:

```python
if persona:
    return {"status": "error", "message": "--persona is an ADK-only bundle; use --framework adk if you want it."}
if with_composio:
    return {"status": "error", "message": "--with-composio is an ADK-only bundle; use --framework adk if you want it."}
for flag_name, flag_set in (("with-slack", with_slack), ("with-telegram", with_telegram), ("with-teams", with_teams)):
    if flag_set:
        return {
            "status": "error",
            "message": f"--{flag_name} is not yet supported for the claude-agent-sdk backend. Use --framework adk.",
        }
```

Apply the **same** pattern to `nuvel/backends/anthropic_managed_agents/scaffold.py` (with the framework name `anthropic-managed-agents` in the error message).

- [ ] **Step 5: Add the three flags to the CLI parser**

In `nuvel/cli.py`, in `build_parser()` where `--with-composio` is defined, add:

```python
p_new.add_argument(
    "--with-slack", action="store_true",
    help="(adk only) Add a Slack gateway via Composio Slackbot. Implies --with-composio.",
)
p_new.add_argument(
    "--with-telegram", action="store_true",
    help="(adk only) Add a Telegram gateway (webhook + Bot API outbound).",
)
p_new.add_argument(
    "--with-teams", action="store_true",
    help="(adk only) Add an MS Teams gateway (aiohttp sidecar via Microsoft 365 Agents SDK).",
)
```

In `_cmd_new()`, thread the flags into the `scaffold_agent(...)` call:

```python
result = scaffold_agent(
    name=args.name,
    output_dir=args.output_dir,
    description=args.description,
    system_prompt=args.system_prompt,
    persona=args.persona,
    with_composio=args.with_composio,
    with_slack=args.with_slack,
    with_telegram=args.with_telegram,
    with_teams=args.with_teams,
)
```

And extend the success-summary block to also report channels:

```python
if result["status"] == "ok":
    print(f"Agent scaffolded at: {result['path']}")
    print(f"Files created: {result['files_created']}")
    print(f"Framework: {args.framework}")
    flags = []
    if result.get("persona"):
        flags.append("persona")
    if result.get("with_composio"):
        flags.append("composio")
    if flags:
        print(f"Bundles: {', '.join(flags)}")
    channels = [
        ch for ch, key in (("slack", "with_slack"), ("telegram", "with_telegram"), ("teams", "with_teams"))
        if result.get(key)
    ]
    if channels:
        print(f"Channels: {', '.join(channels)}")
    return 0
```

- [ ] **Step 6: Run all tests to verify**

Run: `python -m pytest tests/ -q`
Expected: all 163 + new tests pass (~170+ total). The new gateway-flag tests should pass; the existing 163 should be unchanged.

- [ ] **Step 7: Commit**

```bash
git add nuvel/cli.py nuvel/backends/adk/scaffold.py \
        nuvel/backends/claude_agent_sdk/scaffold.py \
        nuvel/backends/anthropic_managed_agents/scaffold.py \
        tests/test_scaffold_gateways.py
git commit -m "feat(cli): accept --with-slack/--with-telegram/--with-teams flags

ADK accepts and echoes; --with-slack auto-enables --with-composio.
Non-ADK backends reject any of the three with a clear error.
No overlay behavior yet — flags are plumbed only.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Gateway-base overlay + template placeholders

**Goal:** Introduce the `gateway-base/` overlay (shared `_common.py` with session-key composition, session ensure, and in-process agent invocation) and add the four placeholders to base templates so subsequent channel tasks just need to populate them. With no channel flags set, scaffold output is byte-identical to today.

**Files:**
- Create: `nuvel/backends/adk/templates_overlays/gateway-base/{{agent_package}}/gateways/__init__.py`
- Create: `nuvel/backends/adk/templates_overlays/gateway-base/{{agent_package}}/gateways/_common.py`
- Modify: `nuvel/backends/adk/templates/run_adk.py`
- Modify: `nuvel/backends/adk/templates/requirements.txt`
- Modify: `nuvel/backends/adk/templates/.env.example`
- Modify: `nuvel/backends/adk/templates/README.md.tmpl`
- Modify: `nuvel/backends/adk/scaffold.py` (apply overlay + populate placeholders)
- Create: `tests/test_gateway_common.py`
- Modify: `tests/test_scaffold_gateways.py` (extend)

- [ ] **Step 1: Write failing tests for `_common.session_key`**

Create `tests/test_gateway_common.py`:

```python
"""Tests for the shared gateway _common module.

The module under test lives inside a *generated* agent — so each test
scaffolds a tiny agent in a tmpdir, then imports its `_common` module.
"""

import importlib.util
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from nuvel.backends.adk.scaffold import scaffold_agent


def _scaffold_with(tmpdir, **flags):
    """Scaffold an agent with the given flags and return its package dir."""
    result = scaffold_agent("agent-test", output_dir=tmpdir, **flags)
    if result["status"] != "ok":
        raise AssertionError(result.get("message"))
    return Path(result["path"]) / "agent_test"


def _import_module(pkg_dir: Path, dotted: str):
    """Dynamically import `dotted` from a generated agent package."""
    file_path = pkg_dir / Path(*dotted.split(".")).with_suffix(".py")
    spec = importlib.util.spec_from_file_location(f"_gw_{dotted.replace('.', '_')}", file_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


class TestSessionKey(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp()
        # Scaffold once, reuse for all session_key tests.
        cls.pkg = _scaffold_with(cls.tmpdir, with_telegram=True)
        cls.common = _import_module(cls.pkg, "gateways._common")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def test_slack_dm(self):
        payload = {"team_id": "T01", "channel": "D456", "user": "U012", "channel_type": "im"}
        user_id, session_id = self.common.session_key("slack", payload)
        self.assertEqual(user_id, "slack:T01:U012")
        self.assertEqual(session_id, "slack:dm:T01:D456")

    def test_slack_channel_with_thread(self):
        payload = {"team_id": "T01", "channel": "C123", "user": "U012",
                   "ts": "1700000000.001", "thread_ts": "1699999999.500"}
        user_id, session_id = self.common.session_key("slack", payload)
        self.assertEqual(user_id, "slack:T01:U012")
        self.assertEqual(session_id, "slack:thread:T01:C123:1699999999.500")

    def test_slack_channel_without_thread_uses_ts(self):
        payload = {"team_id": "T01", "channel": "C123", "user": "U012", "ts": "1700000000.001"}
        _, session_id = self.common.session_key("slack", payload)
        self.assertEqual(session_id, "slack:thread:T01:C123:1700000000.001")

    def test_telegram_private_chat(self):
        payload = {"chat": {"id": 999, "type": "private"}, "from": {"id": 555}}
        user_id, session_id = self.common.session_key("telegram", payload)
        self.assertEqual(user_id, "telegram:555")
        self.assertEqual(session_id, "telegram:dm:555")

    def test_telegram_group(self):
        payload = {"chat": {"id": -1001, "type": "supergroup"}, "from": {"id": 555}}
        user_id, session_id = self.common.session_key("telegram", payload)
        self.assertEqual(user_id, "telegram:555")
        self.assertEqual(session_id, "telegram:group:-1001")

    def test_telegram_forum_topic(self):
        payload = {"chat": {"id": -1001, "type": "supergroup"}, "from": {"id": 555},
                   "message_thread_id": 42}
        _, session_id = self.common.session_key("telegram", payload)
        self.assertEqual(session_id, "telegram:group:-1001:42")

    def test_unknown_platform_raises(self):
        with self.assertRaises(ValueError):
            self.common.session_key("discord", {})


if __name__ == "__main__":
    unittest.main()
```

Also extend `tests/test_scaffold_gateways.py` with a test asserting that scaffolding **without** any flag is byte-identical to today (use a hash):

```python
class TestNoFlagsByteIdentical(unittest.TestCase):
    """Scaffolding with no channel flags must produce the same files as today."""

    def setUp(self):
        self.tmp_a = tempfile.mkdtemp()
        self.tmp_b = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp_a, ignore_errors=True)
        shutil.rmtree(self.tmp_b, ignore_errors=True)

    def test_no_flags_run_adk_has_no_gateway_imports(self):
        adk_scaffold("agent-base", output_dir=self.tmp_a)
        run_adk = (Path(self.tmp_a) / "agent-base" / "run_adk.py").read_text()
        self.assertNotIn("gateway", run_adk.lower(),
                         "run_adk.py must not mention 'gateway' when no channel flags are set")
        self.assertNotIn("{{gateway", run_adk,
                         "no gateway placeholder substrings should remain")

    def test_no_flags_env_example_has_no_gateway_block(self):
        adk_scaffold("agent-base2", output_dir=self.tmp_b)
        env = (Path(self.tmp_b) / "agent-base2" / ".env.example").read_text()
        self.assertNotIn("{{gateway", env)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_gateway_common.py tests/test_scaffold_gateways.py -v`
Expected: `tests/test_gateway_common.py` fails with `FileNotFoundError` (no `_common.py` module yet); the new `TestNoFlagsByteIdentical` cases pass already (because no placeholder has been added yet).

- [ ] **Step 3: Add placeholders to base templates**

In `nuvel/backends/adk/templates/run_adk.py`:
- Add `{{gateway_imports}}` on its own line right after the existing `from {{agent_package}}.config.logging import ...` line (around line 28).
- Add `{{gateway_mounts}}` inside `main()` immediately **after** the existing `add_endpoints(app)` call (around line 285), so routers are mounted before uvicorn starts.
- Update `APIKeyMiddleware.PUBLIC_PREFIXES`:

```python
PUBLIC_PREFIXES = ("/health", "/favicon.ico", "/gateways")
```

In `nuvel/backends/adk/templates/requirements.txt`, add a line:
```
{{gateway_requirements}}
```
(immediately after the existing `{{composio_requirement}}` line — placeholders that expand to empty leave a blank line, which is harmless in `requirements.txt`)

In `nuvel/backends/adk/templates/.env.example`, add a line right after the existing `{{composio_env_block}}` placeholder:
```
{{gateway_env_block}}
```

In `nuvel/backends/adk/templates/README.md.tmpl`, append:
```
{{gateway_readme_section}}
```
on its own line near the end of the file (after the existing main content).

- [ ] **Step 4: Create the gateway-base overlay**

Create `nuvel/backends/adk/templates_overlays/gateway-base/{{agent_package}}/gateways/__init__.py`:

```python
"""Messaging-app gateways for {{agent_name}}.

Each gateway exposes a small adapter between an external messaging platform
(Slack, Telegram, MS Teams) and the ADK agent that powers this service.

Slack and Telegram are FastAPI APIRouters mounted on the same server as the
agent (`run_adk.py`). MS Teams runs as a separate aiohttp sidecar
(`teams_bridge.py`) for compatibility with the Microsoft 365 Agents SDK.
"""
```

Create `nuvel/backends/adk/templates_overlays/gateway-base/{{agent_package}}/gateways/_common.py`:

```python
"""Shared helpers for in-process messaging gateways (Slack, Telegram).

Teams uses its own sidecar and does not import this module; its session-key
composition is duplicated inside `teams_bridge.py` to keep the sidecar
independently importable.
"""

from __future__ import annotations

import logging
from typing import Any

from google.adk.runners import Runner
from google.adk.sessions import BaseSessionService
from google.genai import types as genai_types

logger = logging.getLogger(__name__)


def session_key(platform: str, payload: dict[str, Any]) -> tuple[str, str]:
    """Compose (user_id, session_id) for an inbound platform event.

    See spec §6 for the policy table. Hybrid: thread-scoped in channels,
    user-scoped in DMs.

    Raises:
        ValueError: if `platform` is unknown.
    """
    if platform == "slack":
        team = payload.get("team_id") or payload.get("team", "unknown")
        user = payload.get("user", "anonymous")
        channel = payload.get("channel", "unknown")
        is_dm = payload.get("channel_type") == "im" or str(channel).startswith("D")
        if is_dm:
            return f"slack:{team}:{user}", f"slack:dm:{team}:{channel}"
        thread = payload.get("thread_ts") or payload.get("ts")
        return f"slack:{team}:{user}", f"slack:thread:{team}:{channel}:{thread}"

    if platform == "telegram":
        from_user = (payload.get("from") or {}).get("id", "anonymous")
        chat = payload.get("chat") or {}
        chat_type = chat.get("type", "private")
        chat_id = chat.get("id", "unknown")
        if chat_type == "private":
            return f"telegram:{from_user}", f"telegram:dm:{from_user}"
        thread = payload.get("message_thread_id")
        suffix = f":{thread}" if thread is not None else ""
        return f"telegram:{from_user}", f"telegram:group:{chat_id}{suffix}"

    raise ValueError(f"Unknown platform: {platform!r}")


async def ensure_session(
    session_service: BaseSessionService,
    app_name: str,
    user_id: str,
    session_id: str,
) -> None:
    """Create the session if it does not already exist. Idempotent."""
    existing = await session_service.get_session(
        app_name=app_name, user_id=user_id, session_id=session_id
    )
    if existing is None:
        await session_service.create_session(
            app_name=app_name, user_id=user_id, session_id=session_id, state={}
        )


async def invoke_agent(
    runner: Runner,
    user_id: str,
    session_id: str,
    text: str,
) -> str:
    """Run the agent in-process and return the final assistant text reply.

    Iterates `runner.run_async(...)` events, collects all text parts emitted
    by non-user events, and returns the **last non-empty** text — matching
    the v1 Teams bridge's extraction rule.
    """
    new_message = genai_types.Content(role="user", parts=[genai_types.Part(text=text)])
    texts: list[str] = []
    async for event in runner.run_async(
        user_id=user_id, session_id=session_id, new_message=new_message
    ):
        if getattr(event, "author", None) == "user":
            continue
        content = getattr(event, "content", None)
        if not content:
            continue
        for part in getattr(content, "parts", None) or []:
            piece = getattr(part, "text", None)
            if piece:
                texts.append(piece)
    return texts[-1] if texts else "Agent did not return text."
```

- [ ] **Step 5: Wire scaffold to apply gateway-base + populate empty placeholders**

In `nuvel/backends/adk/scaffold.py`:

(a) Extend `_build_replacements` parameters and dict to include the four new placeholders. With **no** channel selected, all four expand to empty strings:

```python
def _build_replacements(
    name: str,
    package: str,
    description: str,
    system_prompt: str,
    persona: bool,
    with_composio: bool,
    with_slack: bool,
    with_telegram: bool,
    with_teams: bool,
) -> dict[str, str]:
    # ... existing frame logic ...

    gateway_imports = ""
    gateway_mounts = ""
    gateway_requirements = ""
    gateway_env_block = ""
    gateway_readme_section = ""

    # Channel-specific contributions are stitched in the next tasks (3, 4, 5).
    # For now: any channel flag set means at least the base overlay applies,
    # which contributes nothing to imports/mounts directly — that's per-channel.

    return {
        # ... existing entries ...
        "{{gateway_imports}}": gateway_imports,
        "{{gateway_mounts}}": gateway_mounts,
        "{{gateway_requirements}}": gateway_requirements,
        "{{gateway_env_block}}": gateway_env_block,
        "{{gateway_readme_section}}": gateway_readme_section,
    }
```

(b) In `scaffold_agent`, after the existing overlays apply gateway-base when any channel flag is set:

```python
# 2. Overlays — order matters: later overlays override earlier ones
if persona:
    _stamp_tree(OVERLAYS_DIR / "persona", target, replacements, files_created)
if with_composio:
    _stamp_tree(OVERLAYS_DIR / "composio", target, replacements, files_created)
if with_slack or with_telegram or with_teams:
    _stamp_tree(OVERLAYS_DIR / "gateway-base", target, replacements, files_created)
# Per-channel overlays added in subsequent tasks.
```

(c) Pass the three new flags through from `scaffold_agent` to `_build_replacements`:

```python
replacements = _build_replacements(
    name, package, description, system_prompt, persona, with_composio,
    with_slack, with_telegram, with_teams,
)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/test_gateway_common.py tests/test_scaffold_gateways.py tests/ -q`
Expected: all tests pass. The session-key tests verify the dispatch table; the byte-identical test verifies that no flags = no `{{gateway_*}}` substrings remain (substituted away with empty strings).

- [ ] **Step 7: Commit**

```bash
git add nuvel/backends/adk/templates/run_adk.py \
        nuvel/backends/adk/templates/requirements.txt \
        nuvel/backends/adk/templates/.env.example \
        nuvel/backends/adk/templates/README.md.tmpl \
        nuvel/backends/adk/templates_overlays/gateway-base/ \
        nuvel/backends/adk/scaffold.py \
        tests/test_gateway_common.py tests/test_scaffold_gateways.py
git commit -m "feat(scaffold): add gateway-base overlay and template placeholders

Introduces the shared gateways/_common.py module (session_key,
ensure_session, invoke_agent) and the four placeholders that channel
overlays will populate: {{gateway_imports}}, {{gateway_mounts}},
{{gateway_requirements}}, {{gateway_env_block}}, {{gateway_readme_section}}.

With no channel flags set, all placeholders expand to empty and
scaffold output is byte-identical to before this change.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Telegram channel (end-to-end)

**Goal:** `nuvel new --with-telegram` produces an agent with a working Telegram webhook handler at `/gateways/telegram`, plus env-var docs, README section, and a verification test.

**Why Telegram first:** simplest of the three. Single `httpx` outbound, single secret-token verification, no Composio dependency. Validates the channel-overlay pattern end-to-end before tackling Slack and the Teams sidecar.

**Files:**
- Create: `nuvel/backends/adk/templates_overlays/gateway-telegram/{{agent_package}}/gateways/telegram.py`
- Modify: `nuvel/backends/adk/scaffold.py` (per-channel placeholder content)
- Create: `tests/test_gateway_telegram.py`
- Modify: `tests/test_scaffold_gateways.py` (add Telegram-flag scaffold tests)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_scaffold_gateways.py`:

```python
class TestTelegramOverlay(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        result = adk_scaffold("agent-tg", output_dir=self.tmpdir, with_telegram=True)
        self.assertEqual(result["status"], "ok")
        self.agent_dir = Path(result["path"])

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_telegram_module_exists(self):
        self.assertTrue((self.agent_dir / "agent_tg" / "gateways" / "telegram.py").is_file())
        self.assertTrue((self.agent_dir / "agent_tg" / "gateways" / "_common.py").is_file())

    def test_run_adk_imports_and_mounts_telegram(self):
        run_adk = (self.agent_dir / "run_adk.py").read_text()
        self.assertIn("from agent_tg.gateways import telegram as gw_telegram", run_adk)
        self.assertIn("app.include_router(gw_telegram.router)", run_adk)

    def test_env_example_has_telegram_block(self):
        env = (self.agent_dir / ".env.example").read_text()
        self.assertIn("TELEGRAM_BOT_TOKEN", env)
        self.assertIn("TELEGRAM_WEBHOOK_SECRET", env)

    def test_readme_has_telegram_section(self):
        readme = (self.agent_dir / "README.md").read_text()
        self.assertIn("Telegram", readme)
        self.assertIn("setWebhook", readme)
```

Create `tests/test_gateway_telegram.py`:

```python
"""Unit tests for the generated agent's Telegram gateway router.

Each test scaffolds a tiny agent with --with-telegram, then dynamically
imports its `gateways.telegram` module and exercises the router with
FastAPI's TestClient against a mocked agent runner and mocked httpx.
"""

import importlib.util
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from nuvel.backends.adk.scaffold import scaffold_agent


def _scaffold_telegram(tmpdir):
    result = scaffold_agent("tg-test", output_dir=tmpdir, with_telegram=True)
    if result["status"] != "ok":
        raise AssertionError(result.get("message"))
    return Path(result["path"]) / "tg_test"


def _import_telegram(pkg_dir: Path):
    init_path = pkg_dir / "gateways" / "__init__.py"
    spec = importlib.util.spec_from_file_location(
        "tg_test_gateways", init_path, submodule_search_locations=[str(pkg_dir / "gateways")]
    )
    pkg = importlib.util.module_from_spec(spec)
    sys.modules["tg_test_gateways"] = pkg
    spec.loader.exec_module(pkg)
    # Now the submodule:
    sub_path = pkg_dir / "gateways" / "telegram.py"
    sub_spec = importlib.util.spec_from_file_location("tg_test_gateways.telegram", sub_path)
    sub = importlib.util.module_from_spec(sub_spec)
    sys.modules["tg_test_gateways.telegram"] = sub
    sub_spec.loader.exec_module(sub)
    return sub


class TestTelegramRouter(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp()
        pkg = _scaffold_telegram(cls.tmpdir)
        cls.tg = _import_telegram(pkg)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def _client(self, runner_mock, secret="testsecret"):
        app = FastAPI()
        app.state.runner = runner_mock
        app.state.app_name = "tg-test"
        app.include_router(self.tg.router)
        with patch.dict("os.environ", {
            "TELEGRAM_WEBHOOK_SECRET": secret,
            "TELEGRAM_BOT_TOKEN": "TESTTOKEN",
        }, clear=False):
            yield TestClient(app)

    def test_missing_secret_returns_401(self):
        runner = AsyncMock()
        for client in self._client(runner):
            r = client.post("/gateways/telegram", json={"update_id": 1})
            self.assertEqual(r.status_code, 401)

    def test_wrong_secret_returns_401(self):
        runner = AsyncMock()
        for client in self._client(runner):
            r = client.post(
                "/gateways/telegram",
                json={"update_id": 1},
                headers={"X-Telegram-Bot-Api-Secret-Token": "WRONG"},
            )
            self.assertEqual(r.status_code, 401)

    def test_valid_text_message_returns_200_and_dispatches(self):
        runner = AsyncMock()
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value.status_code = 200
            for client in self._client(runner):
                r = client.post(
                    "/gateways/telegram",
                    json={
                        "update_id": 42,
                        "message": {
                            "message_id": 1,
                            "chat": {"id": 999, "type": "private"},
                            "from": {"id": 555},
                            "text": "hello",
                        },
                    },
                    headers={"X-Telegram-Bot-Api-Secret-Token": "testsecret"},
                )
                self.assertEqual(r.status_code, 200)

    def test_non_text_update_is_noop_200(self):
        runner = AsyncMock()
        for client in self._client(runner):
            r = client.post(
                "/gateways/telegram",
                json={"update_id": 1, "edited_message": {"text": "x"}},
                headers={"X-Telegram-Bot-Api-Secret-Token": "testsecret"},
            )
            self.assertEqual(r.status_code, 200)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_gateway_telegram.py tests/test_scaffold_gateways.py::TestTelegramOverlay -v`
Expected: scaffold tests fail with `FileNotFoundError` for telegram.py; router tests fail at `_import_telegram` step.

- [ ] **Step 3: Create the Telegram overlay**

Create `nuvel/backends/adk/templates_overlays/gateway-telegram/{{agent_package}}/gateways/telegram.py`:

```python
"""Telegram gateway for {{agent_name}}.

Receives Telegram bot webhook updates at POST /gateways/telegram, verifies
the secret token, dispatches text messages to the in-process ADK runner,
and posts replies via the Telegram Bot API. See the project README for
setup instructions.
"""

from __future__ import annotations

import asyncio
import logging
import os
import secrets

import httpx
from fastapi import APIRouter, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse

from {{agent_package}}.gateways._common import ensure_session, invoke_agent, session_key

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/gateways", tags=["gateway:telegram"])

TELEGRAM_API_BASE = "https://api.telegram.org"


def _verify_secret(token: str | None) -> None:
    expected = os.environ.get("TELEGRAM_WEBHOOK_SECRET")
    if not expected:
        raise HTTPException(status_code=500, detail="TELEGRAM_WEBHOOK_SECRET not configured")
    if not token or not secrets.compare_digest(token, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")


def _bot_token() -> str:
    tok = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not tok:
        raise HTTPException(status_code=500, detail="TELEGRAM_BOT_TOKEN not configured")
    return tok


async def _send_message(chat_id: int | str, text: str, *, reply_to: int | None = None,
                         message_thread_id: int | None = None) -> None:
    body: dict = {"chat_id": chat_id, "text": text}
    if reply_to is not None:
        body["reply_to_message_id"] = reply_to
    if message_thread_id is not None:
        body["message_thread_id"] = message_thread_id
    url = f"{TELEGRAM_API_BASE}/bot{_bot_token()}/sendMessage"
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(url, json=body)
        if r.status_code != 200:
            logger.warning("Telegram sendMessage failed: %s %s", r.status_code, r.text[:200])


async def _send_chat_action(chat_id: int | str, action: str = "typing") -> None:
    url = f"{TELEGRAM_API_BASE}/bot{_bot_token()}/sendChatAction"
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            await client.post(url, json={"chat_id": chat_id, "action": action})
    except Exception:
        # Typing indicator is best-effort.
        pass


def _is_text_message(update: dict) -> bool:
    msg = update.get("message")
    return isinstance(msg, dict) and isinstance(msg.get("text"), str) and bool(msg["text"])


def _should_invoke_in_group(msg: dict, bot_username: str | None) -> bool:
    """Mirror the well-behaved-bot convention: in groups, only invoke when
    the bot is mentioned, the message is a slash command targeting the bot,
    or the message replies to a bot-authored message."""
    chat_type = (msg.get("chat") or {}).get("type", "private")
    if chat_type == "private":
        return True
    text = msg.get("text", "")
    if bot_username and f"@{bot_username}" in text:
        return True
    if text.startswith("/"):
        return True
    reply_to = msg.get("reply_to_message") or {}
    if (reply_to.get("from") or {}).get("is_bot"):
        return True
    return False


async def _process_message(request: Request, msg: dict) -> None:
    runner = request.app.state.runner
    app_name = request.app.state.app_name
    user_id, session_id = session_key("telegram", msg)
    await ensure_session(runner.session_service, app_name, user_id, session_id)

    chat_id = (msg.get("chat") or {}).get("id")
    thread_id = msg.get("message_thread_id")
    reply_to = msg.get("message_id") if (msg.get("chat") or {}).get("type") != "private" else None

    # Best-effort typing indicator while the agent runs.
    keepalive = asyncio.create_task(_typing_keepalive(chat_id))
    try:
        reply = await invoke_agent(runner, user_id, session_id, msg["text"])
    except Exception:
        logger.exception("Telegram: agent invocation failed")
        reply = "Sorry, something went wrong."
    finally:
        keepalive.cancel()

    await _send_message(chat_id, reply, reply_to=reply_to, message_thread_id=thread_id)


async def _typing_keepalive(chat_id: int | str) -> None:
    """Re-send `typing` every 4s until cancelled (Telegram's indicator lasts ~5s)."""
    try:
        while True:
            await _send_chat_action(chat_id, "typing")
            await asyncio.sleep(4)
    except asyncio.CancelledError:
        return


@router.post("/telegram")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
):
    _verify_secret(x_telegram_bot_api_secret_token)
    update = await request.json()

    if not _is_text_message(update):
        return JSONResponse(content={"ok": True, "skipped": "non-text update"})

    msg = update["message"]
    bot_username = os.environ.get("TELEGRAM_BOT_USERNAME") or None
    if not _should_invoke_in_group(msg, bot_username):
        return JSONResponse(content={"ok": True, "skipped": "group: no mention/command/reply"})

    asyncio.create_task(_process_message(request, msg))
    return JSONResponse(content={"ok": True})
```

- [ ] **Step 4: Stitch Telegram contributions in scaffold**

In `nuvel/backends/adk/scaffold.py`, before `_build_replacements`'s `return` statement, populate the gateway placeholders for Telegram:

```python
gateway_imports_lines: list[str] = []
gateway_mounts_lines: list[str] = []
gateway_requirements_lines: list[str] = []
gateway_env_blocks: list[str] = []
gateway_readme_blocks: list[str] = []

if with_telegram:
    gateway_imports_lines.append(f"from {package}.gateways import telegram as gw_telegram")
    gateway_mounts_lines.append("    app.include_router(gw_telegram.router)")
    gateway_env_blocks.append(_TELEGRAM_ENV_BLOCK)
    gateway_readme_blocks.append(_TELEGRAM_README_BLOCK)

# (slack, teams contributions are added in Tasks 4 and 5.)

gateway_imports = ("\n".join(gateway_imports_lines) + "\n") if gateway_imports_lines else ""
gateway_mounts = ("\n".join(gateway_mounts_lines) + "\n") if gateway_mounts_lines else ""
gateway_requirements = ("\n".join(gateway_requirements_lines) + "\n") if gateway_requirements_lines else ""
gateway_env_block = "\n".join(gateway_env_blocks)
gateway_readme_section = "\n".join(gateway_readme_blocks)
```

Add the Telegram constants near the existing Composio constants in the same file:

```python
_TELEGRAM_ENV_BLOCK = (
    "# ── Telegram gateway ─────────────────────────────────────────────\n"
    "# Required: bot token from @BotFather\n"
    "TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here\n"
    "# Required: random string passed to Telegram when calling setWebhook;\n"
    "# Telegram echoes it back via the X-Telegram-Bot-Api-Secret-Token header.\n"
    "TELEGRAM_WEBHOOK_SECRET=change_me_to_a_long_random_string\n"
    "# Optional: bot username (without @) for group-mention detection.\n"
    "# TELEGRAM_BOT_USERNAME=your_bot_username\n"
    "\n"
)

_TELEGRAM_README_BLOCK = (
    "\n## Channel: Telegram\n"
    "\n"
    "1. Create a bot via @BotFather and copy the token into `TELEGRAM_BOT_TOKEN`.\n"
    "2. Set a long random string as `TELEGRAM_WEBHOOK_SECRET`.\n"
    "3. After deploying, register the webhook:\n"
    "\n"
    "   ```\n"
    "   curl -F \"url=https://<your-deployment>/gateways/telegram\" \\\n"
    "        -F \"secret_token=$TELEGRAM_WEBHOOK_SECRET\" \\\n"
    "        \"https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/setWebhook\"\n"
    "   ```\n"
    "\n"
    "4. For local dev, run `ngrok http 8000` first and pass the ngrok URL above.\n"
    "5. The bot replies inline in DMs and in the same thread/topic in groups.\n"
)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_gateway_telegram.py tests/test_scaffold_gateways.py tests/test_gateway_common.py -q`
Expected: all pass.

Then run the full suite: `python -m pytest tests/ -q` — all 163 + new tests pass.

- [ ] **Step 6: Manual smoke test**

```bash
rm -rf /tmp/nuvel-tg-smoke && mkdir -p /tmp/nuvel-tg-smoke
.venv/bin/python -m nuvel.cli new tg-smoke --output-dir /tmp/nuvel-tg-smoke --with-telegram
diff -r /tmp/nuvel-tg-smoke/tg-smoke/tg_smoke/gateways /Users/$(whoami)/Documents/Cursor/nuvel/.worktrees/feat-messaging-gateways/nuvel/backends/adk/templates_overlays/gateway-base/{{agent_package}}/gateways || true
ls /tmp/nuvel-tg-smoke/tg-smoke/tg_smoke/gateways/
grep -A 1 "TELEGRAM_BOT_TOKEN" /tmp/nuvel-tg-smoke/tg-smoke/.env.example
grep "Channel: Telegram" /tmp/nuvel-tg-smoke/tg-smoke/README.md
```

Expected: `gateways/{__init__.py, _common.py, telegram.py}` present, env/README contain the Telegram blocks, no `{{...}}` substrings remain.

- [ ] **Step 7: Commit**

```bash
git add nuvel/backends/adk/templates_overlays/gateway-telegram/ \
        nuvel/backends/adk/scaffold.py \
        tests/test_gateway_telegram.py tests/test_scaffold_gateways.py
git commit -m "feat(gateway): Telegram channel via --with-telegram

Adds the gateway-telegram overlay: a FastAPI APIRouter at
/gateways/telegram that verifies Telegram's secret token, dispatches
text messages to the in-process ADK runner via
gateways._common.invoke_agent, and posts replies via the Bot API.
Group messages require @-mention, /-command, or reply-to-bot to
trigger (well-behaved-bot convention).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Slack channel (end-to-end via Composio Slackbot)

**Goal:** `nuvel new --with-slack` produces an agent with a working `/gateways/slack/composio` webhook that receives Composio's normalized Slack events and replies via Composio's `SLACKBOT_SEND_MESSAGE` tool. Auto-enables `--with-composio`.

**Why after Telegram:** the channel pattern is now proven; Slack adds two new wrinkles — using the Composio SDK for outbound and dispatching by `trigger_slug`.

**Files:**
- Create: `nuvel/backends/adk/templates_overlays/gateway-slack/{{agent_package}}/gateways/slack.py`
- Modify: `nuvel/backends/adk/scaffold.py` (Slack contributions to placeholders + ENV/README constants)
- Create: `tests/test_gateway_slack.py`
- Modify: `tests/test_scaffold_gateways.py` (Slack-flag scaffold tests)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_scaffold_gateways.py`:

```python
class TestSlackOverlay(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        result = adk_scaffold("agent-sl", output_dir=self.tmpdir, with_slack=True)
        self.assertEqual(result["status"], "ok")
        # Slack auto-enables composio.
        self.assertTrue(result["with_composio"])
        self.agent_dir = Path(result["path"])

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_slack_module_exists(self):
        self.assertTrue((self.agent_dir / "agent_sl" / "gateways" / "slack.py").is_file())

    def test_run_adk_imports_and_mounts_slack(self):
        run_adk = (self.agent_dir / "run_adk.py").read_text()
        self.assertIn("from agent_sl.gateways import slack as gw_slack", run_adk)
        self.assertIn("app.include_router(gw_slack.router)", run_adk)

    def test_env_example_has_slack_block(self):
        env = (self.agent_dir / ".env.example").read_text()
        self.assertIn("COMPOSIO_WEBHOOK_SECRET", env)

    def test_readme_has_slack_section(self):
        readme = (self.agent_dir / "README.md").read_text()
        self.assertIn("Slack", readme)
        self.assertIn("composio trigger create", readme)
```

Create `tests/test_gateway_slack.py`:

```python
"""Unit tests for the generated agent's Slack gateway router."""

import importlib.util
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from nuvel.backends.adk.scaffold import scaffold_agent


def _scaffold_slack(tmpdir):
    result = scaffold_agent("sl-test", output_dir=tmpdir, with_slack=True)
    if result["status"] != "ok":
        raise AssertionError(result.get("message"))
    return Path(result["path"]) / "sl_test"


def _import_slack(pkg_dir: Path):
    init_path = pkg_dir / "gateways" / "__init__.py"
    spec = importlib.util.spec_from_file_location(
        "sl_test_gateways", init_path, submodule_search_locations=[str(pkg_dir / "gateways")]
    )
    pkg = importlib.util.module_from_spec(spec)
    sys.modules["sl_test_gateways"] = pkg
    spec.loader.exec_module(pkg)
    sub_path = pkg_dir / "gateways" / "slack.py"
    sub_spec = importlib.util.spec_from_file_location("sl_test_gateways.slack", sub_path)
    sub = importlib.util.module_from_spec(sub_spec)
    sys.modules["sl_test_gateways.slack"] = sub
    sub_spec.loader.exec_module(sub)
    return sub


class TestSlackRouter(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp()
        pkg = _scaffold_slack(cls.tmpdir)
        cls.sl = _import_slack(pkg)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def _client(self, runner_mock, composio_mock=None):
        app = FastAPI()
        app.state.runner = runner_mock
        app.state.app_name = "sl-test"
        app.state.composio_client = composio_mock or MagicMock()
        app.include_router(self.sl.router)
        with patch.dict("os.environ", {"COMPOSIO_WEBHOOK_SECRET": "s3cret"}, clear=False):
            yield TestClient(app)

    def test_missing_secret_returns_401(self):
        for client in self._client(AsyncMock()):
            r = client.post("/gateways/slack/composio", json={"trigger_slug": "x"})
            self.assertEqual(r.status_code, 401)

    def test_wrong_secret_returns_401(self):
        for client in self._client(AsyncMock()):
            r = client.post("/gateways/slack/composio?secret=wrong", json={"trigger_slug": "x"})
            self.assertEqual(r.status_code, 401)

    def test_unknown_trigger_is_noop_200(self):
        for client in self._client(AsyncMock()):
            r = client.post(
                "/gateways/slack/composio?secret=s3cret",
                json={"trigger_slug": "SLACKBOT_FUTURE_THING", "payload": {}},
            )
            self.assertEqual(r.status_code, 200)

    def test_dm_trigger_invokes_agent(self):
        for client in self._client(AsyncMock()):
            r = client.post(
                "/gateways/slack/composio?secret=s3cret",
                json={
                    "trigger_slug": "SLACKBOT_DIRECT_MESSAGE_RECEIVED",
                    "payload": {
                        "team_id": "T01", "channel": "D456", "user": "U012",
                        "text": "hello", "ts": "1700000000.001", "channel_type": "im",
                    },
                },
            )
            self.assertEqual(r.status_code, 200)

    def test_bot_message_is_dropped_to_prevent_loops(self):
        for client in self._client(AsyncMock()):
            r = client.post(
                "/gateways/slack/composio?secret=s3cret",
                json={
                    "trigger_slug": "SLACKBOT_DIRECT_MESSAGE_RECEIVED",
                    "payload": {"channel": "D1", "user": "U2", "text": "hi", "bot_id": "B1"},
                },
            )
            self.assertEqual(r.status_code, 200)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_gateway_slack.py tests/test_scaffold_gateways.py::TestSlackOverlay -v`
Expected: scaffold tests fail (file not found); router import fails.

- [ ] **Step 3: Create the Slack overlay**

Create `nuvel/backends/adk/templates_overlays/gateway-slack/{{agent_package}}/gateways/slack.py`:

```python
"""Slack gateway for {{agent_name}} via Composio Slackbot.

Receives Composio webhook deliveries at POST /gateways/slack/composio,
verifies the shared-secret query parameter, dispatches text messages to
the in-process ADK runner, and posts replies via the SLACKBOT_SEND_MESSAGE
Composio tool.

Setup is documented in this agent's README.md ("Channel: Slack" section).
"""

from __future__ import annotations

import asyncio
import logging
import os
import secrets

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse

from {{agent_package}}.gateways._common import ensure_session, invoke_agent, session_key

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/gateways", tags=["gateway:slack"])

# Triggers that produce a user-facing reply.
INVOKE_TRIGGERS = {
    "SLACKBOT_DIRECT_MESSAGE_RECEIVED",
    "SLACKBOT_CHANNEL_MESSAGE_RECEIVED",
}


def _verify_secret(request: Request) -> None:
    expected = os.environ.get("COMPOSIO_WEBHOOK_SECRET")
    if not expected:
        raise HTTPException(status_code=500, detail="COMPOSIO_WEBHOOK_SECRET not configured")
    provided = request.query_params.get("secret", "")
    if not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")


def _is_self_or_bot(payload: dict) -> bool:
    return bool(payload.get("bot_id")) or bool(payload.get("is_bot_message"))


def _should_invoke_channel_message(payload: dict) -> bool:
    """Channel-mention default: only invoke when the bot was @-mentioned.
    Override with SLACK_CHANNEL_TRIGGER_MODE=all to invoke on every message."""
    if os.environ.get("SLACK_CHANNEL_TRIGGER_MODE", "mention").lower() == "all":
        return True
    text = str(payload.get("text") or "")
    bot_user_id = os.environ.get("SLACK_BOT_USER_ID")
    if bot_user_id and f"<@{bot_user_id}>" in text:
        return True
    return False


async def _send_reply(composio_client, channel: str, text: str,
                       thread_ts: str | None = None) -> None:
    args = {"channel": channel, "markdown_text": text}
    if thread_ts:
        args["thread_ts"] = thread_ts
    try:
        # Composio Python SDK: synchronous .tools.execute(...). Run off-loop.
        await asyncio.to_thread(
            composio_client.tools.execute, "SLACKBOT_SEND_MESSAGE", arguments=args
        )
    except Exception:
        logger.exception("Slack: SLACKBOT_SEND_MESSAGE failed")


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
        reply = "Sorry, something went wrong."

    channel = payload.get("channel")
    thread_ts = payload.get("thread_ts") or payload.get("ts") if in_thread else None
    await _send_reply(composio, channel, reply, thread_ts=thread_ts)


@router.post("/slack/composio")
async def composio_webhook(request: Request):
    _verify_secret(request)
    body = await request.json()
    slug = body.get("trigger_slug", "")
    payload = body.get("payload") or {}

    if slug not in INVOKE_TRIGGERS:
        # All other triggers (reactions, channel_created, future ones): log only.
        logger.info("Slack: log-only trigger %s", slug)
        return JSONResponse(content={"ok": True, "skipped": "log-only trigger"})

    if _is_self_or_bot(payload):
        return JSONResponse(content={"ok": True, "skipped": "bot/self message"})

    if slug == "SLACKBOT_CHANNEL_MESSAGE_RECEIVED" and not _should_invoke_channel_message(payload):
        return JSONResponse(content={"ok": True, "skipped": "channel: no mention"})

    in_thread = slug == "SLACKBOT_CHANNEL_MESSAGE_RECEIVED"
    asyncio.create_task(_process(request, payload, in_thread=in_thread))
    return JSONResponse(content={"ok": True})
```

- [ ] **Step 4: Add Slack contributions to `_build_replacements` and ENV/README constants**

In `nuvel/backends/adk/scaffold.py`, extend the per-channel block inside `_build_replacements`:

```python
if with_slack:
    gateway_imports_lines.append(f"from {package}.gateways import slack as gw_slack")
    gateway_mounts_lines.append("    app.include_router(gw_slack.router)")
    gateway_env_blocks.append(_SLACK_ENV_BLOCK)
    gateway_readme_blocks.append(_SLACK_README_BLOCK)
```

Add the constants:

```python
_SLACK_ENV_BLOCK = (
    "# ── Slack gateway (via Composio Slackbot) ────────────────────────\n"
    "# Required: random shared secret used by Composio when delivering\n"
    "# trigger webhooks. Set the same value when running\n"
    "# `composio trigger create ... --webhook ...?secret=<this>`.\n"
    "COMPOSIO_WEBHOOK_SECRET=change_me_to_a_long_random_string\n"
    "# Optional: bot user ID for @-mention detection in channels.\n"
    "# SLACK_BOT_USER_ID=U0BOT...\n"
    "# Optional: 'all' to invoke on every channel message; default 'mention'.\n"
    "# SLACK_CHANNEL_TRIGGER_MODE=mention\n"
    "\n"
)

_SLACK_README_BLOCK = (
    "\n## Channel: Slack\n"
    "\n"
    "This gateway uses [Composio's Slackbot toolkit](https://docs.composio.dev/) for\n"
    "both inbound (webhook triggers) and outbound (`SLACKBOT_SEND_MESSAGE`).\n"
    "\n"
    "1. In the Composio dashboard, connect Slack to your workspace.\n"
    "2. Set `COMPOSIO_WEBHOOK_SECRET` in `.env` to a long random string.\n"
    "3. After deploying, register the triggers you want. Minimum:\n"
    "\n"
    "   ```\n"
    "   composio trigger create SLACKBOT_DIRECT_MESSAGE_RECEIVED \\\n"
    "       --webhook \"https://<your-deployment>/gateways/slack/composio?secret=$COMPOSIO_WEBHOOK_SECRET\"\n"
    "   composio trigger create SLACKBOT_CHANNEL_MESSAGE_RECEIVED \\\n"
    "       --webhook \"https://<your-deployment>/gateways/slack/composio?secret=$COMPOSIO_WEBHOOK_SECRET\"\n"
    "   ```\n"
    "\n"
    "4. (Optional) Set `SLACK_BOT_USER_ID` so channel-mentions only trigger replies\n"
    "   when the bot is explicitly @-mentioned (default behavior).\n"
)
```

(Note: this overlay does **not** contribute to `gateway_requirements` — Slack reuses the Composio SDK that `--with-composio` already pulled in.)

- [ ] **Step 5: Wire the Composio client onto `app.state` in run_adk.py**

The Slack handler reads `request.app.state.composio_client`. The `--with-composio` overlay already provides `composio_mcp.build_composio_mcp_toolset()` — but that's the MCP toolset, not a SDK client. Add a tiny line to the gateway scaffold path: in `gateway-base/{{agent_package}}/gateways/_common.py`, add:

```python
def get_composio_client():
    """Lazy import: only used when the Slack overlay is active."""
    from composio import Composio
    return Composio(api_key=os.environ.get("COMPOSIO_API_KEY"))
```

(Add `import os` to `_common.py`.)

In `nuvel/backends/adk/templates/run_adk.py`, after `add_endpoints(app)` and before `uvicorn.run(...)`, add a small block that runs only when gateways are present. The simplest approach: emit it as part of `{{gateway_mounts}}`. Rework the scaffold to prepend a one-time bootstrap line when **any** channel is enabled:

```python
if with_slack:
    gateway_mounts_lines.insert(0, "    from {package}.gateways._common import get_composio_client".format(package=package))
    gateway_mounts_lines.insert(1, "    app.state.composio_client = get_composio_client()")
    gateway_mounts_lines.append("    app.include_router(gw_slack.router)")
```

Also add the runner+app_name boilerplate that **all** gateway routers depend on. Replace the `if with_slack or with_telegram or with_teams:` overlay block with one that also injects state setup at the top of `gateway_mounts_lines`:

```python
if with_slack or with_telegram or with_teams:
    state_lines = [
        f"    app.state.app_name = \"{name}\"",
        # Build a Runner instance for in-process invocation.
        # In standard mode `get_fast_api_app` already constructed the runner;
        # we expose its session_service and root agent via app.state.
        # Simple approach: import the agent and wrap a Runner here.
        f"    from {package}.agent import root_agent as _root",
        "    from google.adk.runners import Runner as _Runner",
        "    app.state.runner = _Runner(app_name=app.state.app_name, "
        "agent=_root, session_service=app.state.session_service "
        "if hasattr(app.state, 'session_service') else None)",
    ]
    gateway_mounts_lines = state_lines + gateway_mounts_lines
```

(Implementation note: the exact mechanism for sharing the `session_service` between ADK's built-in `Runner` and the gateway runner depends on internals. If `get_fast_api_app` doesn't expose its session service via `app.state`, the simplest workaround is to construct a fresh `Runner` with the same `session_service_uri`. The implementer may need to adjust this small block to match ADK's actual public surface — verify by running the manual smoke test and using the gateway end-to-end. The contract that subsequent tasks rely on is just: `request.app.state.runner` is a working `Runner` and `request.app.state.app_name` is the agent name.)

- [ ] **Step 6: Run all tests**

Run: `python -m pytest tests/ -q`
Expected: all pass.

- [ ] **Step 7: Manual smoke test**

```bash
rm -rf /tmp/nuvel-sl-smoke && mkdir -p /tmp/nuvel-sl-smoke
.venv/bin/python -m nuvel.cli new sl-smoke --output-dir /tmp/nuvel-sl-smoke --with-slack
ls /tmp/nuvel-sl-smoke/sl-smoke/sl_smoke/gateways/
grep "COMPOSIO_WEBHOOK_SECRET" /tmp/nuvel-sl-smoke/sl-smoke/.env.example
grep "Channel: Slack" /tmp/nuvel-sl-smoke/sl-smoke/README.md
grep "composio_imports\|composio_extends" /tmp/nuvel-sl-smoke/sl-smoke/sl_smoke/tools/__init__.py  # verify --with-composio overlay applied
```

Expected: `slack.py` present, env contains the Slack block, Composio overlay is active.

- [ ] **Step 8: Commit**

```bash
git add nuvel/backends/adk/templates_overlays/gateway-slack/ \
        nuvel/backends/adk/templates_overlays/gateway-base/ \
        nuvel/backends/adk/scaffold.py \
        tests/test_gateway_slack.py tests/test_scaffold_gateways.py
git commit -m "feat(gateway): Slack channel via --with-slack (Composio Slackbot)

Adds the gateway-slack overlay: a FastAPI APIRouter at
/gateways/slack/composio that verifies a shared secret in the URL,
dispatches inbound Composio Slackbot triggers to the in-process ADK
runner, and posts replies via SLACKBOT_SEND_MESSAGE. Channel
mentions invoke only on @-mention by default (override with
SLACK_CHANNEL_TRIGGER_MODE=all).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Teams channel (sidecar port of v1)

**Goal:** `nuvel new --with-teams` produces an agent with a `gateways/teams_bridge.py` module that ports the v1 verbatim, with the env-var renames from spec §9 applied. The bridge runs as a separate process (`python -m {agent_package}.gateways.teams_bridge`).

**Files:**
- Create: `nuvel/backends/adk/templates_overlays/gateway-teams/{{agent_package}}/gateways/teams_bridge.py`
- Modify: `nuvel/backends/adk/scaffold.py` (Teams contributions)
- Create: `tests/test_gateway_teams_bridge.py`
- Modify: `tests/test_scaffold_gateways.py` (Teams scaffold tests)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_scaffold_gateways.py`:

```python
class TestTeamsOverlay(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        result = adk_scaffold("agent-tm", output_dir=self.tmpdir, with_teams=True)
        self.assertEqual(result["status"], "ok")
        self.agent_dir = Path(result["path"])

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_teams_bridge_module_exists(self):
        self.assertTrue((self.agent_dir / "agent_tm" / "gateways" / "teams_bridge.py").is_file())

    def test_requirements_includes_microsoft_agents(self):
        reqs = (self.agent_dir / "requirements.txt").read_text()
        self.assertIn("microsoft-agents-hosting-aiohttp", reqs)
        self.assertIn("microsoft-agents-authentication-msal", reqs)
        self.assertIn("aiohttp", reqs)
        self.assertIn("pypdf", reqs)

    def test_env_example_has_teams_block(self):
        env = (self.agent_dir / ".env.example").read_text()
        self.assertIn("TEAMS_BRIDGE_PORT", env)

    def test_readme_has_teams_section(self):
        readme = (self.agent_dir / "README.md").read_text()
        self.assertIn("Teams", readme)
        self.assertIn("teams_bridge", readme)

    def test_teams_bridge_uses_renamed_envvars(self):
        bridge = (self.agent_dir / "agent_tm" / "gateways" / "teams_bridge.py").read_text()
        # Old names that MUST be gone:
        self.assertNotIn("DATA_AGENT_BASE_URL", bridge)
        self.assertNotIn("DATA_AGENT_APP_NAME", bridge)
        self.assertNotIn("DATA_AGENT_API_KEY", bridge)
        self.assertNotIn("DATA_AGENT_TIMEOUT_SECONDS", bridge)
        # M365_* renamed to TEAMS_*:
        self.assertNotIn("M365_BRIDGE_PORT", bridge)
        self.assertNotIn("M365_PROGRESS_TEXTS", bridge)
        self.assertNotIn("M365_ENABLE_INTERMEDIATE_MESSAGES", bridge)
        # New names:
        self.assertIn("AGENT_BASE_URL", bridge)
        self.assertIn("AGENT_APP_NAME", bridge)
        self.assertIn("API_KEY", bridge)
        self.assertIn("AGENT_TIMEOUT_SECONDS", bridge)
        self.assertIn("TEAMS_BRIDGE_PORT", bridge)
        self.assertIn("TEAMS_PROGRESS_TEXTS", bridge)

    def test_teams_bridge_default_app_name_is_scaffolded(self):
        bridge = (self.agent_dir / "agent_tm" / "gateways" / "teams_bridge.py").read_text()
        # Default for AGENT_APP_NAME should be the scaffolded agent name.
        self.assertIn('os.getenv("AGENT_APP_NAME", "agent-tm")', bridge)
```

Create `tests/test_gateway_teams_bridge.py`:

```python
"""Smoke tests for the generated agent's Teams sidecar.

The Microsoft 365 Agents SDK is heavyweight (aiohttp, MSAL); these tests
verify only that the module *parses* and exposes the expected entry points.
Full integration with Bot Framework is exercised separately by the
operator (Agents Playground / Azure Bot Service)."""

import importlib.util
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from nuvel.backends.adk.scaffold import scaffold_agent


class TestTeamsBridgeParseable(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp()
        result = scaffold_agent("tm-test", output_dir=cls.tmpdir, with_teams=True)
        if result["status"] != "ok":
            raise AssertionError(result.get("message"))
        cls.bridge_path = Path(result["path"]) / "tm_test" / "gateways" / "teams_bridge.py"

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def test_module_parses(self):
        # Compile-only — avoids importing the heavy SDK.
        source = self.bridge_path.read_text()
        compile(source, str(self.bridge_path), "exec")

    def test_module_has_main(self):
        source = self.bridge_path.read_text()
        self.assertIn("def main(", source)
        self.assertIn('if __name__ == "__main__":', source)

    def test_module_has_dual_mode(self):
        source = self.bridge_path.read_text()
        self.assertIn("_has_service_connection_config", source)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_gateway_teams_bridge.py tests/test_scaffold_gateways.py::TestTeamsOverlay -v`
Expected: file not found.

- [ ] **Step 3: Port the v1 to the overlay**

Copy `reference/teams-v1/data-analysis-agent/run_m365_bridge.py` to
`nuvel/backends/adk/templates_overlays/gateway-teams/{{agent_package}}/gateways/teams_bridge.py`.

Apply these substitutions throughout the new file:

| Old | New |
|---|---|
| `DATA_AGENT_BASE_URL` (default) | `AGENT_BASE_URL` |
| `"data_analysis_agent"` (the v1's hardcoded default app name) | `"{{agent_name}}"` (the placeholder will be substituted at scaffold time) |
| `DATA_AGENT_APP_NAME` | `AGENT_APP_NAME` |
| `DATA_AGENT_API_KEY` | `API_KEY` |
| `DATA_AGENT_TIMEOUT_SECONDS` | `AGENT_TIMEOUT_SECONDS` |
| `M365_BRIDGE_PORT` | `TEAMS_BRIDGE_PORT` |
| `M365_BRIDGE_HOST` | `TEAMS_BRIDGE_HOST` |
| `M365_PROGRESS_EVENTS` | `TEAMS_PROGRESS_EVENTS` |
| `M365_ENABLE_INTERMEDIATE_MESSAGES` | `TEAMS_ENABLE_INTERMEDIATE_MESSAGES` |
| `M365_PROGRESS_MIN_DELAY_MS` | `TEAMS_PROGRESS_MIN_DELAY_MS` |
| `M365_PROGRESS_TEXTS` | `TEAMS_PROGRESS_TEXTS` |
| `M365_ENABLE_ATTACHMENT_CONTEXT` | `TEAMS_ENABLE_ATTACHMENT_CONTEXT` |
| `M365_MAX_ATTACHMENT_COUNT` | `TEAMS_MAX_ATTACHMENT_COUNT` |
| `M365_ENABLE_ATTACHMENT_DOWNLOAD` | `TEAMS_ENABLE_ATTACHMENT_DOWNLOAD` |
| `M365_MAX_ATTACHMENT_BYTES` | `TEAMS_MAX_ATTACHMENT_BYTES` |
| `M365_MAX_INLINE_B64_CHARS` | `TEAMS_MAX_INLINE_B64_CHARS` |
| `M365_FORWARD_RAW_ATTACHMENTS` | `TEAMS_FORWARD_RAW_ATTACHMENTS` |
| `class DataAnalysisAgentBridge` | `class AgentBridge` |
| `data_analysis_agent` (any other occurrence) | `{{agent_package}}` |

Adjust the docstring header to:

```python
"""Local Microsoft 365 / Teams bridge for {{agent_name}}.

Exposes /api/messages (Bot Framework format) and forwards each user
message to the agent server started by run_adk.py. Two operating modes
selected automatically by env: SDK mode when CONNECTIONS__SERVICE_CONNECTION__SETTINGS__*
is set, anonymous mode (Agents Playground) otherwise.

Run with: python -m {{agent_package}}.gateways.teams_bridge
"""
```

Verify the rewritten file with the test fixture for env-var names.

- [ ] **Step 4: Add Teams contributions to `_build_replacements` and constants**

In `nuvel/backends/adk/scaffold.py`:

```python
if with_teams:
    # Teams runs as a sidecar; nothing to import or mount in run_adk.py.
    gateway_requirements_lines.extend([
        "microsoft-agents-hosting-aiohttp",
        "microsoft-agents-authentication-msal",
        "aiohttp",
        "pypdf",
    ])
    gateway_env_blocks.append(_TEAMS_ENV_BLOCK)
    gateway_readme_blocks.append(_TEAMS_README_BLOCK.replace("{{agent_package}}", package))
```

Add the constants (kept verbose to mirror v1 behavior; trim later if noisy):

```python
_TEAMS_ENV_BLOCK = (
    "# ── Teams gateway (sidecar — runs separately) ───────────────────\n"
    "# The Teams bridge is a separate process. Run it with:\n"
    "#   python -m <agent_package>.gateways.teams_bridge\n"
    "#\n"
    "# SDK mode (production) — Azure Bot Service + Teams:\n"
    "# CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTID=...\n"
    "# CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTSECRET=...\n"
    "# CONNECTIONS__SERVICE_CONNECTION__SETTINGS__TENANTID=...\n"
    "#\n"
    "# Anonymous mode (Agents Playground / local dev): leave the three above\n"
    "# unset; the bridge accepts unauthenticated POSTs to /api/messages.\n"
    "#\n"
    "# Bridge → agent connection (defaults usually fine):\n"
    "# AGENT_BASE_URL=http://127.0.0.1:8000\n"
    "# AGENT_APP_NAME=<scaffolded agent name>\n"
    "# AGENT_TIMEOUT_SECONDS=120\n"
    "#\n"
    "# Bridge runtime:\n"
    "TEAMS_BRIDGE_PORT=3978\n"
    "# TEAMS_BRIDGE_HOST=localhost\n"
    "# TEAMS_ENABLE_INTERMEDIATE_MESSAGES=true\n"
    "# TEAMS_PROGRESS_TEXTS=Analyzing request...|Inspecting available data...|Running tools...|Preparing final response...\n"
    "# TEAMS_PROGRESS_MIN_DELAY_MS=350\n"
    "\n"
)

_TEAMS_README_BLOCK = (
    "\n## Channel: Microsoft Teams (sidecar)\n"
    "\n"
    "Teams is implemented as a separate process that proxies to this agent's\n"
    "REST API. It uses the Microsoft 365 Agents SDK, which is aiohttp-based\n"
    "and runs alongside (not inside) the FastAPI agent server.\n"
    "\n"
    "**Run command:**\n"
    "\n"
    "```\n"
    "python -m {{agent_package}}.gateways.teams_bridge\n"
    "```\n"
    "\n"
    "**Setup:**\n"
    "\n"
    "1. (Production) Register the bot in Azure Bot Service / Teams Developer Portal\n"
    "   and set `CONNECTIONS__SERVICE_CONNECTION__SETTINGS__{CLIENTID,CLIENTSECRET,TENANTID}`.\n"
    "2. (Local dev) Skip step 1 and run the bridge against the\n"
    "   [Microsoft 365 Agents Playground](https://aka.ms/agents-playground).\n"
    "3. Point the bot's messaging endpoint at `https://<bridge-host>:3978/api/messages`.\n"
    "\n"
    "The bridge handles JWT validation in production mode and falls back to an\n"
    "anonymous-POST mode for the Agents Playground when SDK config is absent.\n"
)
```

- [ ] **Step 5: Run tests**

Run: `python -m pytest tests/ -q`
Expected: all pass. The Teams parse test confirms the rewritten file compiles. `test_teams_bridge_default_app_name_is_scaffolded` confirms the `{{agent_name}}` substitution applied correctly during stamping.

- [ ] **Step 6: Manual smoke test**

```bash
rm -rf /tmp/nuvel-tm-smoke && mkdir -p /tmp/nuvel-tm-smoke
.venv/bin/python -m nuvel.cli new tm-smoke --output-dir /tmp/nuvel-tm-smoke --with-teams
ls /tmp/nuvel-tm-smoke/tm-smoke/tm_smoke/gateways/
grep "microsoft-agents" /tmp/nuvel-tm-smoke/tm-smoke/requirements.txt
grep "TEAMS_BRIDGE_PORT" /tmp/nuvel-tm-smoke/tm-smoke/.env.example
python -c "import ast; ast.parse(open('/tmp/nuvel-tm-smoke/tm-smoke/tm_smoke/gateways/teams_bridge.py').read())"
```

Expected: bridge present, parseable, requirements + env include the right new lines.

- [ ] **Step 7: Commit**

```bash
git add nuvel/backends/adk/templates_overlays/gateway-teams/ \
        nuvel/backends/adk/scaffold.py \
        tests/test_gateway_teams_bridge.py tests/test_scaffold_gateways.py
git commit -m "feat(gateway): Teams sidecar via --with-teams (port of v1 bridge)

Ports the production-tested run_m365_bridge.py to the gateway-teams
overlay with env-var renames per spec §9: DATA_AGENT_* -> AGENT_*,
M365_* -> TEAMS_*. The bridge runs as a separate aiohttp process via
python -m {agent_package}.gateways.teams_bridge and proxies to the
ADK FastAPI server's REST API. Dual-mode behavior preserved (SDK mode
for production, anonymous mode for Agents Playground).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Combined integration test, repo docs, cleanup

**Goal:** Verify all three channels coexist in a single scaffold without conflicts, document the feature at the repo level, and remove the temporary `reference/` directory.

**Files:**
- Modify: `tests/test_scaffold_gateways.py` (combined test)
- Modify: `README.md`
- Modify: `CONTRIBUTING.md`
- Delete: `reference/`

- [ ] **Step 1: Add combined-flags test**

Append to `tests/test_scaffold_gateways.py`:

```python
class TestAllChannelsTogether(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        result = adk_scaffold(
            "agent-all", output_dir=self.tmpdir,
            with_slack=True, with_telegram=True, with_teams=True,
        )
        self.assertEqual(result["status"], "ok")
        self.agent_dir = Path(result["path"])

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_all_three_modules_present(self):
        gw = self.agent_dir / "agent_all" / "gateways"
        for fname in ("__init__.py", "_common.py", "slack.py", "telegram.py", "teams_bridge.py"):
            self.assertTrue((gw / fname).is_file(), f"Missing: {fname}")

    def test_run_adk_mounts_slack_and_telegram_only(self):
        run_adk = (self.agent_dir / "run_adk.py").read_text()
        self.assertIn("gw_slack", run_adk)
        self.assertIn("gw_telegram", run_adk)
        self.assertNotIn("teams_bridge", run_adk)  # Teams is a sidecar, not mounted

    def test_env_example_contains_all_three_blocks(self):
        env = (self.agent_dir / ".env.example").read_text()
        self.assertIn("TELEGRAM_BOT_TOKEN", env)
        self.assertIn("COMPOSIO_WEBHOOK_SECRET", env)
        self.assertIn("TEAMS_BRIDGE_PORT", env)

    def test_readme_contains_all_three_sections(self):
        readme = (self.agent_dir / "README.md").read_text()
        self.assertIn("Channel: Slack", readme)
        self.assertIn("Channel: Telegram", readme)
        self.assertIn("Channel: Microsoft Teams", readme)

    def test_no_unrendered_placeholders_anywhere(self):
        # Walk the agent directory; no file should contain `{{` template syntax.
        for path in self.agent_dir.rglob("*"):
            if path.is_file() and path.suffix in (".py", ".md", ".txt", ".example"):
                content = path.read_text(errors="ignore")
                self.assertNotIn("{{", content, f"Unrendered placeholder in {path}")
```

- [ ] **Step 2: Run all tests**

Run: `python -m pytest tests/ -q`
Expected: all pass. Total: 163 + ~30 new = ~190+.

- [ ] **Step 3: Update repo README**

In the project-root `README.md`, find the feature-list section and add:

```markdown
- **Messaging gateways**: scaffold an agent reachable from Slack, Telegram, or MS Teams with one flag (`--with-slack`, `--with-telegram`, `--with-teams`). See [docs/superpowers/specs/2026-05-09-messaging-gateways-design.md](docs/superpowers/specs/2026-05-09-messaging-gateways-design.md).
```

(Place it adjacent to the existing feature highlights — style matches surrounding bullets.)

- [ ] **Step 4: Update CONTRIBUTING.md**

Append a section:

```markdown
## Adding a new messaging-app channel

Channels live as overlays under `nuvel/backends/adk/templates_overlays/gateway-*/`.
A new channel needs:

1. `gateway-<name>/{{agent_package}}/gateways/<name>.py` — FastAPI APIRouter (or a
   sidecar module if the channel's SDK is incompatible with FastAPI, like Teams).
2. A `--with-<name>` flag added to `nuvel/cli.py` and threaded through
   `scaffold_agent()` in `nuvel/backends/adk/scaffold.py`.
3. ENV / README contributions in `_build_replacements` (see how `_TELEGRAM_ENV_BLOCK`
   is wired).
4. Tests under `tests/test_gateway_<name>.py` and a scaffold-flag test in
   `tests/test_scaffold_gateways.py`.
5. The same flag added to the rejection lists in
   `nuvel/backends/claude_agent_sdk/scaffold.py` and
   `nuvel/backends/anthropic_managed_agents/scaffold.py`.
```

- [ ] **Step 5: Remove the reference directory**

```bash
rm -rf reference/
git status  # confirm reference/ is removed
```

(`.worktrees/` is gitignored, so this only affects the worktree's tracked state. The reference directory was never committed to the branch in the first place — the `rm` is just tidying the working copy.)

- [ ] **Step 6: Final test run + commit**

```bash
python -m pytest tests/ -q
git add README.md CONTRIBUTING.md tests/test_scaffold_gateways.py
git commit -m "docs+test: integration test for all three channels + repo-level docs

Adds a combined --with-slack/--with-telegram/--with-teams test verifying
no overlay conflicts and no unrendered placeholders. Documents the
feature in the project README and the channel-overlay convention in
CONTRIBUTING.md.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 7: Branch is ready for PR**

Run a final summary of what's on the branch:

```bash
git log --oneline main..HEAD
git diff --stat main..HEAD
```

Expected: ~6 commits (gitignore + spec + spec-revision + 5 feature commits + this docs commit), reasonable file count and line additions.

---

## Self-review notes

- **Spec coverage check:**
  - §1 architecture → covered across Tasks 2 (base), 3 (Telegram), 4 (Slack), 5 (Teams).
  - §2 backend scope → Task 1.
  - §3 CLI surface → Task 1.
  - §4 scaffold mechanism → Task 2.
  - §5 `_common` module → Task 2.
  - §6 session mapping → Task 2 (`session_key`) + per-channel tests.
  - §7 Slack handler → Task 4.
  - §8 Telegram handler → Task 3.
  - §9 Teams sidecar → Task 5.
  - §10 env vars → wired across Tasks 3-5.
  - §11 webhook auth → Task 2 (`/gateways` PUBLIC_PREFIX) + per-channel verification.
  - §12 error handling → covered via try/except in handler stubs.
  - §13 testing → tests created in each task.
  - §14 README structure → wired across Tasks 3-5; integration in Task 6.
  - §15 doc updates → Task 6.

- **Risk: ADK Runner construction in `run_adk.py`.** Task 4 Step 5 notes that the exact mechanism for sharing `session_service` between ADK's `get_fast_api_app` and the gateway's `Runner` may need a small adjustment depending on ADK internals. Implementer must verify by importing the rendered `run_adk.py` and exercising a gateway end-to-end manually before committing Task 4. If the public surface is unsuitable, the fallback is to construct an independent `Runner` with a fresh `DatabaseSessionService(SESSION_SERVICE_URI)` — same backing store, two `Runner` instances. Acceptable.

- **Streaming-mode coexistence:** the existing streaming branch in `run_adk.py` (`if streaming_enabled:`) builds a different `app`. Gateway mounts must apply to **both** branches. Implementer should add the `gateway_mounts` substitution to both code paths in `run_adk.py` (the substitution mechanism is text-based, so this happens automatically as long as `{{gateway_mounts}}` appears in both branches). If the placeholder only appears once, gateways won't mount in streaming mode — verify in the manual smoke test of Task 4.
