"""``nuvel_mcp.auth`` is only the tenant context variable; OAuth lives in nuvel_mcp.oauth."""

import importlib

import pytest

from nuvel_mcp import auth


def test_auth_exposes_only_current_principal():
    public = {name for name in vars(auth) if not name.startswith("_")}
    # The context variable, plus the modules/annotations the file itself imports.
    assert "current_principal" in public
    assert not public & {"BearerAuthenticator", "load_authenticator", "Authenticator",
                         "TOKENS_ENV", "LOOPBACK_HOSTS"}
    assert public - {"current_principal", "annotations", "contextvars"} == set()


def test_current_principal_defaults_to_none_and_resets():
    assert auth.current_principal.get() is None
    token = auth.current_principal.set("client-1")
    assert auth.current_principal.get() == "client-1"
    auth.current_principal.reset(token)
    assert auth.current_principal.get() is None


def test_sdk_http_module_is_gone():
    with pytest.raises(ImportError):
        importlib.import_module("nuvel_mcp.http")
