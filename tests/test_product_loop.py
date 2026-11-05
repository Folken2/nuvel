"""The product loop, end to end: create an agent, run it, assert what it produced.

`nuvel agent create` scaffolds a real ADK agent into a temp directory. A driver
process then imports that generated package, swaps its model for a scripted
in-process one, and runs one turn through the agent's own ``AgentHarness``
Runner with the full plugin chain. The turn makes the model call the agent's
``save_memory`` tool, so the run leaves three things on disk:

- the run record: ``traces/conversations/<date>_<session>.json``
- the trace: ``traces/<date>_<session>.jsonl``
- the files: the memory file the tool wrote, and the user's attachment saved
  as an artifact by the SaveFilesAsArtifacts plugin.

No network: both child processes start behind a guard that refuses every
non-loopback connection and name lookup, and the model never leaves the process. A regression in the scaffold, the runner wiring, or
the trace / artifact writers fails this test.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import nuvel

_AGENT = "product-loop-agent"
_PKG = "product_loop_agent"
_FACT = "The client's invoices are due on the 15th."
_ATTACHMENT = b"line one of the client's notes\n"
_REPLY = "Saved it to memory."

# Every child process starts through this: it refuses non-loopback connections
# and name lookups, then runs the real command (``-m module`` or a script path)
# as ``__main__``.
_NO_NETWORK = textwrap.dedent(
    """
    import ipaddress
    import runpy
    import socket
    import sys

    def _is_loopback(host):
        try:
            return ipaddress.ip_address(host).is_loopback
        except ValueError:
            return host == "localhost"

    _connect = socket.socket.connect
    _getaddrinfo = socket.getaddrinfo

    def _connect_loopback_only(sock, address):
        if isinstance(address, tuple) and not _is_loopback(address[0]):
            raise OSError(f"network access attempted: {address!r}")
        return _connect(sock, address)

    def _getaddrinfo_loopback_only(host, *args, **kwargs):
        if host is not None and not _is_loopback(host):
            raise OSError(f"name lookup attempted: {host!r}")
        return _getaddrinfo(host, *args, **kwargs)

    socket.socket.connect = _connect_loopback_only
    socket.getaddrinfo = _getaddrinfo_loopback_only
    socket.gethostbyname = lambda host: _getaddrinfo_loopback_only(host, None)[0][4][0]

    del sys.argv[0]
    if sys.argv[0] == "-m":
        del sys.argv[0]
        runpy.run_module(sys.argv[0], run_name="__main__", alter_sys=True)
    else:
        runpy.run_path(sys.argv[0], run_name="__main__")
    """
)

# Runs inside the generated agent's project directory, in its own process, so
# the generated package's import-time config (TRACE_DIR, the AgentHarness
# singleton) never leaks into the rest of the test session.
_DRIVER = textwrap.dedent(
    """
    import asyncio
    import json
    from typing import AsyncGenerator

    from google.adk.models.base_llm import BaseLlm
    from google.adk.models.llm_request import LlmRequest
    from google.adk.models.llm_response import LlmResponse
    from google.genai import types

    from {pkg}.agent import root_agent
    from {pkg}.harness import AgentHarness

    FACT, REPLY, ATTACHMENT = {fact!r}, {reply!r}, {attachment!r}
    calls = []

    class ScriptedLlm(BaseLlm):
        # Turn 1: call save_memory. Turn 2: answer in text.
        async def generate_content_async(
            self, llm_request: LlmRequest, stream: bool = False
        ) -> AsyncGenerator[LlmResponse, None]:
            calls.append(sorted(llm_request.tools_dict))
            if len(calls) == 1:
                part = types.Part(function_call=types.FunctionCall(
                    name="save_memory", args={{"content": FACT, "topic": "billing"}},
                ))
            else:
                part = types.Part(text=REPLY)
            yield LlmResponse(
                model_version="scripted-e2e",
                content=types.Content(role="model", parts=[part]),
                usage_metadata=types.GenerateContentResponseUsageMetadata(
                    prompt_token_count=100, candidates_token_count=10,
                    total_token_count=110,
                ),
            )

    root_agent.model = ScriptedLlm(model="scripted-e2e")

    async def main():
        harness = AgentHarness.get({pkg!r})
        runner = harness.build_runner(agent=root_agent)
        session = await runner.session_service.create_session(
            app_name={pkg!r}, user_id="client"
        )
        message = types.Content(role="user", parts=[
            types.Part(text="Remember: " + FACT),
            types.Part(inline_data=types.Blob(
                data=ATTACHMENT, mime_type="text/plain", display_name="notes.txt",
            )),
        ])
        texts = []
        async for event in runner.run_async(
            user_id="client", session_id=session.id, new_message=message
        ):
            if event.content:
                texts += [p.text for p in event.content.parts or [] if p.text]
        await runner.close()
        # The trace JSONL is written by a background task that exits once its
        # queue idles; asyncio.run() would cancel it, so let it drain first.
        pending = asyncio.all_tasks() - {{asyncio.current_task()}}
        if pending:
            await asyncio.wait(pending, timeout=30)
        print(json.dumps({{"session_id": session.id, "texts": texts, "calls": calls}}))

    asyncio.run(main())
    """
)


def _run_offline(
    args: list[str], *, guard: Path, cwd: Path, env: dict[str, str]
) -> subprocess.CompletedProcess:
    """Run ``python <args>`` behind the no-network guard; fail on a non-zero exit."""
    result = subprocess.run(
        [sys.executable, str(guard), *args],
        cwd=cwd, env=env, capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, (
        f"{args} exited {result.returncode}\n"
        f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )
    return result


def test_create_run_and_assert_the_artefact(tmp_path: Path) -> None:
    # Start from the defaults a fresh client gets: no opt-in NUVEL_* features, no
    # developer trace / artifact / session config leaking into the run.
    env = {
        k: v for k, v in os.environ.items()
        if not k.startswith(("TRACE_", "NUVEL_", "ARTIFACT_", "SESSION_SERVICE"))
    }
    env.update(
        AGENTS_OUTPUT_DIR=str(tmp_path),
        DEV_MODE="true",
        TRACE_DIR=str(tmp_path / "traces"),
        MEMORY_DIR=str(tmp_path / "memory"),
        ARTIFACT_SERVICE_URI=f"file://{tmp_path / 'artifacts'}",
        # litellm otherwise fetches its model-price map over the network at import.
        LITELLM_LOCAL_MODEL_COST_MAP="True",
        PYTHONDONTWRITEBYTECODE="1",
        # The children run from the temp dir: import the nuvel under test, not whatever
        # a relative PYTHONPATH or another editable install would resolve to there.
        PYTHONPATH=os.pathsep.join(
            [str(Path(nuvel.__file__).resolve().parents[1])]
            + [str(Path(p).resolve()) for p in os.environ.get("PYTHONPATH", "").split(os.pathsep) if p]
        ),
    )

    guard = tmp_path / "no_network.py"
    guard.write_text(_NO_NETWORK, encoding="utf-8")

    # 1. Create: the CLI a client runs.
    _run_offline(
        ["-m", "nuvel", "agent", "create", _AGENT,
         "--output-dir", str(tmp_path), "--description", "End-to-end product loop agent"],
        guard=guard, cwd=tmp_path, env=env,
    )
    project = tmp_path / _AGENT
    assert (project / _PKG / "agent.py").is_file()
    assert (project / _PKG / "harness.py").is_file()
    assert (project / "run_adk.py").is_file()

    # 2. Run: one turn through the generated agent's own Runner.
    driver = tmp_path / "drive_agent.py"
    driver.write_text(
        _DRIVER.format(pkg=_PKG, fact=_FACT, reply=_REPLY, attachment=_ATTACHMENT),
        encoding="utf-8",
    )
    result = _run_offline(
        [str(driver)],
        guard=guard,
        cwd=project,
        env={**env, "PYTHONPATH": os.pathsep.join([str(project), env["PYTHONPATH"]])},
    )
    outcome = json.loads(result.stdout.strip().splitlines()[-1])
    session_id = outcome["session_id"]
    # The trace and run-record writers name their files after this session-id prefix.
    file_id = session_id[:16]

    assert outcome["texts"] == [_REPLY]
    assert len(outcome["calls"]) == 2, "the model was not called once per step"
    assert "save_memory" in outcome["calls"][0], "the agent's tools never reached the model"

    # 3a. Files: the tool's write, and the attachment saved as an artifact.
    memory_file = tmp_path / "memory" / "topics" / "billing.md"
    assert memory_file.is_file(), sorted(p.name for p in (tmp_path / "memory").rglob("*"))
    assert _FACT in memory_file.read_text(encoding="utf-8")
    artifacts = [p for p in (tmp_path / "artifacts").rglob("*") if p.is_file()]
    assert any(p.read_bytes() == _ATTACHMENT for p in artifacts), (
        f"attachment not saved as an artifact; found {artifacts}"
    )

    # 3b. Run record: the consolidated per-conversation JSON `nuvel eval` reads.
    records = list((tmp_path / "traces" / "conversations").glob(f"*_{file_id}.json"))
    assert len(records) == 1, f"expected one run record, found {records}"
    record = json.loads(records[0].read_text(encoding="utf-8"))
    assert record["meta"]["session_id"] == session_id
    assert record["meta"]["agent"] == _PKG
    assert record["meta"]["model"] == "scripted-e2e"
    assert "save_memory" in record["tools_available"]
    summary = record["summary"]
    assert summary["turn_count"] == 1
    assert summary["total_llm_calls"] == 2
    assert summary["total_tool_calls"] == 1
    assert summary["total_tokens"] == 220
    (turn,) = record["turns"]
    assert _FACT in turn["user_input"]
    (tool_call,) = turn["tool_calls"]
    assert tool_call["tool"] == "save_memory"
    assert tool_call["status"] == "success"
    assert tool_call["args"] == {"content": _FACT, "topic": "billing"}
    assert turn["llm_calls"][-1]["response"] == _REPLY

    # 3c. Trace: the per-event JSONL, one ordered stream for the run.
    traces = list((tmp_path / "traces").glob(f"*_{file_id}.jsonl"))
    assert len(traces) == 1, f"expected one trace file, found {traces}"
    events = [json.loads(line) for line in traces[0].read_text(encoding="utf-8").splitlines()]
    kinds = [e["event"] for e in events]
    assert kinds[0] == "user_message" and kinds[-1] == "run_end", kinds
    for kind in ("run_start", "llm_request", "llm_response", "tool_start", "tool_end"):
        assert kind in kinds, f"trace is missing {kind!r}: {kinds}"
    assert {e["session_id"] for e in events} == {session_id}
    assert len({e["trace_id"] for e in events}) == 1
    run_end = events[-1]
    assert run_end["llm_calls"] == 2
    assert run_end["tool_calls"] == 1
    assert run_end["total_tokens"] == 220
