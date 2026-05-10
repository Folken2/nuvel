"""Unit tests for the voice-memo transcription module + gateway wiring.

Covers:
- env-gate off → no transcription, audio attachment falls through unchanged
- gate on with mocked OpenAI Whisper response → transcript replaces audio
- gate on with mocked Groq Whisper response → transcript replaces audio
- audio mime detection (positive + negative)
- transcription failure → fallback marker, audio attachment is still stripped
- multimodal message with both image + voice → image forwarded, voice transcribed
"""

import asyncio
import importlib.util
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from nuvel.backends.adk.scaffold import scaffold_agent


def _scaffold_all(tmpdir: str) -> Path:
    """Scaffold an agent with both Slack + Telegram overlays for testing."""
    result = scaffold_agent(
        "vt-test", output_dir=tmpdir, with_slack=True, with_telegram=True,
    )
    if result["status"] != "ok":
        raise AssertionError(result.get("message"))
    return Path(result["path"]) / "vt_test"


def _import_modules(pkg_dir: Path):
    """Import the generated `vt_test` package and return its modules."""
    import types as _types

    pkg = _types.ModuleType("vt_test")
    pkg.__path__ = [str(pkg_dir)]
    pkg.__package__ = "vt_test"
    sys.modules["vt_test"] = pkg

    gw_init = pkg_dir / "gateways" / "__init__.py"
    gw_spec = importlib.util.spec_from_file_location(
        "vt_test.gateways", gw_init,
        submodule_search_locations=[str(pkg_dir / "gateways")],
    )
    gw_pkg = importlib.util.module_from_spec(gw_spec)
    gw_pkg.__package__ = "vt_test.gateways"
    sys.modules["vt_test.gateways"] = gw_pkg
    pkg.gateways = gw_pkg
    gw_spec.loader.exec_module(gw_pkg)

    def _load(name: str, filename: str):
        path = pkg_dir / "gateways" / filename
        spec = importlib.util.spec_from_file_location(f"vt_test.gateways.{name}", path)
        mod = importlib.util.module_from_spec(spec)
        mod.__package__ = "vt_test.gateways"
        sys.modules[f"vt_test.gateways.{name}"] = mod
        spec.loader.exec_module(mod)
        return mod

    common = _load("_common", "_common.py")
    transcription = _load("transcription", "transcription.py")
    slack = _load("slack", "slack.py")
    telegram = _load("telegram", "telegram.py")
    return common, transcription, slack, telegram


