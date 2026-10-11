"""The generated agent's tests/test_agent.py must really record and replay.

Its ``_run_agent`` hands RecordingsPlugin / ReplayPlugin their config through
session state. That config has to be in the session the Runner loads, and has
to name a streaming mode ADK 2.10 supports; otherwise neither plugin engages
and the "replay" tests silently call the real model.

These tests scaffold an agent, import its tests/test_agent.py, and swap the
real root agent for one that never touches a network: a model-free agent for
recording, and an LlmAgent on a fake in-process model for the round-trip.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import AsyncGenerator

import pytest
import yaml
from google.adk.agents import LlmAgent
from google.adk.agents.base_agent import BaseAgent
from google.adk.events.event import Event
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from nuvel.backends.adk.scaffold import scaffold_agent

_AGENT = "record-replay-agent"
_PKG = "record_replay_agent"


@pytest.fixture(scope="module")
def agent_tests_module():
    """Scaffold an agent and import its generated tests/test_agent.py."""
    tmp = tempfile.mkdtemp(prefix="nuvel-record-replay-")
    result = scaffold_agent(_AGENT, output_dir=tmp)
    assert result["status"] == "ok", result
    root = Path(result["path"])
    path = root / "tests" / "test_agent.py"
    # Importing the module switches NUVEL_RECORD_REPLAY on for the process;
    # put it back afterwards so later tests still see the gate's default.
    saved_flag = os.environ.get("NUVEL_RECORD_REPLAY")
    spec = importlib.util.spec_from_file_location("_generated_test_agent", path)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)  # inserts the project root on sys.path
        yield module
    finally:
        if saved_flag is None:
            os.environ.pop("NUVEL_RECORD_REPLAY", None)
        else:
            os.environ["NUVEL_RECORD_REPLAY"] = saved_flag
        for mod in list(sys.modules):
            if mod == _PKG or mod.startswith(_PKG + "."):
                sys.modules.pop(mod, None)
        while str(root) in sys.path:
            sys.path.remove(str(root))
        shutil.rmtree(tmp, ignore_errors=True)


class _EchoAgent(BaseAgent):
    """A model-free agent: replies "ok" without any model call."""

    async def _run_async_impl(self, ctx) -> AsyncGenerator[Event, None]:
        yield Event(
            author=self.name,
            invocation_id=ctx.invocation_id,
            content=types.Content(role="model", parts=[types.Part(text="ok")]),
        )


_model_calls: list[LlmRequest] = []


class _FakeLlm(BaseLlm):
    """An in-process model that logs its calls and returns canned text."""

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        _model_calls.append(llm_request)
        yield LlmResponse(
            content=types.Content(
                role="model", parts=[types.Part(text="recorded reply")]
            )
        )


def _texts(responses) -> list[str]:
    return [p.text for c in responses for p in c.parts if p.text]


def test_record_mode_writes_generated_recordings(
    agent_tests_module, tmp_path, monkeypatch
):
    monkeypatch.setattr(agent_tests_module, "IS_RECORD_MODE", True)
    monkeypatch.setattr(agent_tests_module, "root_agent", _EchoAgent(name="echo"))

    responses = agent_tests_module._run_agent("Hello!", str(tmp_path))

    assert _texts(responses) == ["ok"]
    recording = tmp_path / "generated-recordings.yaml"
    assert recording.exists(), "RecordingsPlugin never engaged"
    # A model-free run has nothing to record; ADK writes the empty set as {}.
    assert not (yaml.safe_load(recording.read_text()) or {}).get("recordings")


def test_record_then_replay_round_trip_skips_the_model(
    agent_tests_module, tmp_path, monkeypatch
):
    agent = LlmAgent(
        name="assistant",
        model=_FakeLlm(model="fake-model"),
        instruction="Reply briefly.",
    )
    monkeypatch.setattr(agent_tests_module, "root_agent", agent)
    _model_calls.clear()

    monkeypatch.setattr(agent_tests_module, "IS_RECORD_MODE", True)
    recorded = agent_tests_module._run_agent("Hello!", str(tmp_path))
    assert _texts(recorded) == ["recorded reply"]
    assert len(_model_calls) == 1

    data = yaml.safe_load((tmp_path / "generated-recordings.yaml").read_text())
    assert len(data["recordings"]) == 1
    assert data["recordings"][0]["agent_name"] == "assistant"

    monkeypatch.setattr(agent_tests_module, "IS_RECORD_MODE", False)
    replayed = agent_tests_module._run_agent("Hello!", str(tmp_path))
    assert _texts(replayed) == ["recorded reply"]
    assert len(_model_calls) == 1, "replay called the model instead of the recording"
