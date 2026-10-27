"""The record_recipe_signal and share_recipe tools: validation, masking, tenant, storage, relay.

Masking outcomes depend on the lexicon, so every share test injects a tiny
``Lexicon`` (``masking.load_lexicon`` is monkeypatched); none reads package data.
"""

import io
import json

import pytest

from nuvel_mcp import masking
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
    "schema": "nuvel.recipe/2", "recipe_id": UUID_B, "install_id": UUID_C, "plugin": PLUGIN,
    "version": 1, "category": "client-followup", "language": "es",
    "title": "Seguimiento tras una reunión", "when_to_use": "Cuando hay que cerrar acuerdos con el cliente",
    "steps": [{"action": "draft", "tool_kind": "email", "slots": ["person"],
               "note": "Redactar el resumen para {person}"}],
    "pitfalls": ["No enviar sin revisar"],
    "narrative": "El usuario pidió el resumen de la reunión con {company} y preparé el borrador para el cliente.",
}
WORDS = """
seguimiento tras una reunión cuando hay que cerrar acuerdos con el cliente redactar
resumen para no enviar sin revisar usuario pidió de la y preparé borrador hablé sobre
contrato
"""
SECRETS = ("pedro", "acme")
SECRET_NARRATIVE = "Hablé con pedro sobre el contrato de Acme S.L. y preparé el borrador para el cliente."
SIGNAL_FILE = "_recipes/signals/2026-10-27.jsonl"
FREE_TEXT = ["/title", "/when_to_use", "/steps/0/note", "/pitfalls/0", "/narrative"]


def _lexicon():
    f = masking.fold
    return masking.Lexicon(
        words=frozenset(f(w) for w in WORDS.split()), numbers=frozenset(), dates=frozenset(),
        currency=frozenset(), version="es-test.1", language="es",
    )


@pytest.fixture(autouse=True)
def _no_relay(monkeypatch):
    monkeypatch.delenv("NUVEL_FEEDBACK_URL", raising=False)


@pytest.fixture(autouse=True)
def _tiny_lexicon(monkeypatch):
    lexicon = _lexicon()
    monkeypatch.setattr(masking, "load_lexicon", lambda language: lexicon if language == "es" else None)
    monkeypatch.setattr(masking, "ABBREVIATIONS", frozenset({"ceo"}))


def call_signal(s, signal):
    res = s.handle_tools_call({"name": "record_recipe_signal", "arguments": {"signal": signal}})
    return res, json.loads(res["content"][0]["text"])


def call_recipe(s, recipe):
    res = s.handle_tools_call({"name": "share_recipe", "arguments": {"recipe": recipe}})
    return res, json.loads(res["content"][0]["text"])


def stored_files(tmp_path):
    return sorted((tmp_path / "_recipes" / "recipes").rglob("v*.json"))


def has_secret(text):
    folded = text.casefold()
    return any(secret in folded for secret in SECRETS)


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
    call_recipe(s, RECIPE)
    assert "tenant" not in json.loads((tmp_path / SIGNAL_FILE).read_text())
    assert "tenant" not in json.loads(stored_files(tmp_path)[0].read_text())


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
        assert body.pop("words") > 0
        assert body == {"status": "recorded", "recipe_id": UUID_B, "version": 1, "masked": 0, "dropped": []}
        _, body = call_recipe(s, RECIPE)
        assert body == {"status": "duplicate", "recipe_id": UUID_B, "version": 1}
        _, body = call_recipe(s, {**RECIPE, "version": 2})
        assert body["status"] == "recorded" and body["version"] == 2
    finally:
        current_principal.reset(token)
    stored = [json.loads(p.read_text()) for p in stored_files(tmp_path) if p.name == "v1.json"]
    assert len(stored) == 1
    record = stored[0]
    assert record["tenant"] == "acme"
    assert record["masking"] == {"lexicon": "es-test.1", "masked": 0,
                                 "words": record["masking"]["words"], "dropped": []}
    assert {k: v for k, v in record.items() if k not in ("tenant", "masking")} == RECIPE


