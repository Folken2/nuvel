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
from unittest.mock import AsyncMock, patch

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
