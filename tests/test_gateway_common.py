"""Tests for the shared gateway _common module.

The module under test lives inside a *generated* agent — so each test
scaffolds a tiny agent in a tmpdir, then imports its `_common` module.
"""

import importlib.util
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from nuvel.backends.adk.scaffold import scaffold_agent


def _scaffold_with(tmpdir, **flags):
    """Scaffold an agent with the given flags and return its package dir."""
    result = scaffold_agent("agent-test", output_dir=tmpdir, **flags)
    if result["status"] != "ok":
        raise AssertionError(result.get("message"))
    return Path(result["path"]) / "agent_test"


def _import_module(pkg_dir: Path, dotted: str):
    """Dynamically import `dotted` from a generated agent package."""
    file_path = pkg_dir / Path(*dotted.split(".")).with_suffix(".py")
    spec = importlib.util.spec_from_file_location(f"_gw_{dotted.replace('.', '_')}", file_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


class TestSessionKey(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp()
        # Scaffold once, reuse for all session_key tests.
        cls.pkg = _scaffold_with(cls.tmpdir, with_telegram=True)
        cls.common = _import_module(cls.pkg, "gateways._common")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def test_slack_dm(self):
        payload = {"team_id": "T01", "channel": "D456", "user": "U012", "channel_type": "im"}
        user_id, session_id = self.common.session_key("slack", payload)
        self.assertEqual(user_id, "slack:T01:U012")
        self.assertEqual(session_id, "slack:dm:T01:D456")

    def test_slack_channel_with_thread(self):
        payload = {"team_id": "T01", "channel": "C123", "user": "U012",
                   "ts": "1700000000.001", "thread_ts": "1699999999.500"}
        user_id, session_id = self.common.session_key("slack", payload)
        self.assertEqual(user_id, "slack:T01:U012")
        self.assertEqual(session_id, "slack:thread:T01:C123:1699999999.500")

    def test_slack_channel_without_thread_uses_ts(self):
        payload = {"team_id": "T01", "channel": "C123", "user": "U012", "ts": "1700000000.001"}
        _, session_id = self.common.session_key("slack", payload)
        self.assertEqual(session_id, "slack:thread:T01:C123:1700000000.001")

    def test_telegram_private_chat(self):
        payload = {"chat": {"id": 999, "type": "private"}, "from": {"id": 555}}
        user_id, session_id = self.common.session_key("telegram", payload)
        self.assertEqual(user_id, "telegram:555")
        self.assertEqual(session_id, "telegram:dm:555")

    def test_telegram_group(self):
        payload = {"chat": {"id": -1001, "type": "supergroup"}, "from": {"id": 555}}
        user_id, session_id = self.common.session_key("telegram", payload)
        self.assertEqual(user_id, "telegram:555")
        self.assertEqual(session_id, "telegram:group:-1001")

    def test_telegram_forum_topic(self):
        payload = {"chat": {"id": -1001, "type": "supergroup"}, "from": {"id": 555},
                   "message_thread_id": 42}
        _, session_id = self.common.session_key("telegram", payload)
        self.assertEqual(session_id, "telegram:group:-1001:42")

    def test_unknown_platform_raises(self):
        with self.assertRaises(ValueError):
            self.common.session_key("discord", {})


class TestAttachmentHelpers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp()
        cls.pkg = _scaffold_with(cls.tmpdir, with_telegram=True)
        cls.common = _import_module(cls.pkg, "gateways._common")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def test_inline_data_path(self):
        items = [self.common.InboundAttachment(
            mime_type="image/png", display_name="x.png", data=b"\x89PNG\x00" * 10,
        )]
        parts = self.common.attachments_to_parts(items, inline_max_bytes=10_000)
        self.assertEqual(len(parts), 1)
        self.assertIsNotNone(getattr(parts[0], "inline_data", None))
        self.assertEqual(parts[0].inline_data.mime_type, "image/png")

    def test_file_data_fallback_when_bytes_too_large(self):
        items = [self.common.InboundAttachment(
            mime_type="application/pdf", display_name="big.pdf",
            data=b"x" * 100, file_uri="https://example.com/big.pdf",
        )]
        parts = self.common.attachments_to_parts(items, inline_max_bytes=10)
        self.assertEqual(len(parts), 1)
        self.assertIsNotNone(getattr(parts[0], "file_data", None))
        self.assertEqual(parts[0].file_data.file_uri, "https://example.com/big.pdf")

    def test_text_skip_part_when_no_bytes_no_uri(self):
        items = [self.common.InboundAttachment(
            mime_type="image/png", display_name="orphan.png",
        )]
        parts = self.common.attachments_to_parts(items, inline_max_bytes=10_000)
        self.assertEqual(len(parts), 1)
        self.assertTrue(getattr(parts[0], "text", "").startswith("[attachment "))
        self.assertIn("orphan.png", parts[0].text)

    def test_enforce_count_cap_trims_excess(self):
        items = [
            self.common.InboundAttachment(mime_type="text/plain", display_name=f"f{i}.txt", data=b"hi")
            for i in range(7)
        ]
        kept, notes = self.common.enforce_attachment_limits(items, max_count=5, max_bytes=1024)
        self.assertEqual(len(kept), 5)
        self.assertEqual(len(notes), 2)
        self.assertIn("f5.txt", notes[0])

    def test_enforce_size_cap_drops_oversize(self):
        items = [
            self.common.InboundAttachment(mime_type="text/plain", display_name="ok.txt", data=b"hi"),
            self.common.InboundAttachment(mime_type="application/pdf", display_name="big.pdf", data=b"x" * 1000),
        ]
        kept, notes = self.common.enforce_attachment_limits(items, max_count=10, max_bytes=100)
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0].display_name, "ok.txt")
        self.assertEqual(len(notes), 1)
        self.assertIn("big.pdf", notes[0])


if __name__ == "__main__":
    unittest.main()
