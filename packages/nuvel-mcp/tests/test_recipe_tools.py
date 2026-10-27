"""The record_recipe_signal and share_recipe tools: validation, screen, tenant, storage, relay."""

import io
import json
import uuid

import pytest

from nuvel_mcp import server as server_mod
from nuvel_mcp.auth import current_principal
from nuvel_mcp.server import FEEDBACK_TOOLS, HTTP_DEFAULT_TOOLS, SkillsMCPServer

UUID_A = "11111111-1111-4111-8111-111111111111"
UUID_B = "22222222-2222-4222-8222-222222222222"
UUID_C = "33333333-3333-4333-8333-333333333333"
PLUGIN = {"name": "chief-of-staff", "version": "0.3.0"}
SIGNAL = {
    "schema": "nuvel.recipe_signal/1", "signal_id": UUID_A, "recipe_id": UUID_B, "install_id": UUID_C,
    "plugin": PLUGIN, "category": "client-followup", "event": "used",
    "at": "2026-10-27T10:00:00Z", "uses": 3, "successes": 2, "corrections": 0,
}
RECIPE = {
    "schema": "nuvel.recipe/1", "recipe_id": UUID_B, "install_id": UUID_C, "plugin": PLUGIN,
    "version": 1, "category": "client-followup", "language": "es",
    "title": "Seguimiento tras una reunión", "when_to_use": "Cuando hay que cerrar acuerdos con el cliente",
    "steps": [{"action": "draft", "tool_kind": "email", "text": "Redactar el resumen para el cliente"}],
    "pitfalls": ["No enviar sin revisar"],
}
SIGNAL_FILE = "_recipes/signals/2026-10-27.jsonl"


@pytest.fixture(autouse=True)
def _no_relay(monkeypatch):
    monkeypatch.delenv("NUVEL_FEEDBACK_URL", raising=False)


def call_signal(s, signal):
    res = s.handle_tools_call({"name": "record_recipe_signal", "arguments": {"signal": signal}})
    return res, json.loads(res["content"][0]["text"])


def call_recipe(s, recipe):
    res = s.handle_tools_call({"name": "share_recipe", "arguments": {"recipe": recipe}})
    return res, json.loads(res["content"][0]["text"])


class _FakeUrlopen:
    def __init__(self, exc=None):
        self.exc, self.requests = exc, []

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        if self.exc:
            raise self.exc
        return io.BytesIO(b"{}")


def test_signal_recorded_then_duplicate(tmp_path):
    s = SkillsMCPServer(None, feedback_dir=tmp_path)
    res, body = call_signal(s, SIGNAL)
    assert res["isError"] is False and body == {"status": "recorded", "signal_id": UUID_A}
    res, body = call_signal(s, SIGNAL)
    assert res["isError"] is False and body == {"status": "duplicate", "signal_id": UUID_A}
    assert len((tmp_path / SIGNAL_FILE).read_text().splitlines()) == 1


def test_signal_tenant_from_principal(tmp_path):
    s = SkillsMCPServer(None, feedback_dir=tmp_path)
    token = current_principal.set("acme")
    try:
        call_signal(s, SIGNAL)
    finally:
        current_principal.reset(token)
    assert json.loads((tmp_path / SIGNAL_FILE).read_text())["tenant"] == "acme"


def test_no_principal_means_no_tenant(tmp_path):
    s = SkillsMCPServer(None, feedback_dir=tmp_path)
    call_signal(s, SIGNAL)
    assert "tenant" not in json.loads((tmp_path / SIGNAL_FILE).read_text())


@pytest.mark.parametrize("name,doc", [("record_recipe_signal", SIGNAL), ("share_recipe", RECIPE)])
def test_client_tenant_rejected(tmp_path, name, doc):
    s = SkillsMCPServer(None, feedback_dir=tmp_path)
    key = "signal" if name == "record_recipe_signal" else "recipe"
    res = s.handle_tools_call({"name": name, "arguments": {key: {**doc, "tenant": "evil"}}})
    assert res["isError"] is True and "evil" not in json.dumps(res)
    assert json.loads(res["content"][0]["text"])["errors"] == ["/tenant"]
    assert not (tmp_path / "_recipes").exists()


def test_signal_screen_rejects_paths_only(tmp_path):
    s = SkillsMCPServer(None, feedback_dir=tmp_path)
    res, body = call_signal(s, {**SIGNAL, "category": "ana@acme.com"})
    assert res["isError"] is True and "ana@acme.com" not in json.dumps(res)
    assert body["status"] == "rejected" and body["errors"]
    assert not (tmp_path / "_recipes").exists()


