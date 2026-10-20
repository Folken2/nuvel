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
