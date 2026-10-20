"""Plugins must be safe on a shared ADK server.

An ADK server builds one instance of each plugin and runs every session
through it, often concurrently. Per-run state therefore has to be keyed by
invocation (or kept in session state); anything stored flat on ``self`` leaks
between users. Session state is also client-writable through ADK's HTTP API,
so a plugin must not trust it for file paths.

Most tests run against both copies of the chain: the meta-agent's
``nuvel.plugins`` and the generated-agent template, scaffolded into a temp dir.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import re
import shutil
import sys
import tempfile
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import AsyncGenerator

import pytest
from google.adk.agents.base_agent import BaseAgent
from google.adk.apps import App
from google.adk.events.event import Event
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import InMemoryRunner
from google.genai import types

from nuvel.backends.adk.scaffold import scaffold_agent

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_PKG_DIR = (
    REPO_ROOT / "nuvel" / "backends" / "adk" / "templates" / "{{agent_package}}"
)
META_PKG_DIR = REPO_ROOT / "nuvel"

_AGENT = "shared-server-agent"
_PKG = "shared_server_agent"


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def template_pkg():
    """Scaffold a generated agent and make its package importable."""
    tmp = tempfile.mkdtemp(prefix="nuvel-shared-server-")
    result = scaffold_agent(_AGENT, output_dir=tmp)
    assert result["status"] == "ok", result
    root = str(Path(result["path"]))
    sys.path.insert(0, root)
    try:
        yield _PKG
    finally:
        for mod in list(sys.modules):
            if mod == _PKG or mod.startswith(_PKG + "."):
                sys.modules.pop(mod, None)
        sys.path.remove(root)
        shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture(params=["meta", "template"])
def plugins_pkg(request):
    """Dotted name of a plugins package: the meta-agent's or the template's."""
    if request.param == "meta":
        return "nuvel.plugins"
    return request.getfixturevalue("template_pkg") + ".plugins"


def _mod(pkg: str, name: str):
    return importlib.import_module(f"{pkg}.{name}")


# ── Fake ADK contexts ────────────────────────────────────────────────


def _inv(session_id: str, text: str = "hi", agent: str = "root"):
    return SimpleNamespace(
        invocation_id=f"inv-{uuid.uuid4().hex[:8]}",
        session=SimpleNamespace(id=session_id, state={}),
        agent=SimpleNamespace(name=agent, tools=[]),
        user_content=types.Content(role="user", parts=[types.Part(text=text)]),
    )


def _cb(inv, agent: str = "root"):
    return SimpleNamespace(
        invocation_id=inv.invocation_id,
        agent_name=agent,
        state={},
        session=inv.session,
    )


def _tool_ctx(inv, call_id: str = "call-1", agent: str = "root"):
    return SimpleNamespace(
        invocation_id=inv.invocation_id,
        agent_name=agent,
        state={},
        session=inv.session,
        function_call_id=call_id,
    )


def _tool(name: str = "lookup"):
    return SimpleNamespace(name=name)


def _text_response(text: str, *, model: str = "", prompt=0, completion=0):
    usage = None
    if prompt or completion:
        usage = types.GenerateContentResponseUsageMetadata(
            prompt_token_count=prompt,
            candidates_token_count=completion,
            total_token_count=prompt + completion,
        )
    return LlmResponse(
        content=types.Content(role="model", parts=[types.Part(text=text)]),
        model_version=model or None,
        usage_metadata=usage,
    )


class _EchoAgent(BaseAgent):
    """A model-free agent: replies "ok" so a real Runner can drive plugins."""

    async def _run_async_impl(self, ctx) -> AsyncGenerator[Event, None]:
        yield Event(
            author=self.name,
            invocation_id=ctx.invocation_id,
            content=types.Content(role="model", parts=[types.Part(text="ok")]),
        )


async def _run_echo(plugins, *, state=None) -> list[str]:
    """Run one turn of _EchoAgent through a real Runner; return reply texts."""
    runner = InMemoryRunner(
        app=App(name="t", root_agent=_EchoAgent(name="echo"), plugins=plugins)
    )
    session = await runner.session_service.create_session(
        app_name="t", user_id="u", state=state or {}
    )
    texts = []
    async for event in runner.run_async(
        user_id="u",
        session_id=session.id,
        new_message=types.Content(role="user", parts=[types.Part(text="hi")]),
    ):
        if event.content and event.content.parts:
            texts.extend(p.text for p in event.content.parts if p.text)
    return texts


# ── MemoryPlugin (template only) ─────────────────────────────────────


async def test_memory_plugin_does_not_break_agent_runs(template_pkg):
    """ADK calls before_agent_callback(agent=, callback_context=); a different
    signature raises inside the plugin manager and aborts every run."""
    memory_plugin = _mod(f"{template_pkg}.plugins", "memory_plugin")
    texts = await _run_echo([memory_plugin.MemoryPlugin()])
    assert texts == ["ok"]


# ── Recordings / Replay ──────────────────────────────────────────────


async def test_recordings_ignore_client_supplied_dir_by_default(
    plugins_pkg, tmp_path, monkeypatch
):
    monkeypatch.delenv("NUVEL_RECORD_REPLAY", raising=False)
    target = tmp_path / "chosen-by-client"
    plugins = importlib.import_module(plugins_pkg)
    state = {"_adk_recordings_config": {"dir": str(target), "user_message_index": 0, "streaming_mode": "none"}}

    assert await _run_echo([plugins.recordings], state=state) == ["ok"]
    assert not target.exists()


async def test_recordings_write_when_enabled(plugins_pkg, tmp_path, monkeypatch):
    """Positive control: the opt-in flag restores ADK's recording behaviour."""
    monkeypatch.setenv("NUVEL_RECORD_REPLAY", "1")
    target = tmp_path / "recordings"
    plugins = importlib.import_module(plugins_pkg)
    state = {"_adk_recordings_config": {"dir": str(target), "user_message_index": 0, "streaming_mode": "none"}}

    await _run_echo([plugins.recordings], state=state)
    assert (target / "generated-recordings.yaml").exists()