class TestTranscriptionModule(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp()
        pkg = _scaffold_all(cls.tmpdir)
        cls.common, cls.tx, cls.slack, cls.tg = _import_modules(pkg)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    # ------------------------------------------------------------------
    # is_audio_attachment
    # ------------------------------------------------------------------
    def test_is_audio_detects_audio_mime(self):
        self.assertTrue(self.tx.is_audio_attachment("audio/ogg", "voice.ogg"))
        self.assertTrue(self.tx.is_audio_attachment("audio/mpeg", "x.mp3"))

    def test_is_audio_detects_extension_when_mime_unknown(self):
        self.assertTrue(self.tx.is_audio_attachment("application/octet-stream", "memo.m4a"))
        self.assertTrue(self.tx.is_audio_attachment(None, "memo.opus"))

    def test_is_audio_rejects_non_audio(self):
        self.assertFalse(self.tx.is_audio_attachment("image/png", "x.png"))
        self.assertFalse(self.tx.is_audio_attachment("application/pdf", "doc.pdf"))
        self.assertFalse(self.tx.is_audio_attachment(None, None))

    # ------------------------------------------------------------------
    # transcribe_audio — provider behavior
    # ------------------------------------------------------------------
    def _mock_post(self, status_code=200, body=None):
        resp = MagicMock()
        resp.status_code = status_code
        resp.json = MagicMock(return_value=body or {"text": "hello world"})
        resp.text = "err"
        post = AsyncMock(return_value=resp)
        return post

    def test_transcribe_openai_calls_openai_endpoint(self):
        post = self._mock_post(body={"text": "openai transcript"})
        with patch("httpx.AsyncClient.post", new=post), \
             patch.dict(
                 "os.environ",
                 {"GATEWAY_TRANSCRIBE_PROVIDER": "openai", "OPENAI_API_KEY": "sk-test"},
                 clear=False,
             ):
            text = asyncio.run(self.tx.transcribe_audio(b"\x00OGG", "audio/ogg"))
        self.assertEqual(text, "openai transcript")
        url = post.call_args.args[1] if len(post.call_args.args) > 1 else post.call_args.kwargs.get("url")
        # url is positional in client.post(url, ...) — first positional after self
        called_url = post.call_args.args[0] if post.call_args.args else post.call_args.kwargs["url"]
        self.assertIn("api.openai.com", called_url)
        headers = post.call_args.kwargs.get("headers") or {}
        self.assertEqual(headers.get("Authorization"), "Bearer sk-test")

    def test_transcribe_groq_calls_groq_endpoint(self):
        post = self._mock_post(body={"text": "groq transcript"})
        with patch("httpx.AsyncClient.post", new=post), \
             patch.dict(
                 "os.environ",
                 {"GATEWAY_TRANSCRIBE_PROVIDER": "groq", "GROQ_API_KEY": "gsk-test"},
                 clear=False,
             ):
            text = asyncio.run(self.tx.transcribe_audio(b"\x00OGG", "audio/ogg"))
        self.assertEqual(text, "groq transcript")
        called_url = post.call_args.args[0] if post.call_args.args else post.call_args.kwargs["url"]
        self.assertIn("api.groq.com", called_url)
        headers = post.call_args.kwargs.get("headers") or {}
        self.assertEqual(headers.get("Authorization"), "Bearer gsk-test")

    def test_transcribe_missing_api_key_raises(self):
        with patch.dict(
            "os.environ",
            {"GATEWAY_TRANSCRIBE_PROVIDER": "openai"},
            clear=False,
        ):
            # Drop the key if present in the parent env.
            import os as _os
            _os.environ.pop("OPENAI_API_KEY", None)
            with self.assertRaises(self.tx.TranscriptionError):
                asyncio.run(self.tx.transcribe_audio(b"\x00OGG", "audio/ogg"))

    def test_transcribe_unsupported_provider_raises(self):
        with patch.dict(
            "os.environ",
            {"GATEWAY_TRANSCRIBE_PROVIDER": "moonshot", "OPENAI_API_KEY": "x"},
            clear=False,
        ):
            with self.assertRaises(self.tx.TranscriptionError):
                asyncio.run(self.tx.transcribe_audio(b"\x00OGG", "audio/ogg"))

    def test_transcribe_http_error_raises(self):
        resp = MagicMock()
        resp.status_code = 500
        resp.text = "server boom"
        post = AsyncMock(return_value=resp)
        with patch("httpx.AsyncClient.post", new=post), \
             patch.dict(
                 "os.environ",
                 {"GATEWAY_TRANSCRIBE_PROVIDER": "openai", "OPENAI_API_KEY": "sk-x"},
                 clear=False,
             ):
            with self.assertRaises(self.tx.TranscriptionError):
                asyncio.run(self.tx.transcribe_audio(b"\x00OGG", "audio/ogg"))

    # ------------------------------------------------------------------
    # transcription_enabled gate
    # ------------------------------------------------------------------
    def test_gate_off_by_default(self):
        with patch.dict("os.environ", {}, clear=False):
            import os as _os
            _os.environ.pop("GATEWAY_TRANSCRIBE_AUDIO", None)
            self.assertFalse(self.tx.transcription_enabled())

    def test_gate_on_with_one(self):
        with patch.dict("os.environ", {"GATEWAY_TRANSCRIBE_AUDIO": "1"}, clear=False):
            self.assertTrue(self.tx.transcription_enabled())


class TestSlackVoiceTranscription(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp()
        pkg = _scaffold_all(cls.tmpdir)
        cls.common, cls.tx, cls.slack, cls.tg = _import_modules(pkg)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def _attachments(self, *, with_image=False):
        items = [
            self.common.InboundAttachment(
                mime_type="audio/ogg", display_name="voice.ogg", data=b"OGGfakebytes",
            ),
        ]
        if with_image:
            items.insert(0, self.common.InboundAttachment(
                mime_type="image/png", display_name="x.png", data=b"\x89PNG",
            ))
        return items

    def test_gate_off_passes_audio_through_unchanged(self):
        atts = self._attachments()
        import os as _os
        _os.environ.pop("GATEWAY_TRANSCRIBE_AUDIO", None)
        kept, markers = asyncio.run(
            self.slack._transcribe_voice_attachments(atts, payload={"files": []})
        )
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0].mime_type, "audio/ogg")
        self.assertEqual(markers, [])

    def test_gate_on_replaces_audio_with_transcript_marker(self):
        atts = self._attachments()
        async def fake_transcribe(audio, mime):
            return "hello from voice"

        payload = {"files": [{"name": "voice.ogg", "duration_ms": 23000}]}

        with patch.dict("os.environ", {"GATEWAY_TRANSCRIBE_AUDIO": "1"}, clear=False), \
             patch.object(self.slack, "transcribe_audio", side_effect=fake_transcribe):
            kept, markers = asyncio.run(
                self.slack._transcribe_voice_attachments(atts, payload=payload)
            )
        self.assertEqual(kept, [])
        self.assertEqual(len(markers), 1)
        self.assertIn("hello from voice", markers[0])
        self.assertIn("0:23", markers[0])

    def test_failure_falls_back_to_marker(self):
        atts = self._attachments()
        async def boom(audio, mime):
            raise self.tx.TranscriptionError("nope")

        with patch.dict(
            "os.environ", {"GATEWAY_TRANSCRIBE_AUDIO": "1"}, clear=False
        ), patch.object(self.slack, "transcribe_audio", side_effect=boom):
            kept, markers = asyncio.run(
                self.slack._transcribe_voice_attachments(atts, payload={"files": []})
            )
        self.assertEqual(kept, [])
        self.assertEqual(markers, [self.tx.FALLBACK_MARKER])

    def test_image_plus_voice_keeps_image_only(self):
        atts = self._attachments(with_image=True)
        async def fake_transcribe(audio, mime):
            return "voice content"

        with patch.dict(
            "os.environ", {"GATEWAY_TRANSCRIBE_AUDIO": "1"}, clear=False
        ), patch.object(self.slack, "transcribe_audio", side_effect=fake_transcribe):
            kept, markers = asyncio.run(
                self.slack._transcribe_voice_attachments(atts, payload={"files": []})
            )
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0].mime_type, "image/png")
        self.assertEqual(len(markers), 1)
        self.assertIn("voice content", markers[0])


