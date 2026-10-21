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

def test_shim_public_names_and_warning(recwarn):
    import importlib, sys
    sys.modules.pop("nuvel.mcp", None)
    mod = importlib.import_module("nuvel.mcp")
    assert mod.SkillsMCPServer is __import__("nuvel_mcp").SkillsMCPServer
    assert any(issubclass(w.category, DeprecationWarning) and "nuvel_mcp" in str(w.message) for w in recwarn)

def test_monkeypatch_through_shim_hits_real_module(monkeypatch):
    import nuvel.mcp.server as shim_server, nuvel_mcp.server as real
    monkeypatch.setattr(shim_server, "GITHUB_ISSUES_URL", "https://example.invalid")
    assert real.GITHUB_ISSUES_URL == "https://example.invalid"
