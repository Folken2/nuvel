"""Tests for nuvel.scaffold — the stamping module."""

import os
import shutil
import tempfile
import unittest
from pathlib import Path

from nuvel.scaffold import validate_agent_name, scaffold_agent


class TestValidateAgentName(unittest.TestCase):
    """Test agent name validation rules."""

    def test_valid_basic_names(self):
        self.assertEqual(validate_agent_name("my-agent"), "my-agent")
        self.assertEqual(validate_agent_name("agent"), "agent")

    def test_valid_with_digits(self):
        self.assertEqual(validate_agent_name("k8s-monitor"), "k8s-monitor")
        self.assertEqual(validate_agent_name("agent123"), "agent123")

    def test_valid_single_letter(self):
        self.assertEqual(validate_agent_name("a"), "a")

    def test_invalid_starts_with_digit(self):
        with self.assertRaises(ValueError):
            validate_agent_name("1agent")

    def test_invalid_ends_with_hyphen(self):
        with self.assertRaises(ValueError):
            validate_agent_name("agent-")

    def test_invalid_uppercase(self):
        with self.assertRaises(ValueError):
            validate_agent_name("My-Agent")

    def test_invalid_too_long(self):
        with self.assertRaises(ValueError):
            validate_agent_name("a" * 41)

    def test_invalid_empty(self):
        with self.assertRaises(ValueError):
            validate_agent_name("")

    def test_invalid_special_chars(self):
        with self.assertRaises(ValueError):
            validate_agent_name("my_agent")  # underscores not allowed
        with self.assertRaises(ValueError):
            validate_agent_name("my agent")  # spaces not allowed

    def test_invalid_double_hyphen(self):
        with self.assertRaises(ValueError):
            validate_agent_name("my--agent")


