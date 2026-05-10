"""Tests for skill_curator — post-task self-improving skills proposer.

The curator is opt-in via NUVEL_SKILL_CURATOR=1, runs as an
after_agent_callback, and writes proposals to ~/.nuvel/skill-proposals/
for human review. It NEVER auto-applies changes.
"""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from nuvel.callbacks import skill_curator as sc


def _event(text=None, function_call=None, author="agent"):
    parts = []
    if text is not None:
        parts.append(SimpleNamespace(text=text, function_call=None, function_response=None))
    if function_call is not None:
        parts.append(
            SimpleNamespace(text=None, function_call=SimpleNamespace(name=function_call), function_response=None)
        )
    content = SimpleNamespace(parts=parts) if parts else None
    return SimpleNamespace(content=content, author=author)


def _ctx(events, agent_name="my_agent"):
    session = SimpleNamespace(events=events, id="s1", app_name="app", user_id="u1")
    return SimpleNamespace(session=session, agent_name=agent_name, state={})


class TestComplexityHeuristic(unittest.TestCase):
    def test_below_threshold_simple(self):
        events = [_event(text="hi"), _event(text="hello")]
        self.assertFalse(sc._is_complex(events, min_tools=5, min_events=12))

    def test_above_threshold_by_tool_count(self):
        events = [_event(function_call=f"tool_{i}") for i in range(6)]
        self.assertTrue(sc._is_complex(events, min_tools=5, min_events=12))

    def test_above_threshold_by_event_count(self):
        events = [_event(text=f"e{i}") for i in range(15)]
        self.assertTrue(sc._is_complex(events, min_tools=5, min_events=12))


class TestEnvVarGate(unittest.TestCase):
    def test_disabled_when_env_unset(self):
        events = [_event(function_call=f"t_{i}") for i in range(10)]
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("NUVEL_SKILL_CURATOR", None)
            llm_fn = mock.Mock()
            sc.skill_curator(_ctx(events), llm_fn=llm_fn)
            llm_fn.assert_not_called()

    def test_enabled_when_env_set(self):
        events = [_event(function_call=f"t_{i}") for i in range(10)]
        llm_fn = mock.Mock(return_value=json.dumps({"action": "noop", "rationale": "ok"}))
        with mock.patch.dict(os.environ, {"NUVEL_SKILL_CURATOR": "1"}):
            sc.skill_curator(_ctx(events), llm_fn=llm_fn)
            llm_fn.assert_called_once()


class TestNoopBelowThreshold(unittest.TestCase):
    def test_no_llm_call_when_simple(self):
        events = [_event(text="hi")]
        llm_fn = mock.Mock()
        with mock.patch.dict(os.environ, {"NUVEL_SKILL_CURATOR": "1"}):
            sc.skill_curator(_ctx(events), llm_fn=llm_fn)
            llm_fn.assert_not_called()


class TestProposalWriting(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(self.id().replace(".", "_") + "_proposals")
        # Use a real tmp dir under cwd-tmp via env override
        import tempfile
        self.tmpdir = tempfile.mkdtemp(prefix="nuvel-prop-")
        self.env = {
            "NUVEL_SKILL_CURATOR": "1",
            "NUVEL_SKILL_PROPOSALS_DIR": self.tmpdir,
        }

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_propose_new_writes_proposal(self):
        events = [_event(function_call=f"t_{i}") for i in range(8)]
        payload = {
            "action": "propose_new",
            "skill_name": "csv-summarizer",
            "rationale": "Pattern of CSV reads + summaries repeated.",
            "patch": "# csv-summarizer\n\nWhen given a CSV...",
        }
        llm_fn = mock.Mock(return_value=json.dumps(payload))
        with mock.patch.dict(os.environ, self.env):
            sc.skill_curator(_ctx(events), llm_fn=llm_fn)
        files = list(Path(self.tmpdir).glob("*.md"))
        self.assertEqual(len(files), 1)
        content = files[0].read_text()
        self.assertIn("csv-summarizer", content)
        self.assertIn("propose_new", content)
        self.assertIn("Pattern of CSV reads", content)

    def test_noop_action_writes_nothing(self):
        events = [_event(function_call=f"t_{i}") for i in range(8)]
        llm_fn = mock.Mock(return_value=json.dumps({"action": "noop", "rationale": "fine"}))
        with mock.patch.dict(os.environ, self.env):
            sc.skill_curator(_ctx(events), llm_fn=llm_fn)
        files = list(Path(self.tmpdir).glob("*.md"))
        self.assertEqual(files, [])

    def test_patch_existing_writes_proposal(self):
        events = [_event(function_call=f"t_{i}") for i in range(8)]
        payload = {
            "action": "patch_existing",
            "skill_name": "adk-tool-creation",
            "rationale": "Missing edge case for async tools.",
            "patch": "Append section: ## Async tools ...",
        }
        llm_fn = mock.Mock(return_value=json.dumps(payload))
        with mock.patch.dict(os.environ, self.env):
            sc.skill_curator(_ctx(events), llm_fn=llm_fn)
        files = list(Path(self.tmpdir).glob("*.md"))
        self.assertEqual(len(files), 1)
        self.assertIn("patch_existing", files[0].read_text())
        self.assertIn("adk-tool-creation", files[0].name)


class TestMalformedLLMResponse(unittest.TestCase):
    def test_bad_json_logs_and_skips(self):
        import tempfile
        tmp = tempfile.mkdtemp(prefix="nuvel-prop-")
        try:
            events = [_event(function_call=f"t_{i}") for i in range(8)]
            llm_fn = mock.Mock(return_value="not-json {{{")
            env = {"NUVEL_SKILL_CURATOR": "1", "NUVEL_SKILL_PROPOSALS_DIR": tmp}
            with mock.patch.dict(os.environ, env):
                sc.skill_curator(_ctx(events), llm_fn=llm_fn)
            self.assertEqual(list(Path(tmp).glob("*.md")), [])
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    def test_unknown_action_skipped(self):
        import tempfile
        tmp = tempfile.mkdtemp(prefix="nuvel-prop-")
        try:
            events = [_event(function_call=f"t_{i}") for i in range(8)]
            llm_fn = mock.Mock(return_value=json.dumps({"action": "delete_everything"}))
            env = {"NUVEL_SKILL_CURATOR": "1", "NUVEL_SKILL_PROPOSALS_DIR": tmp}
            with mock.patch.dict(os.environ, env):
                sc.skill_curator(_ctx(events), llm_fn=llm_fn)
            self.assertEqual(list(Path(tmp).glob("*.md")), [])
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)


class TestExistingSkillEnumeration(unittest.TestCase):
    def test_lists_skill_directories(self):
        import tempfile
        d = tempfile.mkdtemp(prefix="nuvel-skills-")
        try:
            (Path(d) / "skill-a").mkdir()
            (Path(d) / "skill-a" / "SKILL.md").write_text("---\nname: skill-a\n---\nbody")
            (Path(d) / "skill-b").mkdir()
            (Path(d) / "skill-b" / "SKILL.md").write_text("---\nname: skill-b\n---\nbody")
            (Path(d) / "not-a-skill").mkdir()  # no SKILL.md
            names = sc._existing_skill_names(Path(d))
            self.assertEqual(sorted(names), ["skill-a", "skill-b"])
        finally:
            import shutil
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
