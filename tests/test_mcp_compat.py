import pytest

pytestmark = pytest.mark.filterwarnings("ignore:nuvel.mcp is deprecated:DeprecationWarning")


def test_shim_aliases_real_modules():
    import nuvel_mcp.server, nuvel.mcp.server
    assert nuvel.mcp.server is nuvel_mcp.server
    from nuvel.mcp import feedback, skills_loader
    import nuvel_mcp.feedback, nuvel_mcp.skills_loader
    assert feedback is nuvel_mcp.feedback and skills_loader is nuvel_mcp.skills_loader


def test_shim_aliases_http_transport_and_oauth():
    import nuvel.mcp.http_transport, nuvel.mcp.oauth
    import nuvel_mcp.http_transport, nuvel_mcp.oauth
    assert nuvel.mcp.http_transport is nuvel_mcp.http_transport
    assert nuvel.mcp.oauth is nuvel_mcp.oauth

_SHIM_SUBMODULES = ("server", "feedback", "skills_loader", "installer", "http_transport", "oauth")


def _forget_shim(monkeypatch):
    """Drop nuvel.mcp and its aliased submodules (restored after the test)."""
    import sys
    for name in ("nuvel.mcp", *(f"nuvel.mcp.{m}" for m in _SHIM_SUBMODULES)):
        monkeypatch.delitem(sys.modules, name, raising=False)


def test_shim_public_names_and_warning(monkeypatch):
    import importlib
    import warnings
    _forget_shim(monkeypatch)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        mod = importlib.import_module("nuvel.mcp")
    assert mod.SkillsMCPServer is __import__("nuvel_mcp").SkillsMCPServer
    assert any(issubclass(w.category, DeprecationWarning) and "nuvel_mcp" in str(w.message) for w in caught)


def test_shim_warning_points_at_the_importing_frame(monkeypatch):
    import warnings
    _forget_shim(monkeypatch)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        import nuvel.mcp  # noqa: F401
    (warning,) = [w for w in caught if issubclass(w.category, DeprecationWarning)
                  and "nuvel_mcp" in str(w.message)]
    assert warning.filename == __file__


def test_monkeypatch_through_shim_hits_real_module(monkeypatch):
    import nuvel.mcp.server as shim_server, nuvel_mcp.server as real
    monkeypatch.setattr(shim_server, "GITHUB_ISSUES_URL", "https://example.invalid")
    assert real.GITHUB_ISSUES_URL == "https://example.invalid"
