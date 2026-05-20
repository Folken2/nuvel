"""Tests for nuvel.dashboard.cli."""

from __future__ import annotations

from unittest.mock import patch

from nuvel.cli import build_parser


def test_dashboard_subcommand_is_registered() -> None:
    parser = build_parser()
    args = parser.parse_args(["dashboard", "--port", "9001"])
    assert args.command == "dashboard"
    assert args.port == 9001
    assert args.host == "127.0.0.1"
    assert args.demo is False


def test_dashboard_subcommand_accepts_demo_flag() -> None:
    parser = build_parser()
    args = parser.parse_args(["dashboard", "--demo"])
    assert args.demo is True


def test_dashboard_subcommand_collects_sources() -> None:
    parser = build_parser()
    args = parser.parse_args(["dashboard", "-s", "/a", "-s", "/b"])
    assert args.source == ["/a", "/b"]


def test_dashboard_launch_invokes_uvicorn_and_browser() -> None:
    from nuvel.dashboard.cli import _cmd_dashboard
    from argparse import Namespace
    args = Namespace(host="127.0.0.1", port=8765, source=None, demo=True, open_browser=True)

    with patch("nuvel.dashboard.cli.uvicorn.run") as run, \
         patch("nuvel.dashboard.cli.webbrowser.open") as browser, \
         patch("nuvel.dashboard.cli._port_in_use", return_value=False):
        rc = _cmd_dashboard(args)

    assert rc == 0
    assert run.called
    assert browser.called
    assert browser.call_args.args[0] == "http://127.0.0.1:8765"


def test_demo_flag_loads_bundled_fixtures(tmp_path) -> None:
    from argparse import Namespace
    from nuvel.dashboard.cli import _resolve_sources

    args = Namespace(demo=True, source=None)
    sources = _resolve_sources(args)
    assert len(sources) == 1
    assert sources[0].name == "fixtures"
    assert (sources[0] / "multi_agent.jsonl").is_file()
