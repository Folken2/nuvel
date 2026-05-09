"""Unit tests for the generated agent's Telegram gateway router.

Each test scaffolds a tiny agent with --with-telegram, then dynamically
imports its `gateways.telegram` module and exercises the router with
FastAPI's TestClient against a mocked agent runner and mocked httpx.
"""

import importlib.util
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from nuvel.backends.adk.scaffold import scaffold_agent


def _scaffold_telegram(tmpdir):
    result = scaffold_agent("tg-test", output_dir=tmpdir, with_telegram=True)
    if result["status"] != "ok":
        raise AssertionError(result.get("message"))
    return Path(result["path"]) / "tg_test"


def _import_telegram(pkg_dir: Path):
    # Register tg_test as a top-level package so that telegram.py's
    # "from tg_test.gateways._common import ..." resolves correctly.
    import types as _types

    # tg_test (top-level package)
    tg_test_pkg = _types.ModuleType("tg_test")
    tg_test_pkg.__path__ = [str(pkg_dir)]
    tg_test_pkg.__package__ = "tg_test"
    sys.modules["tg_test"] = tg_test_pkg

    # tg_test.gateways (sub-package)
    gw_init_path = pkg_dir / "gateways" / "__init__.py"
    gw_spec = importlib.util.spec_from_file_location(
        "tg_test.gateways", gw_init_path,
        submodule_search_locations=[str(pkg_dir / "gateways")]
    )
    gw_pkg = importlib.util.module_from_spec(gw_spec)
    gw_pkg.__package__ = "tg_test.gateways"
    sys.modules["tg_test.gateways"] = gw_pkg
    tg_test_pkg.gateways = gw_pkg
    gw_spec.loader.exec_module(gw_pkg)

    # tg_test.gateways._common
    common_path = pkg_dir / "gateways" / "_common.py"
    common_spec = importlib.util.spec_from_file_location("tg_test.gateways._common", common_path)
    common_mod = importlib.util.module_from_spec(common_spec)
    common_mod.__package__ = "tg_test.gateways"
    sys.modules["tg_test.gateways._common"] = common_mod
    common_spec.loader.exec_module(common_mod)

    # tg_test.gateways.telegram
    sub_path = pkg_dir / "gateways" / "telegram.py"
    sub_spec = importlib.util.spec_from_file_location("tg_test.gateways.telegram", sub_path)
    sub = importlib.util.module_from_spec(sub_spec)
    sub.__package__ = "tg_test.gateways"
    sys.modules["tg_test.gateways.telegram"] = sub
    sub_spec.loader.exec_module(sub)
    return sub


