"""Unit tests for the generated agent's Slack gateway router."""

import importlib.util
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from nuvel.backends.adk.scaffold import scaffold_agent


def _scaffold_slack(tmpdir):
    result = scaffold_agent("sl-test", output_dir=tmpdir, with_slack=True)
    if result["status"] != "ok":
        raise AssertionError(result.get("message"))
    return Path(result["path"]) / "sl_test"


def _import_slack(pkg_dir: Path):
    import types as _types

    # sl_test (top-level package)
    sl_test_pkg = _types.ModuleType("sl_test")
    sl_test_pkg.__path__ = [str(pkg_dir)]
    sl_test_pkg.__package__ = "sl_test"
    sys.modules["sl_test"] = sl_test_pkg

    # sl_test.gateways (sub-package)
    gw_init_path = pkg_dir / "gateways" / "__init__.py"
    gw_spec = importlib.util.spec_from_file_location(
        "sl_test.gateways", gw_init_path,
        submodule_search_locations=[str(pkg_dir / "gateways")]
    )
    gw_pkg = importlib.util.module_from_spec(gw_spec)
    gw_pkg.__package__ = "sl_test.gateways"
    sys.modules["sl_test.gateways"] = gw_pkg
    sl_test_pkg.gateways = gw_pkg
    gw_spec.loader.exec_module(gw_pkg)

    # sl_test.gateways._common
    common_path = pkg_dir / "gateways" / "_common.py"
    common_spec = importlib.util.spec_from_file_location("sl_test.gateways._common", common_path)
    common_mod = importlib.util.module_from_spec(common_spec)
    common_mod.__package__ = "sl_test.gateways"
    sys.modules["sl_test.gateways._common"] = common_mod
    common_spec.loader.exec_module(common_mod)

    # sl_test.gateways.slack
    sub_path = pkg_dir / "gateways" / "slack.py"
    sub_spec = importlib.util.spec_from_file_location("sl_test.gateways.slack", sub_path)
    sub = importlib.util.module_from_spec(sub_spec)
    sub.__package__ = "sl_test.gateways"
    sys.modules["sl_test.gateways.slack"] = sub
    sub_spec.loader.exec_module(sub)
    return sub


