"""CLI-level tests for the ``nuvel bots`` subcommand.

These exercise the argparse wiring, dispatch routing, error handling and output
formatting in :mod:`nuvel.bots.cli` directly via ``parse_args`` (no subprocess).
``BotClient`` is mocked at the ``nuvel.bots.cli`` module level so no real hermes
CLI is ever invoked.
"""
from unittest.mock import MagicMock, patch

import argparse

import pytest

from nuvel.bots.cli import (
    _cmd_chat,
    _cmd_create,
    _cmd_delete,
    _cmd_info,
    _cmd_list,
    _cmd_logs,
    _cmd_send,
    _dispatch,
    _resolve_hermes_bin,
    register,
)
from nuvel.bots.errors import BotError, BotNotFoundError
from nuvel.bots.types import Bot, BotMessage


@pytest.fixture
def parser():
    """A top-level parser with the ``bots`` subcommand tree registered."""
    parent = argparse.ArgumentParser()
    sub = parent.add_subparsers()
    register(sub)
    return parent


# --------------------------------------------------------------------------- #
# 1. argparse wiring — every subcommand parses without error
# --------------------------------------------------------------------------- #
class TestArgparseWiring:
    @pytest.mark.parametrize(
        "argv, command, func",
        [
            (["bots", "list"], "list", _cmd_list),
            (["bots", "create", "newbot"], "create", _cmd_create),
            (["bots", "delete", "oldbot"], "delete", _cmd_delete),
            (["bots", "chat", "research", "hi"], "chat", _cmd_chat),
            (["bots", "info", "research"], "info", _cmd_info),
            (["bots", "logs", "research"], "logs", _cmd_logs),
            (["bots", "send", "a", "b", "msg"], "send", _cmd_send),
        ],
    )
    def test_subcommand_parses(self, parser, argv, command, func):
        args = parser.parse_args(argv)
        assert args.bots_command == command
        assert args._bots_func is func
        assert args.func is _dispatch

    def test_bots_requires_subcommand(self, parser):
        with pytest.raises(SystemExit):
            parser.parse_args(["bots"])

    def test_create_full_flags(self, parser):
        args = parser.parse_args(
            [
                "bots",
                "create",
                "clone1",
                "--model",
                "x/y",
                "--description",
                "does things",
                "--clone-from",
                "default",
            ]
        )
        assert args.name == "clone1"
        assert args.model == "x/y"
        assert args.description == "does things"
        assert args.clone_from == "default"

    def test_create_defaults(self, parser):
        args = parser.parse_args(["bots", "create", "plain"])
        assert args.model is None
        assert args.description == ""
        assert args.clone_from is None

    def test_logs_limit_flag(self, parser):
        assert parser.parse_args(["bots", "logs", "r"]).limit == 10
        assert parser.parse_args(["bots", "logs", "r", "-n", "5"]).limit == 5
        assert parser.parse_args(["bots", "logs", "r", "--limit", "42"]).limit == 42

    def test_send_positional_dests(self, parser):
        args = parser.parse_args(["bots", "send", "research", "writer", "draft"])
        assert args.from_bot == "research"
        assert args.to_bot == "writer"
        assert args.message == "draft"

    def test_common_flags(self, parser):
        args = parser.parse_args(["bots", "list", "--json", "-v"])
        assert args.json is True
        assert args.verbose is True
        args = parser.parse_args(["bots", "list"])
        assert args.json is False
        assert args.verbose is False


# --------------------------------------------------------------------------- #
# 2. --hermes-bin resolution
# --------------------------------------------------------------------------- #
class TestHermesBinResolution:
    def test_explicit_wins(self):
        assert _resolve_hermes_bin("/custom/hermes") == "/custom/hermes"

    def test_path_lookup(self):
        with patch("nuvel.bots.cli.shutil.which", return_value="/usr/bin/hermes"):
            assert _resolve_hermes_bin(None) == "/usr/bin/hermes"

    def test_falls_back_to_bare_name(self):
        with patch("nuvel.bots.cli.shutil.which", return_value=None), patch(
            "nuvel.bots.cli.Path.is_file", return_value=False
        ):
            assert _resolve_hermes_bin(None) == "hermes"

    @patch("nuvel.bots.cli.BotClient")
    def test_override_flows_into_client(self, mock_client, parser):
        mock_client.return_value.list_bots.return_value = []
        args = parser.parse_args(["bots", "list", "--hermes-bin", "/custom/hermes"])
        assert _dispatch(args) == 0
        mock_client.assert_called_once_with(hermes_bin="/custom/hermes")