class TestTelegramRouter(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp()
        pkg = _scaffold_telegram(cls.tmpdir)
        cls.tg = _import_telegram(pkg)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def _client(self, runner_mock, secret="testsecret"):
        app = FastAPI()
        app.state.runner = runner_mock
        app.state.app_name = "tg-test"
        app.include_router(self.tg.router)
        with patch.dict("os.environ", {
            "TELEGRAM_WEBHOOK_SECRET": secret,
            "TELEGRAM_BOT_TOKEN": "TESTTOKEN",
        }, clear=False):
            yield TestClient(app)

    def test_missing_secret_returns_401(self):
        runner = AsyncMock()
        for client in self._client(runner):
            r = client.post("/gateways/telegram", json={"update_id": 1})
            self.assertEqual(r.status_code, 401)

    def test_wrong_secret_returns_401(self):
        runner = AsyncMock()
        for client in self._client(runner):
            r = client.post(
                "/gateways/telegram",
                json={"update_id": 1},
                headers={"X-Telegram-Bot-Api-Secret-Token": "WRONG"},
            )
            self.assertEqual(r.status_code, 401)

    def test_valid_text_message_returns_200(self):
        runner = AsyncMock()
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value.status_code = 200
            for client in self._client(runner):
                r = client.post(
                    "/gateways/telegram",
                    json={
                        "update_id": 42,
                        "message": {
                            "message_id": 1,
                            "chat": {"id": 999, "type": "private"},
                            "from": {"id": 555},
                            "text": "hello",
                        },
                    },
                    headers={"X-Telegram-Bot-Api-Secret-Token": "testsecret"},
                )
                self.assertEqual(r.status_code, 200)

    def test_non_text_update_is_noop_200(self):
        runner = AsyncMock()
        for client in self._client(runner):
            r = client.post(
                "/gateways/telegram",
                json={"update_id": 1, "edited_message": {"text": "x"}},
                headers={"X-Telegram-Bot-Api-Secret-Token": "testsecret"},
            )
            self.assertEqual(r.status_code, 200)

    def test_message_with_photo_downloads_and_invokes(self):
        runner = AsyncMock()
        runner.session_service = AsyncMock()
        runner.session_service.get_session = AsyncMock(return_value=None)
        runner.session_service.create_session = AsyncMock()

        captured = {}

        async def fake_invoke(_runner, _u, _s, text, attachments=None, **_kw):
            captured["text"] = text
            captured["attachments"] = attachments
            from types import SimpleNamespace
            return SimpleNamespace(text="ok", attachments=[])

        # Two httpx.AsyncClient.post calls: getFile, then sendMessage. Use side_effect.
        get_file_resp = MagicMock()
        get_file_resp.status_code = 200
        get_file_resp.json = MagicMock(return_value={"ok": True, "result": {"file_path": "photos/x.jpg"}})
        get_file_resp.raise_for_status = MagicMock()

        send_msg_resp = MagicMock()
        send_msg_resp.status_code = 200
        send_msg_resp.text = "{}"

        download_resp = MagicMock()
        download_resp.status_code = 200
        download_resp.content = b"\xff\xd8\xff\xe0fakejpg"
        download_resp.raise_for_status = MagicMock()

        async def fake_post(self_client, url, *args, **kwargs):
            if "/getFile" in url:
                return get_file_resp
            if "/sendChatAction" in url:
                return MagicMock(status_code=200)
            return send_msg_resp

        async def fake_get(self_client, url, *args, **kwargs):
            return download_resp

        with patch.object(self.tg, "invoke_agent", side_effect=fake_invoke), \
             patch("httpx.AsyncClient.post", new=fake_post), \
             patch("httpx.AsyncClient.get", new=fake_get):
            for client in self._client(runner):
                r = client.post(
                    "/gateways/telegram",
                    json={
                        "update_id": 1,
                        "message": {
                            "message_id": 1,
                            "chat": {"id": 999, "type": "private"},
                            "from": {"id": 555},
                            "caption": "what's this?",
                            "photo": [
                                {"file_id": "small", "file_size": 100, "width": 100, "height": 100},
                                {"file_id": "big", "file_size": 5000, "width": 800, "height": 600},
                            ],
                        },
                    },
                    headers={"X-Telegram-Bot-Api-Secret-Token": "testsecret"},
                )
                self.assertEqual(r.status_code, 200)

        import time
        for _ in range(50):
            if "attachments" in captured:
                break
            time.sleep(0.02)
        self.assertIn("attachments", captured)
        self.assertEqual(len(captured["attachments"]), 1)
        self.assertEqual(captured["attachments"][0].mime_type, "image/jpeg")
        self.assertEqual(captured["attachments"][0].data, b"\xff\xd8\xff\xe0fakejpg")
        self.assertEqual(captured["text"], "what's this?")

    def test_message_with_document_passes_mime(self):
        runner = AsyncMock()
        runner.session_service = AsyncMock()
        runner.session_service.get_session = AsyncMock(return_value=None)
        runner.session_service.create_session = AsyncMock()
        captured = {}

        async def fake_invoke(_r, _u, _s, text, attachments=None, **_kw):
            captured["attachments"] = attachments
            from types import SimpleNamespace
            return SimpleNamespace(text="ok", attachments=[])

        gf = MagicMock(); gf.status_code = 200
        gf.json = MagicMock(return_value={"ok": True, "result": {"file_path": "docs/y.pdf"}})
        gf.raise_for_status = MagicMock()
        sm = MagicMock(); sm.status_code = 200; sm.text = "{}"
        dl = MagicMock(); dl.status_code = 200; dl.content = b"%PDF-fake"
        dl.raise_for_status = MagicMock()

        async def fake_post(self_client, url, *a, **kw):
            return gf if "/getFile" in url else sm
        async def fake_get(self_client, url, *a, **kw):
            return dl

        with patch.object(self.tg, "invoke_agent", side_effect=fake_invoke), \
             patch("httpx.AsyncClient.post", new=fake_post), \
             patch("httpx.AsyncClient.get", new=fake_get):
            for client in self._client(runner):
                r = client.post(
                    "/gateways/telegram",
                    json={
                        "update_id": 2,
                        "message": {
                            "message_id": 1,
                            "chat": {"id": 999, "type": "private"},
                            "from": {"id": 555},
                            "caption": "read this",
                            "document": {"file_id": "FILE", "file_name": "y.pdf",
                                         "mime_type": "application/pdf", "file_size": 8},
                        },
                    },
                    headers={"X-Telegram-Bot-Api-Secret-Token": "testsecret"},
                )
                self.assertEqual(r.status_code, 200)

        import time
        for _ in range(50):
            if "attachments" in captured:
                break
            time.sleep(0.02)
        self.assertEqual(captured["attachments"][0].mime_type, "application/pdf")
        self.assertEqual(captured["attachments"][0].display_name, "y.pdf")

    def test_outbound_inline_image_calls_send_photo(self):
        runner = AsyncMock()
        runner.session_service = AsyncMock()
        runner.session_service.get_session = AsyncMock(return_value=None)
        runner.session_service.create_session = AsyncMock()

        from tg_test.gateways._common import AgentReply, OutboundAttachment

        async def fake_invoke(*_a, **_kw):
            return AgentReply(
                text="here you go",
                attachments=[OutboundAttachment(
                    mime_type="image/png", display_name="chart.png", data=b"\x89PNGdata",
                )],
            )

        posted = []
        async def capture_post(self_client, url, *args, **kwargs):
            posted.append({"url": url, "kwargs": kwargs})
            m = MagicMock()
            m.status_code = 200
            m.text = "{}"
            return m

        with patch.object(self.tg, "invoke_agent", side_effect=fake_invoke), \
             patch("httpx.AsyncClient.post", new=capture_post):
            for client in self._client(runner):
                r = client.post(
                    "/gateways/telegram",
                    json={
                        "update_id": 9,
                        "message": {
                            "message_id": 1,
                            "chat": {"id": 999, "type": "private"},
                            "from": {"id": 555},
                            "text": "draw it",
                        },
                    },
                    headers={"X-Telegram-Bot-Api-Secret-Token": "testsecret"},
                )
                self.assertEqual(r.status_code, 200)

        import time
        for _ in range(50):
            if any("/sendPhoto" in p["url"] for p in posted):
                break
            time.sleep(0.02)
        photo_calls = [p for p in posted if "/sendPhoto" in p["url"]]
        self.assertEqual(len(photo_calls), 1)
        files = photo_calls[0]["kwargs"].get("files") or {}
        data = photo_calls[0]["kwargs"].get("data") or {}
        self.assertIn("photo", files)
        self.assertEqual(data.get("caption"), "here you go")

    def test_outbound_uri_only_calls_send_document_with_url(self):
        runner = AsyncMock()
        runner.session_service = AsyncMock()
        runner.session_service.get_session = AsyncMock(return_value=None)
        runner.session_service.create_session = AsyncMock()

        from tg_test.gateways._common import AgentReply, OutboundAttachment

        async def fake_invoke(*_a, **_kw):
            return AgentReply(
                text="here is the file",
                attachments=[OutboundAttachment(
                    mime_type="application/pdf", display_name="report.pdf",
                    file_uri="https://example.com/report.pdf",
                )],
            )

        posted = []
        async def capture_post(self_client, url, *args, **kwargs):
            posted.append({"url": url, "kwargs": kwargs})
            m = MagicMock(); m.status_code = 200; m.text = "{}"
            return m

        with patch.object(self.tg, "invoke_agent", side_effect=fake_invoke), \
             patch("httpx.AsyncClient.post", new=capture_post):
            for client in self._client(runner):
                r = client.post(
                    "/gateways/telegram",
                    json={
                        "update_id": 10,
                        "message": {
                            "message_id": 1,
                            "chat": {"id": 999, "type": "private"},
                            "from": {"id": 555},
                            "text": "send it",
                        },
                    },
                    headers={"X-Telegram-Bot-Api-Secret-Token": "testsecret"},
                )
                self.assertEqual(r.status_code, 200)

        import time
        for _ in range(50):
            if any("/sendDocument" in p["url"] for p in posted):
                break
            time.sleep(0.02)
        doc_calls = [p for p in posted if "/sendDocument" in p["url"]]
        self.assertEqual(len(doc_calls), 1)
        body = doc_calls[0]["kwargs"].get("json") or {}
        self.assertEqual(body.get("document"), "https://example.com/report.pdf")
        self.assertEqual(body.get("caption"), "here is the file")
