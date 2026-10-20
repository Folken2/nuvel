"""serverInfo.version follows the installed nuvel-mcp distribution."""

from importlib import metadata

from nuvel_mcp import server as server_mod
from nuvel_mcp.server import SERVER_INFO, SkillsMCPServer


def test_server_info_version_is_the_package_version():
    assert SERVER_INFO["version"] == metadata.version("nuvel-mcp")
    init = SkillsMCPServer(None).handle_initialize({})
    assert init["serverInfo"]["version"] == metadata.version("nuvel-mcp")


def test_package_version_falls_back_when_not_installed(monkeypatch):
    def missing(name):
        raise metadata.PackageNotFoundError(name)

    monkeypatch.setattr(server_mod.metadata, "version", missing)
    assert server_mod._package_version() == "0+unknown"