def test_share_recipe_schema_rejection(tmp_path):
    s = SkillsMCPServer(None, feedback_dir=tmp_path)
    res, body = call_recipe(s, {**RECIPE, "title": "x" * 200})
    assert res["isError"] is True and body == {"status": "rejected", "errors": ["/title"]}
    res, body = call_recipe(s, {**RECIPE, "steps": RECIPE["steps"] * 16})
    assert body == {"status": "rejected", "errors": ["/steps"]}
    assert not (tmp_path / "_recipes").exists()


def test_client_cannot_send_server_fields(tmp_path):
    s = SkillsMCPServer(None, feedback_dir=tmp_path)
    _, body = call_recipe(s, {**RECIPE, "tenant": "x"})
    assert body == {"status": "rejected", "errors": ["/tenant"]}
    stamp = {"lexicon": "es-test.1", "masked": 0, "words": 1, "dropped": []}
    _, body = call_recipe(s, {**RECIPE, "masking": stamp})
    assert body == {"status": "rejected", "errors": ["/masking"]}
    assert not (tmp_path / "_recipes").exists()

    narrative = "El usuario pidió el resumen {?} de la reunión con el cliente y preparé el borrador."
    _, clean = call_recipe(s, {**RECIPE, "version": 3, "narrative": narrative.replace(" {?}", "")})
    _, body = call_recipe(s, {**RECIPE, "version": 4, "narrative": narrative})
    assert body["status"] == "recorded" and body["dropped"] == []
    assert body["masked"] == clean["masked"] + 1  # the client {?} counts as a masked token
    stored = json.loads(next(p for p in stored_files(tmp_path) if p.name == "v4.json").read_text())
    assert "{?}" in stored["narrative"] and stored["masking"]["masked"] == body["masked"]


def test_share_masks_before_store(tmp_path, monkeypatch):
    monkeypatch.setenv("NUVEL_FEEDBACK_URL", "https://relay.example/x")
    sent = []
    monkeypatch.setattr(server_mod, "_relay_feedback", lambda url, payload: sent.append(json.dumps(payload)) or {})
    s = SkillsMCPServer(None, feedback_dir=tmp_path)
    res, body = call_recipe(s, {**RECIPE, "narrative": SECRET_NARRATIVE})
    assert res["isError"] is False and body["status"] == "recorded"
    assert body["masked"] >= 2 and "/narrative" not in body["dropped"]
    [path] = stored_files(tmp_path)
    stored = path.read_text()
    assert not has_secret(stored) and "{?}" in json.loads(stored)["narrative"]
    assert len(sent) == 1 and not has_secret(sent[0])
    relayed = json.loads(sent[0])
    assert relayed["type"] == "recipe" and relayed["record"] == json.loads(stored)
    assert not has_secret(json.dumps(res)) and not has_secret(repr(vars(s)))


def test_share_unknown_language_drops_text(tmp_path):
    s = SkillsMCPServer(None, feedback_dir=tmp_path)
    res, body = call_recipe(s, {**RECIPE, "language": "pt"})
    assert res["isError"] is False and body["status"] == "recorded"
    assert sorted(body["dropped"]) == sorted(FREE_TEXT)
    assert body["masked"] == 0 and body["words"] == 0
    record = json.loads(stored_files(tmp_path)[0].read_text())
    assert record["title"] == "" and record["when_to_use"] == ""
    assert "narrative" not in record and record["pitfalls"] == []
    assert record["steps"] == [{"action": "draft", "tool_kind": "email", "slots": ["person"]}]
    assert record["masking"] == {"lexicon": None, "masked": 0, "words": 0, "dropped": body["dropped"]}


