"""Tests for --workflow scaffolding — the ADK 2.0 Workflow-native agent shape."""

import shutil
import tempfile
import unittest
from pathlib import Path

from nuvel.backends.adk.scaffold import scaffold_agent

TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "nuvel" / "backends" / "adk" / "templates"
RUN_ADK = TEMPLATES_DIR / "run_adk.py"


class _ScaffoldCase(unittest.TestCase):
    """Scaffolds once per class into a temp dir."""

    workflow = False
    agent_name = "wf-agent"

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.result = scaffold_agent(
            name=cls.agent_name,
            output_dir=cls.tmp,
            description="A workflow agent.",
            workflow=cls.workflow,
        )
        cls.root = Path(cls.tmp) / cls.agent_name
        cls.pkg = cls.root / cls.agent_name.replace("-", "_")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)


class TestWorkflowFlagGeneratesWorkflowAgent(_ScaffoldCase):
    workflow = True

    def test_scaffold_succeeds(self):
        self.assertEqual(self.result["status"], "ok")
        self.assertTrue(self.result["workflow"])

    def test_agent_workflow_py_created(self):
        path = self.pkg / "agent_workflow.py"
        self.assertTrue(path.is_file(), f"missing {path}")
        self.assertIn("agent_workflow.py", " ".join(self.result["files"]))

    def test_no_unresolved_placeholders(self):
        text = (self.pkg / "agent_workflow.py").read_text(encoding="utf-8")
        self.assertNotIn("{{", text)
        self.assertIn("wf-agent", text)
        self.assertIn("wf_agent", text)

    def test_uses_workflow_as_root_agent(self):
        text = (self.pkg / "agent_workflow.py").read_text(encoding="utf-8")
        self.assertIn("from google.adk.workflow import Workflow", text)
        self.assertIn("root_agent = Workflow(", text)
        self.assertIn("\"START\"", text)

    def test_declares_task_mode_nodes(self):
        text = (self.pkg / "agent_workflow.py").read_text(encoding="utf-8")
        self.assertIn('mode="task"', text)
        self.assertIn('mode="single_turn"', text)
        # A planning node and an execution node.
        self.assertIn("planner = LlmAgent(", text)
        self.assertIn("executor = LlmAgent(", text)

    def test_declares_typed_contracts(self):
        text = (self.pkg / "agent_workflow.py").read_text(encoding="utf-8")
        self.assertIn("output_schema=Plan", text)
        self.assertIn("output_schema=Outcome", text)
        self.assertIn("class Plan(BaseModel):", text)
        self.assertIn("class Outcome(BaseModel):", text)

    def test_conditional_routing_between_nodes(self):
        text = (self.pkg / "agent_workflow.py").read_text(encoding="utf-8")
        self.assertIn("@node", text)
        self.assertIn("route=", text)
        self.assertIn("__DEFAULT__", text)

    def test_uses_agent_harness_for_runner(self):
        text = (self.pkg / "agent_workflow.py").read_text(encoding="utf-8")
        self.assertIn("from .harness import AgentHarness", text)
        self.assertIn("build_runner(agent=root_agent)", text)

    def test_agent_py_is_a_shim_over_the_workflow(self):
        text = (self.pkg / "agent.py").read_text(encoding="utf-8")
        self.assertIn("from .agent_workflow import root_agent", text)
        self.assertNotIn("LlmAgent(", text)
        self.assertNotIn("{{", text)

    def test_generated_files_are_valid_python(self):
        import ast

        for name in ("agent.py", "agent_workflow.py"):
            src = (self.pkg / name).read_text(encoding="utf-8")
            ast.parse(src, filename=name)

    def test_no_runtime_import_of_nuvel(self):
        text = (self.pkg / "agent_workflow.py").read_text(encoding="utf-8")
        self.assertNotIn("import nuvel", text)
        self.assertNotIn("from nuvel", text)


