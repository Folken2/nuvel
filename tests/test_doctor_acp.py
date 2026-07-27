"""Tests for `nuvel doctor`'s ACP adapter checks.

Covers ACP package detection, the Zed ``agent_servers`` snippet, and the
stdio ``initialize`` handshake smoke-test. The handshake test drives the real
subprocess logic against tiny fake "ACP agents" (plain stdlib scripts) so it
runs without google-adk installed.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from nuvel.doctor import (
    FAIL,
    OK,
    WARN,
    check_acp_handshake,
    detect_acp_package,
    zed_config_snippet,
)

# A fake ACP entrypoint that answers `initialize` like the real adapter.
_FAKE_OK = """\
import sys, json
sys.stdin.readline()
sys.stdout.write(json.dumps({
    "jsonrpc": "2.0", "id": 1,
    "result": {"protocolVersion": 1, "agentCapabilities": {}},
}) + "\\n")
sys.stdout.flush()
"""

# Answers, but without the expected initialize fields.
_FAKE_BAD = """\
import sys, json
sys.stdin.readline()
sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}}) + "\\n")
sys.stdout.flush()
"""

# Dies on import, like an agent whose deps aren't installed.
_FAKE_MISSING_DEP = "import totally_missing_module_xyz  # noqa\n"


def _make_fake_agent(root: Path, pkg: str, main_body: str) -> None:
    acp_dir = root / pkg / "acp"
    acp_dir.mkdir(parents=True)
    (root / pkg / "__init__.py").write_text("")
    (acp_dir / "__init__.py").write_text("")
    (acp_dir / "__main__.py").write_text(main_body)


class TestAcpDetection(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_detects_acp_package(self):
        _make_fake_agent(self.tmp, "agent_x", _FAKE_OK)
        self.assertEqual(detect_acp_package(self.tmp), "agent_x")

    def test_no_acp_package(self):
        (self.tmp / "agent_x").mkdir()
        (self.tmp / "agent_x" / "agent.py").write_text("")
        self.assertIsNone(detect_acp_package(self.tmp))


class TestZedSnippet(unittest.TestCase):
    def test_snippet_shape(self):
        snippet = zed_config_snippet(Path("/proj/my-agent"), "my_agent", python="/usr/bin/python3")
        data = json.loads(snippet)
        entry = data["agent_servers"]["my-agent"]  # hyphenated display name
        self.assertEqual(entry["command"], "/usr/bin/python3")
        self.assertEqual(entry["args"], ["-m", "my_agent.acp"])
        self.assertEqual(entry["cwd"], "/proj/my-agent")


class TestAcpHandshake(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_valid_handshake_is_ok(self):
        _make_fake_agent(self.tmp, "fakeok", _FAKE_OK)
        check = check_acp_handshake(self.tmp, "fakeok", timeout=15.0)
        self.assertEqual(check.status, OK, check.detail)
        self.assertIn("protocolVersion", check.detail)

    def test_unexpected_response_fails(self):
        _make_fake_agent(self.tmp, "fakebad", _FAKE_BAD)
        check = check_acp_handshake(self.tmp, "fakebad", timeout=15.0)
        self.assertEqual(check.status, FAIL, check.detail)

    def test_missing_deps_warns(self):
        _make_fake_agent(self.tmp, "fakedep", _FAKE_MISSING_DEP)
        check = check_acp_handshake(self.tmp, "fakedep", timeout=15.0)
        self.assertEqual(check.status, WARN, check.detail)


if __name__ == "__main__":
    unittest.main()