def test_invariant_hit_rejects(tmp_path, monkeypatch):
    def leaky(recipe, lexicon, abbreviations=None):
        out = json.loads(json.dumps(recipe))
        out["narrative"] = "Escribir a ana@acme.com"
        return out, {"lexicon": "es-test.1", "masked": 0, "words": 3, "dropped": []}

    monkeypatch.setattr(masking, "mask_recipe", leaky)
    s = SkillsMCPServer(None, feedback_dir=tmp_path)
    res, body = call_recipe(s, RECIPE)
    assert res["isError"] is True and body == {"status": "rejected", "errors": ["/narrative"]}
    assert "acme" not in json.dumps(res)
    assert not (tmp_path / "_recipes").exists()


def test_invariant_checks_dropped_pointers(tmp_path, monkeypatch):
    real = masking.mask_recipe

    def bad_pointer(recipe, lexicon, abbreviations=None):
        out, info = real(recipe, lexicon, abbreviations)
        return out, {**info, "dropped": ["/pedro"]}

    monkeypatch.setattr(masking, "mask_recipe", bad_pointer)
    s = SkillsMCPServer(None, feedback_dir=tmp_path)
    res, body = call_recipe(s, RECIPE)
    assert body == {"status": "rejected", "errors": ["/masking/dropped/0"]}
    assert not (tmp_path / "_recipes").exists()


def test_masked_output_revalidated(tmp_path, monkeypatch):
    real = masking.mask_recipe

    def lose_title(recipe, lexicon, abbreviations=None):
        out, info = real(recipe, lexicon, abbreviations)
        del out["title"]
        return out, info

    monkeypatch.setattr(masking, "mask_recipe", lose_title)
    s = SkillsMCPServer(None, feedback_dir=tmp_path)
    res, body = call_recipe(s, RECIPE)
    assert res["isError"] is True and body == {"status": "rejected", "errors": ["/title"]}
    assert not (tmp_path / "_recipes").exists()


def test_same_abbreviations_for_mask_and_invariant(tmp_path, monkeypatch):
    seen = []
    real_mask, real_hits = masking.mask_recipe, masking.invariant_hits
    monkeypatch.setattr(masking, "mask_recipe",
                        lambda r, lx, abbreviations=None: seen.append(abbreviations) or real_mask(r, lx, abbreviations))
    monkeypatch.setattr(masking, "invariant_hits",
                        lambda r, abbreviations=None, dropped=None: seen.append(abbreviations)
                        or real_hits(r, abbreviations, dropped=dropped))
    s = SkillsMCPServer(None, feedback_dir=tmp_path)
    narrative = "El CEO pidió el resumen de la reunión con el cliente y preparé el borrador."
    _, body = call_recipe(s, {**RECIPE, "narrative": narrative})
    assert body["status"] == "recorded"
    assert seen == [frozenset({"ceo"})] * 2
    assert "CEO" in json.loads(stored_files(tmp_path)[0].read_text())["narrative"]


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
    assert "tenant" in sig["description"]
    for word in ("nuvel.recipe/2", "structure", "slots", "notes", "narrative", "placeholder",
                 "masked", "server", "tenant", "masking", "duplicate"):
        assert word in rec["description"]
    assert "nuvel.recipe/1" not in rec["description"]
    props = rec["inputSchema"]["properties"]["recipe"]["properties"]
    assert "narrative" in props and "slots" in props["steps"]["items"]["properties"]


def test_tools_served_in_remote_mode(tmp_path):
    assert {"record_recipe_signal", "share_recipe"} <= HTTP_DEFAULT_TOOLS
    s = SkillsMCPServer(None, feedback_dir=tmp_path)
    s.enable_remote_mode()
    names = {t["name"] for t in s.handle_tools_list({})["tools"]}
    assert {"record_recipe_signal", "share_recipe"} <= names


class _Broken:
    def __init__(self, exc_type=OSError):
        self.exc_type = exc_type

    def append_signal(self, signal):
        raise self.exc_type("/secret/path: disk on fire pedro Acme")

    def put_recipe(self, recipe):
        raise self.exc_type("/secret/path: disk on fire pedro Acme")