# --------------------------------------------------------------------------- #
# 3. dispatch routing + full mock integration (parse → dispatch → method → out)
# --------------------------------------------------------------------------- #
class TestListCommand:
    @patch("nuvel.bots.cli.BotClient")
    def test_list_empty(self, mock_client, parser, capsys):
        mock_client.return_value.list_bots.return_value = []
        args = parser.parse_args(["bots", "list"])
        assert _dispatch(args) == 0
        assert "No bots found." in capsys.readouterr().out

    @patch("nuvel.bots.cli.BotClient")
    def test_list_with_bots(self, mock_client, parser, capsys):
        mock_client.return_value.list_bots.return_value = [
            Bot(name="default", model="deepseek/deepseek-v4-flash"),
            Bot(name="research", model=None),
        ]
        args = parser.parse_args(["bots", "list"])
        assert _dispatch(args) == 0
        out = capsys.readouterr().out
        assert "default" in out
        assert "deepseek/deepseek-v4-flash" in out
        assert "—" in out  # model-less bot rendered with em dash

    @patch("nuvel.bots.cli.BotClient")
    def test_list_json(self, mock_client, parser, capsys):
        mock_client.return_value.list_bots.return_value = [Bot(name="default")]
        args = parser.parse_args(["bots", "list", "--json"])
        assert _dispatch(args) == 0
        import json

        payload = json.loads(capsys.readouterr().out)
        assert payload[0]["name"] == "default"


class TestCreateCommand:
    @patch("nuvel.bots.cli.BotClient")
    def test_create_basic(self, mock_client, parser, capsys):
        mock_client.return_value.create_bot.return_value = Bot(name="newbot")
        args = parser.parse_args(["bots", "create", "newbot"])
        assert _dispatch(args) == 0
        assert "Created bot 'newbot'." in capsys.readouterr().out
        mock_client.return_value.create_bot.assert_called_once_with(
            "newbot", description="", model=None, clone_from=None
        )

    @patch("nuvel.bots.cli.BotClient")
    def test_create_with_all_flags(self, mock_client, parser):
        mock_client.return_value.create_bot.return_value = Bot(name="clone1")
        args = parser.parse_args(
            [
                "bots",
                "create",
                "clone1",
                "--model",
                "x/y",
                "--description",
                "d",
                "--clone-from",
                "default",
            ]
        )
        assert _dispatch(args) == 0
        mock_client.return_value.create_bot.assert_called_once_with(
            "clone1", description="d", model="x/y", clone_from="default"
        )


class TestDeleteCommand:
    @patch("nuvel.bots.cli.BotClient")
    def test_delete_normal(self, mock_client, parser, capsys):
        args = parser.parse_args(["bots", "delete", "oldbot"])
        assert _dispatch(args) == 0
        assert "Deleted bot 'oldbot'." in capsys.readouterr().out
        mock_client.return_value.delete_bot.assert_called_once_with("oldbot")

    @patch("nuvel.bots.cli.BotClient")
    def test_delete_wrong_name(self, mock_client, parser, capsys):
        mock_client.return_value.delete_bot.side_effect = BotNotFoundError("nope")
        args = parser.parse_args(["bots", "delete", "nope"])
        assert _dispatch(args) == 1
        assert "bot not found" in capsys.readouterr().err


class TestChatCommand:
    @patch("nuvel.bots.cli.BotClient")
    def test_chat_normal(self, mock_client, parser, capsys):
        mock_client.return_value.chat.return_value = BotMessage(
            bot="research", content="Hello there"
        )
        args = parser.parse_args(["bots", "chat", "research", "hi"])
        assert _dispatch(args) == 0
        assert "Hello there" in capsys.readouterr().out
        mock_client.return_value.chat.assert_called_once_with("research", "hi")

    @patch("nuvel.bots.cli.BotClient")
    def test_chat_error(self, mock_client, parser, capsys):
        mock_client.return_value.chat.side_effect = BotError("cli exploded")
        args = parser.parse_args(["bots", "chat", "research", "hi"])
        assert _dispatch(args) == 1
        assert "cli exploded" in capsys.readouterr().err


