import pytest


def _subcommands(parser):
    """{name: subparser} of ``parser``'s subcommand group."""
    import argparse
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action.choices
    return {}


def _surface(parser, prefix=""):
    """Every (command path, option strings) pair reachable from ``parser``."""
    import argparse
    out = set()
    for action in parser._actions:
        if action.option_strings:
            out.add((prefix, tuple(sorted(action.option_strings)), action.default))
        elif not isinstance(action, argparse._SubParsersAction):
            out.add((prefix, action.dest, None))
    for name, sub in _subcommands(parser).items():
        out |= _surface(sub, f"{prefix} {name}".strip())
    return out


def test_nuvel_mcp_and_nuvel_cli_share_flags_and_commands():
    import argparse
    import pytest
    pytest.importorskip("nuvel.commands.mcp_serve")
    from nuvel.commands import mcp_serve
    from nuvel_mcp import cli

    standalone = argparse.ArgumentParser()
    sub = standalone.add_subparsers()
    cli.add_serve_arguments(sub.add_parser("serve"))
    cli.add_oauth_commands(sub)
    root = argparse.ArgumentParser()
    mcp_serve.register(root.add_subparsers())
    nuvel_mcp_tree = _subcommands(root)["mcp"]

    assert set(_subcommands(nuvel_mcp_tree)) == {"serve", "client", "token"}
    assert _surface(standalone) == _surface(nuvel_mcp_tree)
    serve_flags = {f for _, opts, _ in _surface(standalone) if isinstance(opts, tuple) for f in opts}
    assert {"--transport", "--host", "--port", "--allow-remote", "--no-auth",
            "--require-auth", "--skills-dir", "--tools", "--trace-store"} <= serve_flags
    assert ("token revoke", "value", None) in _surface(standalone)
    assert ("client create", ("--name",), None) in _surface(standalone)


def test_http_defaults_match_the_shipped_transport():
    import argparse
    from nuvel_mcp.cli import add_serve_arguments
    p = argparse.ArgumentParser(); add_serve_arguments(p)
    args = p.parse_args([])
    assert (args.transport, args.host, args.port) == ("stdio", "127.0.0.1", 8080)
    assert args.skills_dir is None and not args.no_auth and not args.allow_remote


def test_transport_http_runs_the_stdlib_transport(monkeypatch, tmp_path):
    from nuvel_mcp import cli, http_transport
    calls = []
    monkeypatch.setattr(http_transport, "serve_http", lambda *a, **k: calls.append((a, k)))
    assert cli.main(["serve", "--transport", "http", "--no-auth",
                     "--feedback-dir", str(tmp_path)]) == 0
    (server, host, port, _config, provider), kwargs = calls[0]
    assert (host, port, provider, kwargs) == ("127.0.0.1", 8080, None, {"no_auth": True})
    assert server.loader is None  # --skills-dir omitted: feedback-only


def test_nuvel_mcp_token_command_runs_through_nuvel_cli(monkeypatch, tmp_path, capsys):
    import pytest
    nuvel_cli = pytest.importorskip("nuvel.cli")
    monkeypatch.setenv("NUVEL_MCP_OAUTH_STORE", str(tmp_path / "oauth.json"))
    assert nuvel_cli.main(["mcp", "token", "revoke", "nvat_unknown"]) == 1
    assert "no matching token" in capsys.readouterr().err


def test_module_entrypoint_runs_help(capsys):
    from nuvel_mcp.cli import main
    import pytest
    with pytest.raises(SystemExit) as e: main(["serve", "--help"])
    assert e.value.code == 0 and "--feedback-dir" in capsys.readouterr().out


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


def test_http_path_never_imports_the_mcp_sdk():
    import subprocess
    import sys
    code = (
        "import sys\n"
        "from nuvel_mcp import cli, http_transport\n"
        "http_transport.serve_http = lambda *a, **k: None\n"
        "assert cli.main(['serve', '--transport', 'http', '--no-auth']) == 0\n"
        "assert 'mcp' not in sys.modules, 'http imported the mcp SDK'\n"
    )
    res = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr


@pytest.mark.parametrize("flag", [["--auth", "bearer"], ["--allowed-host", "skills.example.com"]])
def test_removed_sdk_transport_flags_are_rejected_by_argparse(flag, capsys):
    from nuvel_mcp.cli import main
    with pytest.raises(SystemExit) as e:
        main(["serve", *flag])
    assert e.value.code == 2
    assert "unrecognized arguments" in capsys.readouterr().err
