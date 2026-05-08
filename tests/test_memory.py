"""Tests for the markdown file-based long-term memory system."""

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

# Ensure project root is importable
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# We test the template file directly by importing from the template path.
# Since the template uses relative imports (..state.memory), we need to
# set up a proper package structure. Instead, we copy the memory module
# to a temp location and test it standalone.

# For testing, we import the template source and patch MEMORY_DIR.
TEMPLATE_STATE_DIR = ROOT / "nuvel" / "backends" / "adk" / "templates" / "{{agent_package}}" / "state"


def _run_module_code(code, namespace):
    """Load compiled module code into namespace. Used for template testing only."""
    exec(code, namespace)  # noqa: S102


def _load_memory_module(memory_dir: str):
    """Load the memory module with a custom MEMORY_DIR.

    Sets the MEMORY_DIR env var before loading so the module's lazy config
    accessors pick up the test directory automatically.
    """
    os.environ["MEMORY_DIR"] = memory_dir
    source = (TEMPLATE_STATE_DIR / "memory.py").read_text(encoding="utf-8")

    module_globals = {"__name__": "memory", "__file__": str(TEMPLATE_STATE_DIR / "memory.py")}
    code = compile(source, str(TEMPLATE_STATE_DIR / "memory.py"), "exec")
    _run_module_code(code, module_globals)

    return module_globals