class TestInfoCommand:
    @patch("nuvel.bots.cli.BotClient")
    def test_info_normal(self, mock_client, parser, capsys):
        mock_client.return_value.get_bot_info.return_value = Bot(
            name="research", model="x/y", description="a research bot"
        )
        args = parser.parse_args(["bots", "info", "research"])
        assert _dispatch(args) == 0
        out = capsys.readouterr().out
        assert "research" in out
        assert "a research bot" in out

    @patch("nuvel.bots.cli.BotClient")
    def test_info_not_found(self, mock_client, parser, capsys):
        mock_client.return_value.get_bot_info.side_effect = BotNotFoundError("ghost")
        args = parser.parse_args(["bots", "info", "ghost"])
        assert _dispatch(args) == 1
        assert "bot not found" in capsys.readouterr().err


class TestLogsCommand:
    @patch("nuvel.bots.cli.BotClient")
    def test_logs_normal(self, mock_client, parser, capsys):
        mock_client.return_value.get_bot_logs.return_value = ["line1", "line2"]
        args = parser.parse_args(["bots", "logs", "research"])
        assert _dispatch(args) == 0
        out = capsys.readouterr().out
        assert "line1" in out and "line2" in out
        mock_client.return_value.get_bot_logs.assert_called_once_with(
            "research", limit=10
        )

    @patch("nuvel.bots.cli.BotClient")
    def test_logs_limit_flag(self, mock_client, parser):
        mock_client.return_value.get_bot_logs.return_value = []
        args = parser.parse_args(["bots", "logs", "research", "-n", "3"])
        assert _dispatch(args) == 0
        mock_client.return_value.get_bot_logs.assert_called_once_with(
            "research", limit=3
        )

    @patch("nuvel.bots.cli.BotClient")
    def test_logs_empty(self, mock_client, parser, capsys):
        mock_client.return_value.get_bot_logs.return_value = []
        args = parser.parse_args(["bots", "logs", "research"])
        assert _dispatch(args) == 0
        assert "(no logs)" in capsys.readouterr().out


class TestSendCommand:
    @patch("nuvel.bots.cli.BotClient")
    def test_send_normal(self, mock_client, parser, capsys):
        mock_client.return_value.chat_to_bot.return_value = BotMessage(
            bot="writer", content="got it"
        )
        args = parser.parse_args(["bots", "send", "research", "writer", "draft"])
        assert _dispatch(args) == 0
        assert "got it" in capsys.readouterr().out
        mock_client.return_value.chat_to_bot.assert_called_once_with(
            "research", "writer", "draft"
        )


# --------------------------------------------------------------------------- #
# 4. centralised error handling in _dispatch
# --------------------------------------------------------------------------- #
class TestErrorHandling:
    def _args(self, func, verbose=False):
        return argparse.Namespace(_bots_func=func, verbose=verbose)

    def test_bot_not_found_exit_1_clean(self, capsys):
        def boom(args):
            raise BotNotFoundError("missing")

        assert _dispatch(self._args(boom)) == 1
        err = capsys.readouterr().err
        assert "bot not found: missing" in err
        assert "Traceback" not in err

    def test_bot_error_exit_1_clean(self, capsys):
        def boom(args):
            raise BotError("kaboom")

        assert _dispatch(self._args(boom)) == 1
        err = capsys.readouterr().err
        assert "Error: kaboom" in err
        assert "Traceback" not in err

    def test_bot_error_verbose_shows_traceback(self, capsys):
        def boom(args):
            raise BotError("kaboom")

        assert _dispatch(self._args(boom, verbose=True)) == 1
        assert "Traceback" in capsys.readouterr().err

    def test_non_bot_error_propagates(self):
        def boom(args):
            raise ValueError("not a bot error")

        with pytest.raises(ValueError):
            _dispatch(self._args(boom))