async def test_replay_ignores_client_supplied_config_by_default(
    plugins_pkg, tmp_path, monkeypatch
):
    monkeypatch.delenv("NUVEL_RECORD_REPLAY", raising=False)
    plugins = importlib.import_module(plugins_pkg)
    state = {
        "_adk_replay_config": {"dir": str(tmp_path / "missing"), "user_message_index": 0, "streaming_mode": "none"}
    }
    # With replay active this raises: the recordings file does not exist.
    assert await _run_echo([plugins.replay], state=state) == ["ok"]


# ── TracePlugin ──────────────────────────────────────────────────────


def _trace_plugin(plugins_pkg, tmp_path, monkeypatch):
    trace_mod = _mod(plugins_pkg, "trace_plugin")
    monkeypatch.setattr(trace_mod, "_TRACE_DIR", tmp_path)
    monkeypatch.setattr(trace_mod, "_TRACE_ENABLED", True)
    plugin = trace_mod.TracePlugin()
    records: list[dict] = []
    plugin._file_writer = SimpleNamespace(write=records.append)
    return plugin, records


async def _start_run(plugin, inv):
    await plugin.on_user_message_callback(
        invocation_context=inv, user_message=inv.user_content
    )
    await plugin.before_run_callback(invocation_context=inv)


async def test_trace_keeps_concurrent_runs_apart(plugins_pkg, tmp_path, monkeypatch):
    plugin, records = _trace_plugin(plugins_pkg, tmp_path, monkeypatch)
    a = _inv("sess-a", "question from A")
    b = _inv("sess-b", "question from B")

    await _start_run(plugin, a)
    await _start_run(plugin, b)  # B starts while A is still in flight
    await plugin.before_model_callback(
        callback_context=_cb(a), llm_request=LlmRequest(model="m", contents=[])
    )
    await plugin.after_model_callback(
        callback_context=_cb(a), llm_response=_text_response("secret answer for A")
    )
    await plugin.after_run_callback(invocation_context=b)
    await plugin.after_run_callback(invocation_context=a)

    a_response = [r for r in records if r["event"] == "llm_response"]
    assert [r["session_id"] for r in a_response] == ["sess-a"]

    trace_ids = {
        r["session_id"]: r["trace_id"] for r in records if r["event"] == "run_start"
    }
    for r in records:
        assert r["trace_id"] == trace_ids[r["session_id"]], r

    b_end = next(
        r for r in records if r["event"] == "run_end" and r["session_id"] == "sess-b"
    )
    assert b_end["llm_calls"] == 0

    conv_dir = tmp_path / "conversations"
    a_conv = json.loads(next(conv_dir.glob("*sess-a*.json")).read_text())
    b_conv = json.loads(next(conv_dir.glob("*sess-b*.json")).read_text())
    assert a_conv["turns"][0]["user_input"] == "question from A"
    assert a_conv["turns"][0]["llm_calls"][0]["response"] == "secret answer for A"
    assert b_conv["turns"][0]["user_input"] == "question from B"
    assert "secret answer for A" not in json.dumps(b_conv)