def test_share_recipe_recorded_and_duplicate(tmp_path):
    s = SkillsMCPServer(None, feedback_dir=tmp_path)
    token = current_principal.set("acme")
    try:
        res, body = call_recipe(s, RECIPE)
        assert res["isError"] is False
        assert body == {"status": "recorded", "recipe_id": UUID_B, "version": 1}
        _, body = call_recipe(s, RECIPE)
        assert body == {"status": "duplicate", "recipe_id": UUID_B, "version": 1}
        _, body = call_recipe(s, {**RECIPE, "version": 2})
        assert body["status"] == "recorded" and body["version"] == 2
    finally:
        current_principal.reset(token)
    stored = list((tmp_path / "_recipes" / "recipes").rglob("v1.json"))
    assert len(stored) == 1 and json.loads(stored[0].read_text())["tenant"] == "acme"


def test_share_recipe_schema_rejection(tmp_path):
    s = SkillsMCPServer(None, feedback_dir=tmp_path)
    res, body = call_recipe(s, {**RECIPE, "title": "x" * 200})
    assert res["isError"] is True and body == {"status": "rejected", "errors": ["/title"]}


def test_share_recipe_screen_rejects_with_paths_only(tmp_path):
    s = SkillsMCPServer(None, feedback_dir=tmp_path)
    bad = {**RECIPE, "steps": [{**RECIPE["steps"][0], "text": "Escribir a ana@acme.com"}]}
    res, body = call_recipe(s, bad)
    assert res["isError"] is True
    assert body == {"status": "rejected", "errors": ["/steps/0/text"]}
    assert "ana@acme.com" not in json.dumps(res)
    assert not (tmp_path / "_recipes").exists()


def test_missing_argument_rejected(tmp_path):
    s = SkillsMCPServer(None, feedback_dir=tmp_path)
    for name in ("record_recipe_signal", "share_recipe"):
        res = s.handle_tools_call({"name": name, "arguments": {}})
        assert res["isError"] is True
        assert json.loads(res["content"][0]["text"])["status"] == "rejected"


def test_tools_listed_with_descriptions(tmp_path):
    assert {"record_recipe_signal", "share_recipe"} <= FEEDBACK_TOOLS
    s = SkillsMCPServer(None, feedback_dir=tmp_path)
    tools = {t["name"]: t for t in s.handle_tools_list({})["tools"]}
    sig, rec = tools["record_recipe_signal"], tools["share_recipe"]
    assert sig["inputSchema"]["required"] == ["signal"]
    assert rec["inputSchema"]["required"] == ["recipe"]
    assert "content-free" in sig["description"]
    for word in ("names", "companies", "amounts", "dates", "places", "contacts", "URLs", "tenant"):
        assert word in rec["description"]
    assert "tenant" in sig["description"]


def test_tools_served_in_remote_mode(tmp_path):
    assert {"record_recipe_signal", "share_recipe"} <= HTTP_DEFAULT_TOOLS
    s = SkillsMCPServer(None, feedback_dir=tmp_path)
    s.enable_remote_mode()
    names = {t["name"] for t in s.handle_tools_list({})["tools"]}
    assert {"record_recipe_signal", "share_recipe"} <= names


class _Broken:
    def append_signal(self, signal):
        raise OSError("/secret/path: disk on fire")

    def put_recipe(self, recipe):
        raise OSError("/secret/path: disk on fire")


def test_store_error_is_generic(tmp_path):
    s = SkillsMCPServer(None, feedback_dir=tmp_path)
    s.recipe_store = _Broken()
    for name, key, doc in (("record_recipe_signal", "signal", SIGNAL), ("share_recipe", "recipe", RECIPE)):
        with pytest.raises(server_mod.McpError) as exc:
            s.handle_tools_call({"name": name, "arguments": {key: doc}})
        assert "secret" not in str(exc.value) and "disk" not in str(exc.value)


def test_relay_after_local_write(tmp_path, monkeypatch):
    monkeypatch.setenv("NUVEL_FEEDBACK_URL", "https://relay.example/x")
    fake = _FakeUrlopen()
    monkeypatch.setattr(server_mod.urllib.request, "urlopen", fake)
    s = SkillsMCPServer(None, feedback_dir=tmp_path)
    call_signal(s, SIGNAL)
    call_recipe(s, RECIPE)
    call_signal(s, SIGNAL)  # duplicate: not relayed
    sent = [json.loads(r.data) for r in fake.requests]
    assert [p["type"] for p in sent] == ["recipe_signal", "recipe"]
    assert sent[0]["record"]["signal_id"] == UUID_A and sent[1]["record"]["recipe_id"] == UUID_B


def test_relay_failure_swallowed(tmp_path, monkeypatch):
    monkeypatch.setenv("NUVEL_FEEDBACK_URL", "https://relay.example/x")
    monkeypatch.setattr(server_mod.urllib.request, "urlopen", _FakeUrlopen(exc=OSError("down")))
    s = SkillsMCPServer(None, feedback_dir=tmp_path)
    res, body = call_recipe(s, RECIPE)
    assert res["isError"] is False and body["status"] == "recorded"
    res, body = call_signal(s, SIGNAL)
    assert res["isError"] is False and body["status"] == "recorded"
