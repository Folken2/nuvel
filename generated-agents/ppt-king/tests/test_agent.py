"""
Regression tests for ppt-king using ADK record/replay.

Usage:
    # Record a golden conversation (calls real LLM)
    RECORD=true python -m pytest tests/test_agent.py -v -k test_golden

    # Replay tests (no LLM calls, instant, free)
    python -m pytest tests/test_agent.py -v

How it works:
    - RecordingsPlugin captures LLM requests/responses and tool calls to YAML
    - ReplayPlugin replays those recordings deterministically
    - Both are activated via session state keys (dormant otherwise)

Adding new test cases:
    1. Add a new test method with a unique recording directory
    2. Run with RECORD=true to capture the golden recording
    3. Commit the YAML file to version control
    4. Future runs replay from the recording (no LLM cost)
"""

import asyncio
import os
import sys
from pathlib import Path

import pytest

# Ensure project root is importable
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv()

from google.adk.agents import LlmAgent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

RECORDINGS_DIR = Path(__file__).parent / "recordings"
IS_RECORD_MODE = os.getenv("RECORD", "").lower() in ("true", "1", "yes")

# Import the agent
from ppt_king.agent import root_agent
from ppt_king.plugins import recordings, replay


def _run_agent(
    user_message: str,
    recording_dir: str,
) -> list[types.Content]:
    """Run the agent with record or replay mode active.

    Args:
        user_message: The user message to send.
        recording_dir: Path to the recording directory for this test case.

    Returns:
        List of response content objects from the agent.
    """
    loop = asyncio.new_event_loop()

    try:
        session_service = InMemorySessionService()
        plugins = []

        if IS_RECORD_MODE:
            plugins.append(recordings)
        else:
            plugins.append(replay)

        from google.adk.app import App

        app = App(
            name="ppt_king",
            root_agent=root_agent,
            plugins=plugins,
        )

        runner = Runner(
            app=app,
            session_service=session_service,
        )

        session = loop.run_until_complete(
            session_service.create_session(
                app_name="ppt_king",
                user_id="test_user",
            )
        )

        # Set record or replay config in session state
        Path(recording_dir).mkdir(parents=True, exist_ok=True)

        if IS_RECORD_MODE:
            state_key = "_adk_recordings_config"
        else:
            state_key = "_adk_replay_config"

        session.state[state_key] = {
            "dir": recording_dir,
            "user_message_index": 0,
        }

        content = types.Content(
            role="user",
            parts=[types.Part(text=user_message)],
        )

        responses = []
        async def collect():
            async for event in runner.run_async(
                user_id="test_user",
                session_id=session.id,
                new_message=content,
            ):
                if event.content and event.content.parts:
                    if any(p.text for p in event.content.parts):
                        responses.append(event.content)

        loop.run_until_complete(collect())
        return responses

    finally:
        loop.close()


class TestAgent:
    """Regression tests using golden recordings.

    Run with RECORD=true to capture new recordings.
    Run without RECORD to replay from existing recordings.
    """

    def test_golden_greeting(self):
        """Test basic greeting interaction."""
        recording_dir = str(RECORDINGS_DIR / "greeting")

        if not IS_RECORD_MODE:
            recording_file = Path(recording_dir) / "generated-recordings.yaml"
            if not recording_file.exists():
                pytest.skip(
                    "No recording found. Run with RECORD=true first: "
                    "RECORD=true python -m pytest tests/test_agent.py -v -k test_golden_greeting"
                )

        responses = _run_agent("Hello!", recording_dir)

        # Basic assertions — the agent should respond
        assert len(responses) > 0, "Agent should produce at least one response"

        # Check that the response contains text
        texts = []
        for resp in responses:
            for part in resp.parts:
                if part.text:
                    texts.append(part.text)
        full_response = " ".join(texts)
        assert len(full_response) > 0, "Agent should produce non-empty text"