async def test_trace_run_cost_is_per_run(plugins_pkg, tmp_path, monkeypatch):
    plugin, records = _trace_plugin(plugins_pkg, tmp_path, monkeypatch)
    for session in ("sess-1", "sess-2"):
        inv = _inv(session)
        await _start_run(plugin, inv)
        await plugin.before_model_callback(
            callback_context=_cb(inv), llm_request=LlmRequest(model="m", contents=[])
        )
        await plugin.after_model_callback(
            callback_context=_cb(inv),
            llm_response=_text_response(
                "answer", model="moonshotai/kimi-k2.5", prompt=1000, completion=500
            ),
        )
        await plugin.after_run_callback(invocation_context=inv)

    costs = [r["total_cost_usd"] for r in records if r["event"] == "run_end"]
    assert costs[0] is not None
    assert costs == [costs[0], costs[0]]


async def test_trace_tool_timing_stays_out_of_session_state(
    plugins_pkg, tmp_path, monkeypatch
):
    plugin, records = _trace_plugin(plugins_pkg, tmp_path, monkeypatch)
    inv = _inv("sess-t")
    await _start_run(plugin, inv)
    ctx = _tool_ctx(inv)
    await plugin.before_tool_callback(tool=_tool(), tool_args={}, tool_context=ctx)
    await plugin.after_tool_callback(
        tool=_tool(), tool_args={}, tool_context=ctx, result={"status": "ok"}
    )
    assert ctx.state == {}
    tool_end = next(r for r in records if r["event"] == "tool_end")
    assert tool_end["duration_ms"] is not None


# ── SkillCuratorPlugin ───────────────────────────────────────────────


def _curator_env(monkeypatch, tmp_path):
    monkeypatch.setenv("NUVEL_SKILL_CURATOR", "1")
    monkeypatch.setenv("NUVEL_SKILL_CURATOR_MIN_EVENTS", "1")
    monkeypatch.setenv("NUVEL_SKILL_PROPOSALS_DIR", str(tmp_path / "proposals"))
    monkeypatch.setenv("NUVEL_SKILLS_DIR", str(tmp_path / "skills"))


def _event(text: str, author: str = "root"):
    return SimpleNamespace(
        author=author,
        content=types.Content(role="model", parts=[types.Part(text=text)]),
    )


async def test_skill_curator_keeps_concurrent_runs_apart(
    plugins_pkg, tmp_path, monkeypatch
):
    _curator_env(monkeypatch, tmp_path)
    prompts: list[str] = []

    def llm_fn(prompt: str) -> str:
        prompts.append(prompt)
        return json.dumps({"action": "noop"})

    plugin = _mod(plugins_pkg, "skill_curator_plugin").SkillCuratorPlugin(llm_fn=llm_fn)
    a, b = _inv("sess-a"), _inv("sess-b")
    await plugin.before_run_callback(invocation_context=a)
    await plugin.on_event_callback(invocation_context=a, event=_event("private note of A"))
    await plugin.before_run_callback(invocation_context=b)  # must not wipe A
    await plugin.on_event_callback(invocation_context=b, event=_event("note of B"))
    await plugin.after_run_callback(invocation_context=b)
    await plugin.after_run_callback(invocation_context=a)

    b_prompt, a_prompt = prompts
    assert "note of B" in b_prompt and "private note of A" not in b_prompt
    assert "private note of A" in a_prompt and "note of B" not in a_prompt


