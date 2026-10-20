import pytest


def test_nuvel_mcp_and_nuvel_cli_share_flags():
    import argparse
    import pytest
    pytest.importorskip("nuvel.commands.mcp_serve")
    from nuvel_mcp.cli import add_serve_arguments
    a = argparse.ArgumentParser(); add_serve_arguments(a)
    from nuvel.commands import mcp_serve
    root = argparse.ArgumentParser(); mcp_serve.register(root.add_subparsers())
    serve = root._subparsers._group_actions[0].choices["mcp"]._subparsers._group_actions[0].choices["serve"]
    assert {x.dest for x in a._actions} == {x.dest for x in serve._actions}

def test_module_entrypoint_runs_help(capsys):
    from nuvel_mcp.cli import main
    import pytest
    with pytest.raises(SystemExit) as e: main(["serve", "--help"])
    assert e.value.code == 0 and "--feedback-dir" in capsys.readouterr().out


def test_http_transport_without_extra_exits_1(monkeypatch, capsys):
    import sys
    import nuvel_mcp
    from nuvel_mcp.cli import main
    monkeypatch.setitem(sys.modules, "mcp", None)  # import mcp -> ImportError
    monkeypatch.delitem(sys.modules, "nuvel_mcp.http", raising=False)
    monkeypatch.delattr(nuvel_mcp, "http", raising=False)
    assert main(["serve", "--transport", "http"]) == 1
    assert 'pip install "nuvel-mcp[http]"' in capsys.readouterr().err


def test_stdio_path_never_imports_the_mcp_sdk():
    import subprocess
    import sys
    code = (
        "import sys, io\n"
        "from nuvel_mcp.cli import main\n"
        "sys.stdin = io.StringIO('')\n"
        "assert main(['serve']) == 0\n"
        "assert 'mcp' not in sys.modules, 'stdio imported the mcp SDK'\n"
    )
    res = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr


def test_http_transport_unrelated_import_error_names_the_module(monkeypatch, capsys):
    from nuvel_mcp import cli

    def broken():
        raise ModuleNotFoundError("No module named 'some_other_dep'", name="some_other_dep")

    monkeypatch.setattr(cli, "_import_http_transport", broken)
    assert cli.main(["serve", "--transport", "http"]) == 1
    err = capsys.readouterr().err
    assert "some_other_dep" in err and "nuvel-mcp[http]" not in err


@pytest.mark.parametrize("missing", ["mcp", "mcp.server.lowlevel", "starlette", "uvicorn",
                                     "anyio", "httpx", "sse_starlette"])
def test_http_transport_missing_sdk_dep_prints_the_hint(monkeypatch, capsys, missing):
    from nuvel_mcp import cli

    def broken():
        raise ModuleNotFoundError(f"No module named {missing!r}", name=missing)

    monkeypatch.setattr(cli, "_import_http_transport", broken)
    assert cli.main(["serve", "--transport", "http"]) == 1
    assert 'pip install "nuvel-mcp[http]"' in capsys.readouterr().err


def test_http_extra_pins_the_verified_sdk_floor():
    import tomllib
    from pathlib import Path
    data = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())
    assert data["project"]["optional-dependencies"]["http"] == ["mcp>=1.30,<2"]