class TestSlackRouter(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp()
        pkg = _scaffold_slack(cls.tmpdir)
        cls.sl = _import_slack(pkg)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def _client(self, runner_mock, composio_mock=None, env_extra=None):
        app = FastAPI()
        app.state.runner = runner_mock
        app.state.app_name = "sl-test"
        app.state.composio_client = composio_mock or MagicMock()
        app.include_router(self.sl.router)
        env = {"COMPOSIO_WEBHOOK_SECRET": "s3cret", **(env_extra or {})}
        with patch.dict("os.environ", env, clear=False):
            yield TestClient(app)

    def test_missing_secret_returns_401(self):
        for client in self._client(AsyncMock()):
            r = client.post("/gateways/slack/composio", json={"trigger_slug": "x"})
            self.assertEqual(r.status_code, 401)

    def test_wrong_secret_returns_401(self):
        for client in self._client(AsyncMock()):
            r = client.post("/gateways/slack/composio?secret=wrong", json={"trigger_slug": "x"})
            self.assertEqual(r.status_code, 401)

    def test_unknown_trigger_is_noop_200(self):
        for client in self._client(AsyncMock()):
            r = client.post(
                "/gateways/slack/composio?secret=s3cret",
                json={"trigger_slug": "SLACKBOT_FUTURE_THING", "payload": {}},
            )
            self.assertEqual(r.status_code, 200)

    def test_dm_trigger_invokes_agent(self):
        for client in self._client(AsyncMock()):
            r = client.post(
                "/gateways/slack/composio?secret=s3cret",
                json={
                    "trigger_slug": "SLACKBOT_DIRECT_MESSAGE_RECEIVED",
                    "payload": {
                        "team_id": "T01", "channel": "D456", "user": "U012",
                        "text": "hello", "ts": "1700000000.001", "channel_type": "im",
                    },
                },
            )
            self.assertEqual(r.status_code, 200)

    def test_bot_message_is_dropped_to_prevent_loops(self):
        for client in self._client(AsyncMock()):
            r = client.post(
                "/gateways/slack/composio?secret=s3cret",
                json={
                    "trigger_slug": "SLACKBOT_DIRECT_MESSAGE_RECEIVED",
                    "payload": {"channel": "D1", "user": "U2", "text": "hi", "bot_id": "B1"},
                },
            )
            self.assertEqual(r.status_code, 200)

    def test_dm_with_files_downloads_and_passes_attachments(self):
        """Files in payload are fetched with SLACK_BOT_TOKEN and forwarded to the runner."""
        runner = AsyncMock()
        runner.session_service = AsyncMock()
        runner.session_service.get_session = AsyncMock(return_value=None)
        runner.session_service.create_session = AsyncMock()

        captured = {}

        async def fake_invoke(_runner, _u, _s, text, attachments=None, **_kw):
            captured["text"] = text
            captured["attachments"] = attachments
            return SimpleNamespace(text="ok", attachments=[])

        fake_resp = MagicMock()
        fake_resp.status_code = 200
        fake_resp.content = b"\x89PNG\x00fakebytes"
        fake_resp.headers = {"Content-Type": "image/png"}
        fake_resp.raise_for_status = MagicMock()

        with patch.object(self.sl, "invoke_agent", side_effect=fake_invoke), \
             patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=fake_resp):
            for client in self._client(runner, env_extra={"SLACK_BOT_TOKEN": "xoxb-test"}):
                r = client.post(
                    "/gateways/slack/composio?secret=s3cret",
                    json={
                        "trigger_slug": "SLACKBOT_DIRECT_MESSAGE_RECEIVED",
                        "payload": {
                            "team_id": "T01", "channel": "D456", "user": "U012",
                            "text": "what's this?", "ts": "1700000000.001", "channel_type": "im",
                            "files": [{
                                "id": "F1", "mimetype": "image/png", "name": "x.png",
                                "url_private": "https://files.slack.com/x.png", "size": 14,
                            }],
                        },
                    },
                )
                self.assertEqual(r.status_code, 200)

        # Background task may run after the response — give the loop a tick:
        import asyncio, time
        for _ in range(50):
            if "attachments" in captured:
                break
            time.sleep(0.02)
        self.assertIn("attachments", captured)
        self.assertEqual(len(captured["attachments"]), 1)
        self.assertEqual(captured["attachments"][0].mime_type, "image/png")
        self.assertEqual(captured["attachments"][0].data, b"\x89PNG\x00fakebytes")
        self.assertEqual(captured["attachments"][0].display_name, "x.png")

    def test_files_without_bot_token_fall_back_to_uri(self):
        runner = AsyncMock()
        runner.session_service = AsyncMock()
        runner.session_service.get_session = AsyncMock(return_value=None)
        runner.session_service.create_session = AsyncMock()
        captured = {}

        async def fake_invoke(_r, _u, _s, text, attachments=None, **_kw):
            captured["attachments"] = attachments
            return SimpleNamespace(text="ok", attachments=[])

        with patch.object(self.sl, "invoke_agent", side_effect=fake_invoke):
            for client in self._client(runner):  # no SLACK_BOT_TOKEN
                r = client.post(
                    "/gateways/slack/composio?secret=s3cret",
                    json={
                        "trigger_slug": "SLACKBOT_DIRECT_MESSAGE_RECEIVED",
                        "payload": {
                            "team_id": "T01", "channel": "D456", "user": "U012",
                            "text": "look", "ts": "1700000000.001", "channel_type": "im",
                            "files": [{"id": "F1", "mimetype": "image/png", "name": "x.png",
                                       "url_private": "https://files.slack.com/x.png", "size": 14}],
                        },
                    },
                )
                self.assertEqual(r.status_code, 200)

        import time
        for _ in range(50):
            if "attachments" in captured:
                break
            time.sleep(0.02)
        self.assertIn("attachments", captured)
        self.assertEqual(len(captured["attachments"]), 1)
        self.assertIsNone(captured["attachments"][0].data)
        self.assertEqual(captured["attachments"][0].file_uri, "https://files.slack.com/x.png")

    def test_outbound_inline_image_uploads_via_composio(self):
        runner = AsyncMock()
        runner.session_service = AsyncMock()
        runner.session_service.get_session = AsyncMock(return_value=None)
        runner.session_service.create_session = AsyncMock()

        # Build an AgentReply with one inline outbound attachment.
        common = self.sl  # import sibling
        Reply = self.sl.AgentReply if hasattr(self.sl, "AgentReply") else None
        # Fall back to importing from _common via the existing module path:
        if Reply is None:
            from sl_test.gateways._common import AgentReply, OutboundAttachment
        else:
            from sl_test.gateways._common import OutboundAttachment

        reply = AgentReply(text="here you go", attachments=[
            OutboundAttachment(mime_type="image/png", display_name="chart.png", data=b"\x89PNGdata"),
        ])

        async def fake_invoke(*_a, **_kw):
            return reply

        composio = MagicMock()
        composio.tools.execute = MagicMock(return_value={"ok": True})

        with patch.object(self.sl, "invoke_agent", side_effect=fake_invoke):
            for client in self._client(runner, composio_mock=composio):
                r = client.post(
                    "/gateways/slack/composio?secret=s3cret",
                    json={
                        "trigger_slug": "SLACKBOT_DIRECT_MESSAGE_RECEIVED",
                        "payload": {
                            "team_id": "T01", "channel": "D456", "user": "U012",
                            "text": "draw", "ts": "1700000000.001", "channel_type": "im",
                        },
                    },
                )
                self.assertEqual(r.status_code, 200)

        import time
        for _ in range(50):
            if composio.tools.execute.called:
                break
            time.sleep(0.02)

        # Find the upload call (there may also be a SLACKBOT_SEND_MESSAGE call).
        upload_calls = [c for c in composio.tools.execute.call_args_list
                        if c.args and c.args[0] == self.sl.SLACK_FILES_UPLOAD_TOOL]
        self.assertEqual(len(upload_calls), 1)
        args = upload_calls[0].kwargs.get("arguments") or upload_calls[0].args[1]
        self.assertEqual(args["channel"], "D456")
        self.assertEqual(args["filename"], "chart.png")
        self.assertEqual(args["filetype"], "png")
        # data was b64-encoded
        import base64
        self.assertEqual(base64.b64decode(args["content_b64"]), b"\x89PNGdata")

    def test_outbound_upload_failure_falls_back_to_text_send(self):
        runner = AsyncMock()
        runner.session_service = AsyncMock()
        runner.session_service.get_session = AsyncMock(return_value=None)
        runner.session_service.create_session = AsyncMock()

        from sl_test.gateways._common import AgentReply, OutboundAttachment

        async def fake_invoke(*_a, **_kw):
            return AgentReply(text="here you go", attachments=[
                OutboundAttachment(mime_type="image/png", display_name="x.png", data=b"\x89PNG"),
            ])

        composio = MagicMock()

        def execute_side_effect(tool, *_a, **_kw):
            if tool == self.sl.SLACK_FILES_UPLOAD_TOOL:
                raise RuntimeError("upload boom")
            return {"ok": True}
        composio.tools.execute = MagicMock(side_effect=execute_side_effect)

        with patch.object(self.sl, "invoke_agent", side_effect=fake_invoke):
            for client in self._client(runner, composio_mock=composio):
                r = client.post(
                    "/gateways/slack/composio?secret=s3cret",
                    json={
                        "trigger_slug": "SLACKBOT_DIRECT_MESSAGE_RECEIVED",
                        "payload": {
                            "team_id": "T01", "channel": "D456", "user": "U012",
                            "text": "draw", "ts": "1700000000.001", "channel_type": "im",
                        },
                    },
                )
                self.assertEqual(r.status_code, 200)

        import time
        for _ in range(50):
            send_calls = [c for c in composio.tools.execute.call_args_list
                          if c.args and c.args[0] == "SLACKBOT_SEND_MESSAGE"]
            if send_calls:
                break
            time.sleep(0.02)
        send_calls = [c for c in composio.tools.execute.call_args_list
                      if c.args and c.args[0] == "SLACKBOT_SEND_MESSAGE"]
        self.assertEqual(len(send_calls), 1, "fallback text send should have happened")
        args = send_calls[0].kwargs.get("arguments") or {}
        self.assertEqual(args.get("markdown_text"), "here you go")