async def test_skill_curator_llm_call_does_not_block_event_loop(
    plugins_pkg, tmp_path, monkeypatch
):
    _curator_env(monkeypatch, tmp_path)

    def slow_llm(prompt: str) -> str:
        time.sleep(0.3)  # a synchronous SDK call, like genai's generate_content
        return json.dumps({"action": "noop"})

    plugin = _mod(plugins_pkg, "skill_curator_plugin").SkillCuratorPlugin(llm_fn=slow_llm)
    inv = _inv("sess-slow")
    await plugin.before_run_callback(invocation_context=inv)
    await plugin.on_event_callback(invocation_context=inv, event=_event("work"))

    ticks = 0

    async def ticker():
        nonlocal ticks
        while True:
            ticks += 1
            await asyncio.sleep(0.01)

    task = asyncio.create_task(ticker())
    await asyncio.sleep(0)
    before = ticks
    await plugin.after_run_callback(invocation_context=inv)
    during = ticks - before
    task.cancel()
    assert during >= 5, f"event loop starved: only {during} ticks in 0.3s"


# ── ToolEventsPlugin ─────────────────────────────────────────────────

_PNG_B64 = "iVBORw0KGgo" + "A" * 2000


async def _tool_round(plugin, inv, call_id="call-1", result=None):
    ctx = _tool_ctx(inv, call_id)
    await plugin.before_tool_callback(tool=_tool(), tool_args={}, tool_context=ctx)
    await plugin.after_tool_callback(
        tool=_tool(), tool_args={}, tool_context=ctx, result=result or {"status": "ok"}
    )
    return ctx


async def test_tool_events_are_scoped_to_their_run(plugins_pkg):
    plugin = _mod(plugins_pkg, "tool_events").ToolEventsPlugin()
    a, b = _inv("sess-a"), _inv("sess-b")
    image_result = {"stdout": json.dumps({"image_base64": _PNG_B64})}

    await plugin.before_run_callback(invocation_context=a)
    await plugin.before_run_callback(invocation_context=b)
    await _tool_round(plugin, a, result=image_result)

    assert [e["type"] for e in plugin.get_tool_events(a.invocation_id)] == [
        "tool_start",
        "tool_end",
    ]
    assert plugin.get_captured_images(a.invocation_id) == [_PNG_B64]
    assert plugin.get_tool_events(b.invocation_id) == []
    assert plugin.get_captured_images(b.invocation_id) == []


async def test_tool_events_are_released_when_the_run_ends(plugins_pkg):
    plugin = _mod(plugins_pkg, "tool_events").ToolEventsPlugin()
    inv = _inv("sess-a")
    await plugin.before_run_callback(invocation_context=inv)
    await _tool_round(plugin, inv, result={"stdout": json.dumps({"image_base64": _PNG_B64})})
    await plugin.after_run_callback(invocation_context=inv)

    assert plugin.get_tool_events(inv.invocation_id) == []
    assert plugin.get_captured_images(inv.invocation_id) == []


async def test_tool_events_stream_to_the_registered_session_only(plugins_pkg):
    plugin = _mod(plugins_pkg, "tool_events").ToolEventsPlugin()
    a, b = _inv("sess-a"), _inv("sess-b")
    queue: asyncio.Queue = asyncio.Queue()
    plugin.set_event_queue("sess-a", queue)

    await plugin.before_run_callback(invocation_context=a)
    await plugin.before_run_callback(invocation_context=b)
    await _tool_round(plugin, b)
    assert queue.empty()
    await _tool_round(plugin, a)
    assert [queue.get_nowait()["type"] for _ in range(2)] == ["tool_start", "tool_end"]


async def test_tool_events_timing_stays_out_of_session_state(plugins_pkg):
    plugin = _mod(plugins_pkg, "tool_events").ToolEventsPlugin()
    inv = _inv("sess-a")
    await plugin.before_run_callback(invocation_context=inv)
    ctx = await _tool_round(plugin, inv)
    assert ctx.state == {}


# ── ConsoleLoggerPlugin ──────────────────────────────────────────────

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


async def test_console_logger_counts_per_run(plugins_pkg, capsys):
    plugin = _mod(plugins_pkg, "console_logger_plugin").ConsoleLoggerPlugin()
    a, b = _inv("sess-aaaa"), _inv("sess-bbbb")
    await plugin.before_run_callback(invocation_context=a)
    await plugin.before_run_callback(invocation_context=b)
    await plugin.before_model_callback(
        callback_context=_cb(a), llm_request=LlmRequest(model="m", contents=[])
    )
    capsys.readouterr()
    await plugin.after_run_callback(invocation_context=b)
    b_done = _ANSI.sub("", capsys.readouterr().err)
    assert "llm calls: 0" in b_done