class TestMemoryModule(unittest.TestCase):
    """Test the state/memory.py module."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.mem = _load_memory_module(self.tmpdir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # ── Helpers ────────────────────────────────────────────────────

    def _call(self, func_name, *args, **kwargs):
        """Call a function from the loaded memory module."""
        return self.mem[func_name](*args, **kwargs)

    # ── Core Memory Tests ─────────────────────────────────────────

    def test_load_core_memory_empty(self):
        """Core memory returns empty string when no file exists."""
        result = self._call("load_core_memory")
        self.assertEqual(result, "")

    def test_save_and_load_core_memory(self):
        """Save then load roundtrips correctly."""
        result = self._call("save_core_memory", "# Test Memory\n\nHello world")
        self.assertEqual(result["status"], "ok")

        content = self._call("load_core_memory")
        self.assertIn("Hello world", content)

    def test_append_core_memory(self):
        """Append adds timestamped entries."""
        self._call("append_core_memory", "First fact")
        self._call("append_core_memory", "Second fact")

        content = self._call("load_core_memory")
        self.assertIn("First fact", content)
        self.assertIn("Second fact", content)
        # Should have timestamps
        self.assertIn("[", content)
        self.assertIn("UTC]", content)

    def test_core_memory_size_limit(self):
        """Save rejects content exceeding max size."""
        with mock.patch.dict(os.environ, {"MEMORY_MAX_CORE_SIZE": "50"}):
            result = self._call("save_core_memory", "x" * 100)
            self.assertEqual(result["status"], "error")
            self.assertIn("exceeds", result["message"])

    def test_append_core_memory_size_limit(self):
        """Append rejects when total would exceed max size."""
        with mock.patch.dict(os.environ, {"MEMORY_MAX_CORE_SIZE": "100"}):
            # First entry fits
            result = self._call("append_core_memory", "Short fact")
            self.assertEqual(result["status"], "ok")

            # Large entry would exceed limit
            result = self._call("append_core_memory", "x" * 200)
            self.assertEqual(result["status"], "error")

    # ── Topic Memory Tests ────────────────────────────────────────

    def test_list_topics_empty(self):
        """No topics when directory is empty."""
        topics = self._call("list_topics")
        self.assertEqual(topics, [])

    def test_save_and_load_topic(self):
        """Save and load a topic file."""
        result = self._call("save_topic", "test-topic", "# Test Topic\n\nDetails here")
        self.assertEqual(result["status"], "ok")

        content = self._call("load_topic", "test-topic")
        self.assertIn("Details here", content)

    def test_append_topic_creates_header(self):
        """Append to new topic creates it with a header."""
        result = self._call("append_topic", "user preferences", "Likes dark mode")
        self.assertEqual(result["status"], "ok")

        content = self._call("load_topic", "user-preferences")
        self.assertIn("# User Preferences", content)
        self.assertIn("Likes dark mode", content)

    def test_append_topic_existing(self):
        """Append to existing topic adds entries."""
        self._call("append_topic", "project", "Uses Python 3.12")
        self._call("append_topic", "project", "FastAPI backend")

        content = self._call("load_topic", "project")
        self.assertIn("Python 3.12", content)
        self.assertIn("FastAPI", content)

    def test_list_topics(self):
        """List returns all topic names."""
        self._call("save_topic", "alpha", "content a")
        self._call("save_topic", "beta", "content b")

        topics = self._call("list_topics")
        self.assertEqual(topics, ["alpha", "beta"])

    def test_delete_topic(self):
        """Delete removes a topic file."""
        self._call("save_topic", "to-delete", "temp content")
        self.assertIn("to-delete", self._call("list_topics"))

        result = self._call("delete_topic", "to-delete")
        self.assertEqual(result["status"], "ok")
        self.assertNotIn("to-delete", self._call("list_topics"))

    def test_delete_nonexistent_topic(self):
        """Delete returns error for missing topic."""
        result = self._call("delete_topic", "nope")
        self.assertEqual(result["status"], "error")

    def test_load_nonexistent_topic(self):
        """Load returns empty string for missing topic."""
        content = self._call("load_topic", "nope")
        self.assertEqual(content, "")

    def test_topic_size_limit(self):
        """Save rejects topic content exceeding max size."""
        with mock.patch.dict(os.environ, {"MEMORY_MAX_TOPIC_SIZE": "50"}):
            result = self._call("save_topic", "big", "x" * 100)
            self.assertEqual(result["status"], "error")

    # ── Slugify Tests ─────────────────────────────────────────────

    def test_slugify_basic(self):
        """Slugify handles common cases."""
        slugify = self.mem["_slugify"]
        self.assertEqual(slugify("User Preferences"), "user-preferences")
        self.assertEqual(slugify("my_topic"), "my-topic")
        self.assertEqual(slugify("  spaces  "), "spaces")
        self.assertEqual(slugify("Special!@#Chars"), "specialchars")

    def test_slugify_truncation(self):
        """Slugify truncates long names."""
        slugify = self.mem["_slugify"]
        result = slugify("a" * 100)
        self.assertLessEqual(len(result), 60)

    def test_slugify_empty_and_special_only(self):
        """Slugify returns empty string for empty or all-special-char input."""
        slugify = self.mem["_slugify"]
        self.assertEqual(slugify(""), "")
        self.assertEqual(slugify("..."), "")
        self.assertEqual(slugify("!!!@#$"), "")

    def test_load_topic_empty_slug_returns_empty(self):
        """Load topic with invalid name returns empty string."""
        content = self._call("load_topic", "!!!")
        self.assertEqual(content, "")

    def test_delete_topic_empty_slug_returns_error(self):
        """Delete topic with invalid name returns error."""
        result = self._call("delete_topic", "!!!")
        self.assertEqual(result["status"], "error")
        self.assertIn("Invalid", result["message"])

    # ── Aggregate Loading Tests ───────────────────────────────────

    def test_load_all_memory_empty(self):
        """Load all returns empty when nothing saved."""
        result = self._call("load_all_memory")
        self.assertEqual(result, "")

    def test_load_all_memory_combined(self):
        """Load all combines core + topics."""
        self._call("save_core_memory", "# Core\n\nCore facts here")
        self._call("save_topic", "project", "# Project\n\nProject details")

        result = self._call("load_all_memory")
        self.assertIn("Core facts here", result)
        self.assertIn("Project details", result)
        self.assertIn("---", result)  # separator

    def test_load_all_memory_core_only(self):
        """Load all with only core memory."""
        self._call("save_core_memory", "Just core")

        result = self._call("load_all_memory")
        self.assertIn("Just core", result)
        self.assertNotIn("---", result)  # no separator for single section

    # ── Stats Tests ───────────────────────────────────────────────

    def test_memory_stats_empty(self):
        """Stats on empty memory."""
        stats = self._call("memory_stats")
        self.assertEqual(stats["core_memory_size"], 0)
        self.assertEqual(stats["topic_count"], 0)

    def test_memory_stats_populated(self):
        """Stats reflect saved content."""
        self._call("save_core_memory", "Some core content")
        self._call("save_topic", "alpha", "Topic A")
        self._call("save_topic", "beta", "Topic B")

        stats = self._call("memory_stats")
        self.assertGreater(stats["core_memory_size"], 0)
        self.assertEqual(stats["topic_count"], 2)
        self.assertIn("alpha", stats["topics"])
        self.assertIn("beta", stats["topics"])

    # ── Directory Creation Tests ──────────────────────────────────

    def test_ensure_memory_dir_creates_structure(self):
        """Memory dir and topics subdir are created on first write."""
        nested_dir = Path(self.tmpdir) / "deep" / "memory"
        with mock.patch.dict(os.environ, {"MEMORY_DIR": str(nested_dir)}):
            self._call("save_core_memory", "test")
            self.assertTrue(nested_dir.is_dir())
            self.assertTrue((nested_dir / "topics").is_dir())


class TestScaffoldIncludesMemory(unittest.TestCase):
    """Test that scaffold_agent produces memory files and wiring."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_scaffold_includes_memory_files(self):
        """Scaffolded agent has memory state, tools, and plugin."""
        from nuvel.backends.adk.scaffold import scaffold_agent

        result = scaffold_agent("test-mem", output_dir=self.tmpdir)
        self.assertEqual(result["status"], "ok")
        agent_dir = Path(result["path"])

        # Memory state module
        self.assertTrue(
            (agent_dir / "test_mem" / "state" / "memory.py").exists(),
            "state/memory.py should exist",
        )

        # Memory tools
        self.assertTrue(
            (agent_dir / "test_mem" / "tools" / "memory_tools.py").exists(),
            "tools/memory_tools.py should exist",
        )

        # Memory plugin
        self.assertTrue(
            (agent_dir / "test_mem" / "plugins" / "memory_plugin.py").exists(),
            "plugins/memory_plugin.py should exist",
        )

        # Memory directory with default file
        self.assertTrue(
            (agent_dir / "memory" / "AGENT_MEMORY.md").exists(),
            "memory/AGENT_MEMORY.md should exist",
        )

    def test_scaffold_tools_init_imports_memory(self):
        """Tools __init__.py imports memory tools."""
        from nuvel.backends.adk.scaffold import scaffold_agent

        result = scaffold_agent("test-mem2", output_dir=self.tmpdir)
        agent_dir = Path(result["path"])

        tools_init = (agent_dir / "test_mem2" / "tools" / "__init__.py").read_text()
        self.assertIn("memory_tool_list", tools_init)
        self.assertIn("from .memory_tools import", tools_init)

    def test_scaffold_plugins_init_includes_memory(self):
        """Plugins __init__.py registers memory plugin."""
        from nuvel.backends.adk.scaffold import scaffold_agent

        result = scaffold_agent("test-mem3", output_dir=self.tmpdir)
        agent_dir = Path(result["path"])

        plugins_init = (agent_dir / "test_mem3" / "plugins" / "__init__.py").read_text()
        self.assertIn("MemoryPlugin", plugins_init)
        self.assertIn("test_mem3.plugins.memory", plugins_init)

    def test_scaffold_instructions_loads_memory(self):
        """Instructions template loads memory into prompt."""
        from nuvel.backends.adk.scaffold import scaffold_agent

        result = scaffold_agent("test-mem4", output_dir=self.tmpdir)
        agent_dir = Path(result["path"])

        instructions = (agent_dir / "test_mem4" / "prompt" / "instructions.py").read_text()
        self.assertIn("load_all_memory", instructions)
        self.assertIn("Long-Term Memory", instructions)

    def test_scaffold_env_example_has_memory_config(self):
        """Env example includes memory configuration."""
        from nuvel.backends.adk.scaffold import scaffold_agent

        result = scaffold_agent("test-mem5", output_dir=self.tmpdir)
        agent_dir = Path(result["path"])

        env = (agent_dir / ".env.example").read_text()
        self.assertIn("MEMORY_DIR", env)
        self.assertIn("MEMORY_ENABLED", env)


if __name__ == "__main__":
    unittest.main()
