"""End-to-end test: scaffold + validate an agent."""

import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

# Ensure project root is importable
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scaffold import scaffold_agent
from meta_agent.tools.validate_tool import _validate_agent_impl


class TestEndToEnd:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()

    def teardown_method(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_scaffold_and_validate(self):
        """Scaffold an agent and verify it passes validation."""
        result = scaffold_agent(
            "hello-world-agent",
            output_dir=self.tmpdir,
            description="A simple greeting agent",
        )
        assert result["status"] == "ok"
        assert result["files_created"] > 10

        agent_dir = os.path.join(self.tmpdir, "hello-world-agent")
        validation = _validate_agent_impl(agent_dir)
        assert validation["status"] == "ok", f"Validation errors: {validation['errors']}"
        assert validation["package"] == "hello_world_agent"
        assert validation["errors"] == []

    def test_generated_files_have_correct_imports(self):
        """Check that generated Python files have correct package imports."""
        scaffold_agent("my-api-agent", output_dir=self.tmpdir)
        agent_dir = os.path.join(self.tmpdir, "my-api-agent")

        # Check agent.py has correct imports
        agent_py = open(os.path.join(agent_dir, "my_api_agent", "agent.py")).read()
        assert "from .config.llm import FAST_MODEL" in agent_py
        assert "from .tools import get_tools" in agent_py
        assert "from .prompt.instructions import get_agent_instruction" in agent_py

        # Check plugins/__init__.py has correct paths
        plugins_init = open(os.path.join(agent_dir, "my_api_agent", "plugins", "__init__.py")).read()
        assert "my_api_agent.plugins.trace" in plugins_init
        assert "{{agent_package}}" not in plugins_init

        # Check run_adk.py has correct imports
        run_adk = open(os.path.join(agent_dir, "run_adk.py")).read()
        assert "my_api_agent.plugins" in run_adk
        assert "my_api_agent.config.logging" in run_adk

    def test_no_placeholders_in_any_file(self):
        """Verify no {{placeholder}} remnants in any generated file."""
        scaffold_agent("clean-agent", output_dir=self.tmpdir)
        agent_dir = os.path.join(self.tmpdir, "clean-agent")

        for root, dirs, files in os.walk(agent_dir):
            for f in files:
                if f.endswith((".py", ".md", ".txt", ".example")):
                    path = os.path.join(root, f)
                    content = open(path, encoding="utf-8").read()
                    assert "{{" not in content, (
                        f"Placeholder found in {os.path.relpath(path, agent_dir)}"
                    )

    def test_multiple_agents_independent(self):
        """Scaffold two agents and verify they don't interfere."""
        r1 = scaffold_agent("agent-one", output_dir=self.tmpdir)
        r2 = scaffold_agent("agent-two", output_dir=self.tmpdir)
        assert r1["status"] == "ok"
        assert r2["status"] == "ok"

        # Each has its own package
        assert os.path.isdir(os.path.join(self.tmpdir, "agent-one", "agent_one"))
        assert os.path.isdir(os.path.join(self.tmpdir, "agent-two", "agent_two"))

        # Both validate
        v1 = _validate_agent_impl(os.path.join(self.tmpdir, "agent-one"))
        v2 = _validate_agent_impl(os.path.join(self.tmpdir, "agent-two"))
        assert v1["status"] == "ok"
        assert v2["status"] == "ok"