class TestScaffoldAgent(unittest.TestCase):
    """Test the scaffold_agent function."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_creates_agent_directory(self):
        result = scaffold_agent("my-agent", output_dir=self.tmpdir)
        self.assertEqual(result["status"], "ok")
        agent_dir = Path(result["path"])
        self.assertTrue(agent_dir.exists())
        self.assertTrue(agent_dir.is_dir())

    def test_renames_package_directory(self):
        result = scaffold_agent("my-agent", output_dir=self.tmpdir)
        agent_dir = Path(result["path"])
        # The {{agent_package}} dir should be renamed to my_agent
        pkg_dir = agent_dir / "my_agent"
        self.assertTrue(pkg_dir.exists(), f"Expected {pkg_dir} to exist")
        # The template placeholder dir should NOT exist
        self.assertFalse((agent_dir / "{{agent_package}}").exists())

    def test_replaces_placeholders_in_file_contents(self):
        result = scaffold_agent(
            "my-agent",
            output_dir=self.tmpdir,
            description="Test agent",
            system_prompt="Be helpful.",
        )
        agent_dir = Path(result["path"])

        # Check agent.py for placeholder replacement
        agent_py = agent_dir / "my_agent" / "agent.py"
        self.assertTrue(agent_py.exists(), f"Expected {agent_py} to exist")
        content = agent_py.read_text()
        self.assertNotIn("{{agent_name}}", content)
        self.assertNotIn("{{agent_package}}", content)
        self.assertNotIn("{{agent_description}}", content)
        self.assertIn("Test agent", content)
        self.assertIn("my_agent", content)

        # Check instructions.py for system prompt
        instructions_py = agent_dir / "my_agent" / "prompt" / "instructions.py"
        self.assertTrue(instructions_py.exists())
        content = instructions_py.read_text()
        self.assertIn("Be helpful.", content)
        self.assertNotIn("{{agent_system_prompt}}", content)

    def test_run_adk_has_correct_imports(self):
        result = scaffold_agent("my-agent", output_dir=self.tmpdir)
        agent_dir = Path(result["path"])
        run_adk = agent_dir / "run_adk.py"
        self.assertTrue(run_adk.exists())
        content = run_adk.read_text()
        self.assertIn("from my_agent.plugins import PLUGIN_PATHS", content)
        self.assertIn("from my_agent.config.logging import", content)
        self.assertNotIn("{{agent_package}}", content)

    def test_creates_all_required_files(self):
        result = scaffold_agent("my-agent", output_dir=self.tmpdir)
        agent_dir = Path(result["path"])
        required = [
            "run_adk.py",
            "requirements.txt",
            ".env.example",
            "README.md",
            "my_agent/__init__.py",
            "my_agent/agent.py",
            "my_agent/tools/__init__.py",
            "my_agent/plugins/__init__.py",
            "my_agent/prompt/instructions.py",
            "my_agent/config/llm.py",
            "my_agent/config/logging.py",
        ]
        for rel in required:
            fpath = agent_dir / rel
            self.assertTrue(fpath.exists(), f"Missing required file: {rel}")

    def test_gitkeep_files_skipped(self):
        result = scaffold_agent("my-agent", output_dir=self.tmpdir)
        agent_dir = Path(result["path"])
        # .gitkeep files should NOT be copied
        for root, dirs, files in os.walk(agent_dir):
            for fname in files:
                self.assertNotEqual(fname, ".gitkeep", f".gitkeep found in {root}")

    def test_duplicate_name_returns_error(self):
        result1 = scaffold_agent("my-agent", output_dir=self.tmpdir)
        self.assertEqual(result1["status"], "ok")
        result2 = scaffold_agent("my-agent", output_dir=self.tmpdir)
        self.assertEqual(result2["status"], "error")

    def test_result_dict_fields(self):
        result = scaffold_agent("my-agent", output_dir=self.tmpdir)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["agent_name"], "my-agent")
        self.assertEqual(result["package_name"], "my_agent")
        self.assertIsInstance(result["files_created"], int)
        self.assertGreater(result["files_created"], 0)
        self.assertIsInstance(result["files"], list)
        self.assertEqual(len(result["files"]), result["files_created"])

    def test_default_description(self):
        result = scaffold_agent("my-agent", output_dir=self.tmpdir)
        agent_dir = Path(result["path"])
        agent_py = agent_dir / "my_agent" / "agent.py"
        content = agent_py.read_text()
        self.assertIn("ADK agent: my-agent", content)

    def test_default_system_prompt(self):
        result = scaffold_agent("my-agent", output_dir=self.tmpdir)
        agent_dir = Path(result["path"])
        instructions = agent_dir / "my_agent" / "prompt" / "instructions.py"
        content = instructions.read_text()
        self.assertIn("You are a helpful AI assistant.", content)

    def test_plugins_init_has_correct_paths(self):
        result = scaffold_agent("my-agent", output_dir=self.tmpdir)
        agent_dir = Path(result["path"])
        plugins_init = agent_dir / "my_agent" / "plugins" / "__init__.py"
        content = plugins_init.read_text()
        self.assertIn("my_agent.plugins.trace", content)
        self.assertNotIn("{{agent_package}}", content)

    def test_tmpl_suffix_stripped(self):
        """Ensure .tmpl suffixed files have that suffix removed."""
        result = scaffold_agent("my-agent", output_dir=self.tmpdir)
        agent_dir = Path(result["path"])
        # agent.py.tmpl should become agent.py
        self.assertTrue((agent_dir / "my_agent" / "agent.py").exists())
        self.assertFalse((agent_dir / "my_agent" / "agent.py.tmpl").exists())

    def test_env_var_output_dir(self):
        """Test that AGENTS_OUTPUT_DIR env var is used when output_dir is None."""
        env_dir = tempfile.mkdtemp()
        try:
            os.environ["AGENTS_OUTPUT_DIR"] = env_dir
            result = scaffold_agent("my-agent")
            self.assertEqual(result["status"], "ok")
            self.assertTrue(Path(result["path"]).exists())
            self.assertTrue(result["path"].startswith(env_dir))
        finally:
            os.environ.pop("AGENTS_OUTPUT_DIR", None)
            shutil.rmtree(env_dir, ignore_errors=True)

    def test_readme_placeholders_replaced(self):
        result = scaffold_agent("my-agent", output_dir=self.tmpdir)
        agent_dir = Path(result["path"])
        readme = agent_dir / "README.md"
        content = readme.read_text()
        self.assertIn("# my-agent", content)
        self.assertIn("my_agent/", content)
        self.assertNotIn("{{agent_name}}", content)
        self.assertNotIn("{{agent_package}}", content)


if __name__ == "__main__":
    unittest.main()