async def test_console_logger_timing_stays_out_of_session_state(plugins_pkg):
    plugin = _mod(plugins_pkg, "console_logger_plugin").ConsoleLoggerPlugin()
    inv = _inv("sess-a")
    await plugin.before_run_callback(invocation_context=inv)
    ctx = await _tool_round(plugin, inv)
    assert ctx.state == {}


# ── ResiliencePlugin rate limiting ───────────────────────────────────


async def test_rate_limit_is_per_session(plugins_pkg):
    plugin = _mod(plugins_pkg, "resilience_plugin").ResiliencePlugin()
    noisy, quiet = _inv(f"noisy-{uuid.uuid4()}"), _inv(f"quiet-{uuid.uuid4()}")

    results = [
        await plugin.before_tool_callback(
            tool=_tool(), tool_args={}, tool_context=_tool_ctx(noisy)
        )
        for _ in range(100)
    ]
    assert results[-1] is not None and results[-1]["error"] == "Rate limit exceeded"

    assert (
        await plugin.before_tool_callback(
            tool=_tool(), tool_args={}, tool_context=_tool_ctx(quiet)
        )
        is None
    )


@pytest.mark.parametrize("utils_pkg", ["nuvel.utils", "template"])
def test_keyed_rate_limiter_is_bounded(utils_pkg, request):
    if utils_pkg == "template":
        utils_pkg = request.getfixturevalue("template_pkg") + ".utils"
    resilience = _mod(utils_pkg, "resilience")
    limiter = resilience.KeyedRateLimiter(rate=1.0, burst=1, max_keys=2)
    for key in ("a", "b", "c"):
        assert limiter.allow(key)
    assert len(limiter) == 2
    # "a" was evicted as least recently used, so it starts with a full bucket.
    assert limiter.allow("a")
    assert not limiter.allow("c")


@pytest.mark.parametrize("utils_pkg", ["nuvel.utils", "template"])
def test_keyed_rate_limiter_caps_the_total_across_keys(utils_pkg, request):
    """Per-key buckets alone let a caller mint fresh keys (sessions) for a
    full bucket each; a total bucket bounds the server-wide rate anyway."""
    if utils_pkg == "template":
        utils_pkg = request.getfixturevalue("template_pkg") + ".utils"
    resilience = _mod(utils_pkg, "resilience")
    limiter = resilience.KeyedRateLimiter(
        rate=0.001, burst=1, total_rate=0.001, total_burst=3
    )
    allowed = [limiter.allow(f"key-{i}") for i in range(10)]
    assert allowed.count(True) == 3
    # A denial by the total cap leaves the key's own bucket untouched.
    assert resilience.KeyedRateLimiter(
        rate=0.001, burst=1, total_rate=0.001, total_burst=0
    ).allow("k") is False


async def test_rate_limit_caps_fresh_sessions_server_wide(plugins_pkg, monkeypatch):
    plugin_mod = _mod(plugins_pkg, "resilience_plugin")
    resilience = importlib.import_module(plugin_mod.__name__.rsplit(".", 2)[0] + ".utils.resilience")
    assert resilience.tool_rate_limiter.total is not None  # shipped with a cap
    monkeypatch.setattr(
        plugin_mod,
        "tool_rate_limiter",
        resilience.KeyedRateLimiter(rate=0.001, burst=5, total_rate=0.001, total_burst=10),
    )
    plugin = plugin_mod.ResiliencePlugin()
    results = [
        await plugin.before_tool_callback(
            tool=_tool(), tool_args={}, tool_context=_tool_ctx(_inv(f"fresh-{i}"))
        )
        for i in range(50)
    ]
    assert sum(r is None for r in results) == 10


# ── Both chains stay in sync ─────────────────────────────────────────

_IDENTICAL_FILES = [
    "plugins/trace_plugin.py",
    "plugins/conversation_trace_writer.py",
    "plugins/tool_events.py",
    "plugins/console_logger_plugin.py",
    "plugins/cost_guard_plugin.py",
    "plugins/context_window_plugin.py",
    "plugins/record_replay_plugin.py",
    "utils/resilience.py",
]


@pytest.mark.parametrize("rel", _IDENTICAL_FILES)
def test_shared_plugin_files_match_between_chains(rel):
    assert (META_PKG_DIR / rel).read_bytes() == (TEMPLATE_PKG_DIR / rel).read_bytes(), (
        f"{rel} drifted between nuvel/ and the generated-agent template; "
        "fix one and copy it to the other."
    )
