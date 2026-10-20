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