class TestTelegramVoiceTranscription(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp()
        pkg = _scaffold_all(cls.tmpdir)
        cls.common, cls.tx, cls.slack, cls.tg = _import_modules(pkg)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def test_gate_off_passes_voice_through(self):
        atts = [self.common.InboundAttachment(
            mime_type="audio/ogg", display_name="voice.ogg", data=b"OGGbytes",
        )]
        import os as _os
        _os.environ.pop("GATEWAY_TRANSCRIBE_AUDIO", None)
        msg = {"voice": {"file_id": "x", "duration": 7}}
        kept, markers = asyncio.run(
            self.tg._transcribe_voice_attachments(atts, msg=msg)
        )
        self.assertEqual(len(kept), 1)
        self.assertEqual(markers, [])

    def test_gate_on_with_groq_replaces_voice(self):
        atts = [self.common.InboundAttachment(
            mime_type="audio/ogg", display_name="voice.ogg", data=b"OGGbytes",
        )]
        msg = {"voice": {"file_id": "x", "duration": 12}}

        # Hit transcribe_audio for real with mocked httpx (groq path).
        resp = MagicMock()
        resp.status_code = 200
        resp.json = MagicMock(return_value={"text": "groq voice text"})
        post = AsyncMock(return_value=resp)

        with patch.dict(
            "os.environ",
            {
                "GATEWAY_TRANSCRIBE_AUDIO": "1",
                "GATEWAY_TRANSCRIBE_PROVIDER": "groq",
                "GROQ_API_KEY": "gsk-test",
            },
            clear=False,
        ), patch("httpx.AsyncClient.post", new=post):
            kept, markers = asyncio.run(
                self.tg._transcribe_voice_attachments(atts, msg=msg)
            )

        self.assertEqual(kept, [])
        self.assertEqual(len(markers), 1)
        self.assertIn("groq voice text", markers[0])
        self.assertIn("0:12", markers[0])
        called_url = post.call_args.args[0] if post.call_args.args else post.call_args.kwargs["url"]
        self.assertIn("api.groq.com", called_url)

    def test_audio_part_uses_audio_duration(self):
        """Telegram `audio` (mp3 etc.) also exposes `duration`; should be used."""
        atts = [self.common.InboundAttachment(
            mime_type="audio/mpeg", display_name="track.mp3", data=b"MP3bytes",
        )]
        msg = {"audio": {"file_id": "x", "duration": 65}}

        async def fake_transcribe(audio, mime):
            return "song lyrics"

        with patch.dict(
            "os.environ", {"GATEWAY_TRANSCRIBE_AUDIO": "1"}, clear=False
        ), patch.object(self.tg, "transcribe_audio", side_effect=fake_transcribe):
            kept, markers = asyncio.run(
                self.tg._transcribe_voice_attachments(atts, msg=msg)
            )
        self.assertEqual(kept, [])
        self.assertIn("1:05", markers[0])
        self.assertIn("song lyrics", markers[0])


if __name__ == "__main__":
    unittest.main()