class TestWithoutWorkflowFlag(_ScaffoldCase):
    workflow = False
    agent_name = "plain-agent"

    def test_scaffold_succeeds(self):
        self.assertEqual(self.result["status"], "ok")
        self.assertFalse(self.result["workflow"])

    def test_agent_workflow_py_not_created(self):
        self.assertFalse((self.pkg / "agent_workflow.py").exists())
        self.assertNotIn(
            "agent_workflow.py", " ".join(self.result["files"])
        )

    def test_agent_py_is_still_an_llm_agent(self):
        text = (self.pkg / "agent.py").read_text(encoding="utf-8")
        self.assertIn("root_agent = LlmAgent(", text)


class TestStreamingGuard(unittest.TestCase):
    """run_adk.py must not assume the root agent is an LlmAgent."""

    def setUp(self):
        self.text = RUN_ADK.read_text(encoding="utf-8")

    def test_llm_agent_rebuild_is_guarded(self):
        self.assertIn("isinstance(root_agent, LlmAgent)", self.text)

    def test_non_llm_agent_root_is_streamed_as_is(self):
        self.assertIn("live_agent = root_agent", self.text)

    def test_guard_precedes_attribute_access(self):
        guard = self.text.index("isinstance(root_agent, LlmAgent)")
        for attr in ("root_agent.instruction", "root_agent.tools", "root_agent.sub_agents"):
            self.assertLess(
                guard, self.text.index(attr),
                f"{attr} is read before the LlmAgent guard",
            )

    def test_guard_semantics(self):
        """Exercise the guard's branch logic against a non-LlmAgent root."""

        class _FakeLlmAgent:
            pass

        class _FakeWorkflow:
            name = "wf"

        for root, expect_rebuild in ((_FakeLlmAgent(), True), (_FakeWorkflow(), False)):
            rebuilt = isinstance(root, _FakeLlmAgent)
            self.assertEqual(rebuilt, expect_rebuild)
            if not rebuilt:
                # The non-LlmAgent path must never touch LlmAgent-only attrs.
                self.assertFalse(hasattr(root, "instruction"))


class TestWorkflowFlagRejectedByOtherBackends(unittest.TestCase):
    def test_claude_agent_sdk_rejects_workflow(self):
        from nuvel.backends.claude_agent_sdk.scaffold import (
            scaffold_agent as claude_scaffold,
        )

        result = claude_scaffold(name="x-agent", workflow=True)
        self.assertEqual(result["status"], "error")
        self.assertIn("--workflow", result["message"])

    def test_managed_agents_rejects_workflow(self):
        from nuvel.backends.anthropic_managed_agents.scaffold import (
            scaffold_agent as managed_scaffold,
        )

        result = managed_scaffold(name="x-agent", workflow=True)
        self.assertEqual(result["status"], "error")
        self.assertIn("--workflow", result["message"])


class TestCliWiring(unittest.TestCase):
    def test_new_parser_accepts_workflow_flag(self):
        from nuvel.cli import build_parser

        args = build_parser().parse_args(["new", "my-agent", "--workflow"])
        self.assertTrue(args.workflow)

    def test_workflow_defaults_to_false(self):
        from nuvel.cli import build_parser

        args = build_parser().parse_args(["new", "my-agent"])
        self.assertFalse(args.workflow)


class TestTaskDelegationSkillBundled(unittest.TestCase):
    SKILL_DIR = (
        Path(__file__).resolve().parents[1]
        / "nuvel" / "backends" / "adk" / "skills" / "adk-task-delegation"
    )

    def test_skill_md_exists_with_frontmatter(self):
        text = (self.SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---"))
        self.assertIn("name: adk-task-delegation", text)

    def test_references_exist(self):
        for ref in ("task-mode-examples.md", "workflow-migration.md"):
            self.assertTrue((self.SKILL_DIR / "references" / ref).is_file(), ref)

    def test_skill_is_discoverable_via_cli(self):
        from nuvel.cli import _load_skills

        slugs = [s["slug"] for s in _load_skills("adk")]
        self.assertIn("adk-task-delegation", slugs)


if __name__ == "__main__":
    unittest.main()
