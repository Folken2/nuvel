import pytest

from nuvel_mcp import auth
from nuvel_mcp.auth import BearerAuthenticator, load_authenticator

ENV = {"NUVEL_MCP_TOKENS": "alice:tok-a,bob:tok-b:with-colon"}


def make():
    return BearerAuthenticator.from_env(ENV)


def test_valid_tokens_map_to_names():
    a = make()
    assert a.authenticate({"Authorization": "Bearer tok-a"}) == "alice"
    assert a.authenticate({"Authorization": "Bearer tok-b:with-colon"}) == "bob"


def test_header_and_scheme_case_insensitive():
    a = make()
    assert a.authenticate({"authorization": "bearer tok-a"}) == "alice"
    assert a.authenticate({"AUTHORIZATION": "BEARER tok-a"}) == "alice"


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer wrong"},
        {"Authorization": "Bearer "},
        {"Authorization": "Bearer"},
        {"Authorization": "Bearer  tok-a"},
        {"Authorization": "Basic tok-a"},
        {"Authorization": "tok-a"},
        {"Authorization": "Bearer tok-é"},
    ],
)
def test_rejected(headers):
    assert make().authenticate(headers) is None


@pytest.mark.parametrize(
    "value", ["alice", ":tok", "alice:", "  :  ", "alice:t,bob", "alice:t,,bob:u", ""]
)
def test_malformed_env_raises(value):
    with pytest.raises(ValueError):
        BearerAuthenticator.from_env({"NUVEL_MCP_TOKENS": value})


def test_missing_env_raises():
    with pytest.raises(ValueError, match="NUVEL_MCP_TOKENS"):
        BearerAuthenticator.from_env({})


def test_error_names_index_not_value():
    with pytest.raises(ValueError) as e:
        BearerAuthenticator.from_env({"NUVEL_MCP_TOKENS": "alice:s3cret,badentry-s3cret2"})
    msg = str(e.value)
    assert "2" in msg and "s3cret" not in msg and "badentry" not in msg


def test_duplicate_names_rejected_by_index():
    with pytest.raises(ValueError) as e:
        BearerAuthenticator.from_env({"NUVEL_MCP_TOKENS": "a:s1,b:s2,a:s3"})
    assert "3" in str(e.value) and "s1" not in str(e.value) and "s3" not in str(e.value)


def test_repr_has_no_token():
    a = make()
    assert "tok-a" not in repr(a) and "tok-b" not in repr(a)
    assert "tok-a" not in str(a)


def test_uses_compare_digest_for_every_token(monkeypatch):
    calls = []
    real = auth.hmac.compare_digest

    def spy(x, y):
        calls.append(1)
        return real(x, y)

    monkeypatch.setattr(auth.hmac, "compare_digest", spy)
    assert make().authenticate({"Authorization": "Bearer tok-a"}) == "alice"
    assert len(calls) == 2  # no short-circuit on first match


def test_load_authenticator(monkeypatch):
    assert load_authenticator(None) is None
    monkeypatch.setenv("NUVEL_MCP_TOKENS", "alice:tok")
    assert load_authenticator("bearer").authenticate({"Authorization": "Bearer tok"}) == "alice"
    monkeypatch.setenv("NUVEL_MCP_TOKENS", "")
    with pytest.raises(ValueError):
        load_authenticator("bearer")


def test_load_authenticator_factory(tmp_path, monkeypatch):
    (tmp_path / "my_auth_mod.py").write_text(
        "class A:\n    def authenticate(self, headers):\n        return 'x'\n"
        "def make():\n    return A()\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    assert load_authenticator("my_auth_mod:make").authenticate({}) == "x"


@pytest.mark.parametrize("spec", ["", "nope", "a:", ":b", "no_such_mod_xyz:f", "nuvel_mcp.auth:missing"])
def test_load_authenticator_bad_spec(spec):
    with pytest.raises(ValueError):
        load_authenticator(spec)


@pytest.mark.parametrize("body", [
    "def make():\n    return None\n",
    "def make():\n    return object()\n",
    "class A:\n    authenticate = 'not callable'\n"
    "def make():\n    return A()\n",
], ids=["none", "object", "non-callable"])
def test_load_authenticator_factory_must_return_an_authenticator(tmp_path, monkeypatch, body):
    (tmp_path / "bad_auth_mod.py").write_text(body)
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(__import__("sys").modules, "bad_auth_mod", raising=False)
    with pytest.raises(ValueError, match="authenticate"):
        load_authenticator("bad_auth_mod:make")
