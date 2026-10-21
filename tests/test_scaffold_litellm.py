"""Tests for the --with-litellm overlay's master-key handling.

The LiteLLM proxy's master key is its admin credential. It used to be
templated as a static, guessable value derived from the agent name. Now the
committed config reads it from the environment, and the scaffolder generates a
random key per project into the gitignored ``<pkg>/litellm/.env`` only.
"""

import re
import shutil
import tempfile
import unittest
from pathlib import Path

from nuvel.backends.adk.scaffold import scaffold_agent

_KEY_LINE = re.compile(r"^LITELLM_MASTER_KEY=(.*)$", re.MULTILINE)


def _master_key(env_text: str) -> str:
    match = _KEY_LINE.search(env_text)
    assert match, "LITELLM_MASTER_KEY line missing"
    return match.group(1).strip()


class TestLitellmMasterKey(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _scaffold(self, name: str) -> Path:
        result = scaffold_agent(name, output_dir=self.tmpdir, with_litellm=True)
        self.assertEqual(result["status"], "ok", result)
        self.assertTrue(result["with_litellm"])
        return Path(result["path"]) / name.replace("-", "_") / "litellm"

    def test_each_scaffold_gets_a_different_random_key(self):
        key_a = _master_key((self._scaffold("agent-a") / ".env").read_text())
        key_b = _master_key((self._scaffold("agent-b") / ".env").read_text())
        self.assertTrue(key_a.startswith("sk-"))
        self.assertGreaterEqual(len(key_a), 40)
        self.assertNotEqual(key_a, key_b)

    def test_config_reads_master_key_from_env(self):
        litellm = self._scaffold("agent-c")
        key = _master_key((litellm / ".env").read_text())
        config = (litellm / "config.yaml").read_text()
        self.assertIn("master_key: os.environ/LITELLM_MASTER_KEY", config)
        self.assertNotIn(key, config)
        self.assertNotIn("sk-litellm-master", config)

    def test_key_lands_only_in_the_gitignored_env(self):
        litellm = self._scaffold("agent-d")
        key = _master_key((litellm / ".env").read_text())
        agent_root = litellm.parent.parent
        leaks = [
            str(p.relative_to(agent_root))
            for p in agent_root.rglob("*")
            if p.is_file() and p != litellm / ".env" and key in p.read_text(errors="ignore")
        ]
        self.assertEqual(leaks, [])
        self.assertIn(".env", (litellm / ".gitignore").read_text().split())

    def test_env_example_has_only_a_placeholder(self):
        litellm = self._scaffold("agent-e")
        example = (litellm / ".env.example").read_text()
        self.assertEqual(_master_key(example), "")
        key_shaped = re.compile(r"sk-[A-Za-z0-9_-]{8,}")
        self.assertIsNone(key_shaped.search(example))
        root_example = (litellm.parent.parent / ".env.example").read_text()
        self.assertIsNone(key_shaped.search(root_example))
        self.assertRegex(root_example, r"(?m)^LITELLM_API_KEY=$")

    def test_proxy_port_binds_to_loopback(self):
        compose = (self._scaffold("agent-f") / "docker-compose.yaml").read_text()
        self.assertIn('"127.0.0.1:4000:4000"', compose)
        self.assertNotIn('- "4000:4000"', compose)

    def test_agent_image_excludes_nested_env_files(self):
        litellm = self._scaffold("agent-g")
        dockerignore = (litellm.parent.parent / ".dockerignore").read_text().split()
        self.assertIn("**/.env", dockerignore)

    def test_without_flag_no_env_is_written(self):
        result = scaffold_agent("agent-h", output_dir=self.tmpdir)
        self.assertEqual(result["status"], "ok")
        self.assertFalse((Path(result["path"]) / "agent_h" / "litellm").exists())
        self.assertFalse(any(Path(result["path"]).rglob(".env")))


if __name__ == "__main__":
    unittest.main()
