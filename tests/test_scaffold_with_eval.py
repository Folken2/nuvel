"""Tests for --with-eval scaffolding — the evalv2 starter suite overlay."""

import shutil
import tempfile
import unittest
from pathlib import Path

import yaml

from nuvel.backends.adk.scaffold import scaffold_agent


class _ScaffoldCase(unittest.TestCase):
    """Scaffolds once per class into a temp dir."""

    with_eval = False
    agent_name = "eval-agent"

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.result = scaffold_agent(
            name=cls.agent_name,
            output_dir=cls.tmp,
            description="An agent with evals.",
            with_eval=cls.with_eval,
        )
        cls.root = Path(cls.tmp) / cls.agent_name
        cls.pkg = cls.root / cls.agent_name.replace("-", "_")
        cls.eval_dir = cls.pkg / "skills" / "default" / "eval"

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)


class TestWithEvalCreatesSuite(_ScaffoldCase):
    with_eval = True

    def test_scaffold_succeeds(self):
        self.assertEqual(self.result["status"], "ok")
        self.assertTrue(self.result["with_eval"])

    def test_suite_yaml_created(self):
        self.assertTrue((self.eval_dir / "suite.yaml").is_file())
        joined = " ".join(self.result["files"])
        self.assertIn("suite.yaml", joined)
        self.assertIn("eval", joined)

    def test_example_created(self):
        self.assertTrue((self.eval_dir / "examples" / "welcome.json").is_file())

    def test_suite_is_valid_yaml_with_required_keys(self):
        data = yaml.safe_load((self.eval_dir / "suite.yaml").read_text(encoding="utf-8"))
        self.assertIsInstance(data, dict)
        for key in ("name", "evaluators", "thresholds"):
            self.assertIn(key, data)

    def test_placeholders_substituted(self):
        text = (self.eval_dir / "suite.yaml").read_text(encoding="utf-8")
        self.assertNotIn("{{", text)
        self.assertIn("eval-agent", text)

    def test_example_placeholders_substituted(self):
        text = (self.eval_dir / "examples" / "welcome.json").read_text(encoding="utf-8")
        self.assertNotIn("{{", text)
        self.assertIn("eval-agent", text)

    def test_suite_loads_via_evalsuite(self):
        from nuvel.evalv2.suite import EvalSuite

        suite = EvalSuite.load(self.eval_dir)
        self.assertEqual(suite.name, "eval-agent-eval")
        self.assertEqual(len(suite.examples), 1)


class TestWithoutEvalOmitsSuite(_ScaffoldCase):
    with_eval = False

    def test_scaffold_succeeds(self):
        self.assertEqual(self.result["status"], "ok")
        self.assertFalse(self.result["with_eval"])

    def test_no_eval_dir(self):
        self.assertFalse((self.pkg / "skills" / "default" / "eval").exists())


class TestWithEvalRejectedByOtherBackends(unittest.TestCase):
    def test_claude_agent_sdk_rejects(self):
        from nuvel.backends.claude_agent_sdk.scaffold import scaffold_agent as sdk_scaffold

        result = sdk_scaffold(name="x-agent", with_eval=True)
        self.assertEqual(result["status"], "error")
        self.assertIn("with-eval", result["message"])

    def test_anthropic_managed_rejects(self):
        from nuvel.backends.anthropic_managed_agents.scaffold import scaffold_agent as ma_scaffold

        result = ma_scaffold(name="x-agent", with_eval=True)
        self.assertEqual(result["status"], "error")
        self.assertIn("with-eval", result["message"])


if __name__ == "__main__":
    unittest.main()