def test_store_error_is_generic(tmp_path):
    s = SkillsMCPServer(None, feedback_dir=tmp_path)
    s.recipe_store = _Broken()
    for name, key, doc in (("record_recipe_signal", "signal", SIGNAL), ("share_recipe", "recipe", RECIPE)):
        with pytest.raises(server_mod.McpError) as exc:
            s.handle_tools_call({"name": name, "arguments": {key: doc}})
        assert "secret" not in str(exc.value) and "disk" not in str(exc.value)


def _rpc(s, recipe):
    return s.dispatch({"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                       "params": {"name": "share_recipe", "arguments": {"recipe": recipe}}})


def _raise_secret(*args, **kwargs):
    raise RuntimeError("pedro of Acme S.L. at " + json.dumps(args, default=str))


@pytest.mark.parametrize("case", ["store_oserror", "store_other_error", "relay_raises", "mask_raises",
                                  "lexicon_raises", "invariant_raises"])
def test_no_raw_text_on_failure_paths(tmp_path, monkeypatch, capsys, case):
    s = SkillsMCPServer(None, feedback_dir=tmp_path)
    if case == "store_oserror":
        s.recipe_store = _Broken(OSError)
    elif case == "store_other_error":
        s.recipe_store = _Broken(RuntimeError)
    elif case == "relay_raises":
        monkeypatch.setenv("NUVEL_FEEDBACK_URL", "https://relay.example/x")
        monkeypatch.setattr(server_mod, "_relay_feedback", _raise_secret)
    elif case == "mask_raises":
        monkeypatch.setattr(masking, "mask_recipe", _raise_secret)
    elif case == "lexicon_raises":
        monkeypatch.setattr(masking, "load_lexicon", _raise_secret)
    elif case == "invariant_raises":
        monkeypatch.setattr(masking, "invariant_hits", _raise_secret)
    messages = []
    monkeypatch.setattr(server_mod, "log", lambda *a: messages.append(" ".join(map(str, a))))

    response = _rpc(s, {**RECIPE, "narrative": SECRET_NARRATIVE})

    captured = capsys.readouterr()
    assert messages, "the failure must be logged"
    for text in [*messages, captured.out, captured.err, json.dumps(response)]:
        assert not has_secret(text), text
    if case == "relay_raises":
        assert json.loads(response["result"]["content"][0]["text"])["status"] == "recorded"
    else:
        assert response["error"]["message"] in (
            "Could not store the recipe", "Could not mask the recipe", "Internal error")
    if case in ("mask_raises", "lexicon_raises", "invariant_raises"):
        assert not (tmp_path / "_recipes").exists()
    assert not has_secret(repr(vars(s)))


def test_generic_internal_error_logs_type_only_for_share_recipe(monkeypatch):
    s = SkillsMCPServer(None)
    messages = []
    monkeypatch.setattr(server_mod, "log", lambda *a: messages.append(" ".join(map(str, a))))
    monkeypatch.setattr(s, "handle_tools_call", _raise_secret)
    s._methods["tools/call"] = s.handle_tools_call
    response = _rpc(s, RECIPE)
    assert response["error"]["message"] == "Internal error"
    assert messages == ["Internal error in tools/call (share_recipe): RuntimeError"]


def test_relay_after_local_write(tmp_path, monkeypatch):
    monkeypatch.setenv("NUVEL_FEEDBACK_URL", "https://relay.example/x")
    fake = _FakeUrlopen()
    monkeypatch.setattr(server_mod.urllib.request, "urlopen", fake)
    s = SkillsMCPServer(None, feedback_dir=tmp_path)
    call_signal(s, SIGNAL)
    call_recipe(s, RECIPE)
    call_signal(s, SIGNAL)  # duplicate: not relayed
    call_recipe(s, RECIPE)  # duplicate: not relayed
    sent = [json.loads(r.data) for r in fake.requests]
    assert [p["type"] for p in sent] == ["recipe_signal", "recipe"]
    assert sent[0]["record"]["signal_id"] == UUID_A and sent[1]["record"]["recipe_id"] == UUID_B
    assert sent[1]["record"] == json.loads(stored_files(tmp_path)[0].read_text())
    assert "masking" in sent[1]["record"]


def test_relay_failure_swallowed(tmp_path, monkeypatch):
    monkeypatch.setenv("NUVEL_FEEDBACK_URL", "https://relay.example/x")
    monkeypatch.setattr(server_mod.urllib.request, "urlopen", _FakeUrlopen(exc=OSError("down")))
    s = SkillsMCPServer(None, feedback_dir=tmp_path)
    res, body = call_recipe(s, RECIPE)
    assert res["isError"] is False and body["status"] == "recorded"
    res, body = call_signal(s, SIGNAL)
    assert res["isError"] is False and body["status"] == "recorded"


@pytest.mark.parametrize("mutate,expected", [
    (lambda r: r.update({"pedro": 1}), ["/*"]),
    (lambda r: r["steps"][0].update({"garcia-lopez": 1}), ["/steps/0/*"]),
    (lambda r: r["plugin"].update({"acme.sl": 1}), ["/plugin/*"]),
    (lambda r: r.update({"pedro": 1, "ana:612345678": 2}), ["/*"]),
])
def test_share_rejection_never_echoes_client_keys(tmp_path, mutate, expected):
    recipe = json.loads(json.dumps(RECIPE))
    mutate(recipe)
    s = SkillsMCPServer(None, feedback_dir=tmp_path)
    response = _rpc(s, recipe)
    body = json.loads(response["result"]["content"][0]["text"])
    assert body == {"status": "rejected", "errors": expected}
    text = json.dumps(response)
    for word in ("pedro", "garcia", "acme", "612345678"):
        assert word not in text
    assert not (tmp_path / "_recipes").exists()


@pytest.mark.parametrize("mutate,expected", [
    (lambda d: d.update({"pedro": 1}), ["/*"]),
    (lambda d: d["plugin"].update({"acme.sl": 1}), ["/plugin/*"]),
])
def test_signal_rejection_never_echoes_client_keys(tmp_path, mutate, expected):
    signal = json.loads(json.dumps(SIGNAL))
    mutate(signal)
    s = SkillsMCPServer(None, feedback_dir=tmp_path)
    res, body = call_signal(s, signal)
    assert res["isError"] is True and body == {"status": "rejected", "errors": expected}
    assert "pedro" not in json.dumps(res) and "acme" not in json.dumps(res)


def test_known_paths_kept_in_rejections(tmp_path):
    s = SkillsMCPServer(None, feedback_dir=tmp_path)
    bad = {**RECIPE, "steps": [{**RECIPE["steps"][0], "slots": ["person", "pedro"]}], "title": "x" * 81}
    _, body = call_recipe(s, bad)
    assert body["errors"] == ["/title", "/steps/0/slots/1"]


def test_stdio_serve_survives_any_parse_error(capsys):
    s = SkillsMCPServer(None)
    huge = '{"jsonrpc":"2.0","id":1,"method":"pedro","params":{"n":' + "9" * 5000 + "}}"
    lines = [huge, '{"pedro garcia": ', json.dumps({"jsonrpc": "2.0", "id": 2, "method": "ping"})]
    out = io.StringIO()
    s.serve(io.StringIO("\n".join(lines) + "\n"), out)
    responses = [json.loads(line) for line in out.getvalue().splitlines()]
    assert [r["error"]["message"] for r in responses[:2]] == ["Parse error", "Parse error"]
    assert all(r["id"] is None for r in responses[:2])
    assert len(responses) == 3 and responses[2]["id"] == 2
    err = capsys.readouterr().err
    assert "pedro" not in err and "999" not in err
