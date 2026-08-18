"""Unit tests for nuvel.bots.BotClient — all Hermes CLI calls are mocked."""
from unittest.mock import MagicMock, patch

import pytest

from nuvel.bots import Bot, BotClient, BotMessage
from nuvel.bots.errors import BotCLIError, BotNotFoundError


def _proc(stdout="", returncode=0, stderr=""):
    return MagicMock(returncode=returncode, stdout=stdout, stderr=stderr)


class TestBotClient:
    def setup_method(self):
        self.client = BotClient(hermes_bin="/fake/hermes")

    # ---- list -------------------------------------------------------- #
    @patch("subprocess.run")
    def test_list_bots_plain(self, mock_run):
        mock_run.return_value = _proc(stdout="default\nresearch\nwriter\n")
        bots = self.client.list_bots()
        assert len(bots) == 3
        assert bots[0].name == "default"
        assert [b.name for b in bots] == ["default", "research", "writer"]

    @patch("subprocess.run")
    def test_list_bots_table_with_marker_and_model(self, mock_run):
        table = (
            " Profile          Model                        Gateway      Alias\n"
            " ───────────────  ───────────────────────────  ───────────  ─────\n"
            " ◆default         deepseek/deepseek-v4-flash   running      —\n"
            " research         anthropic/claude-sonnet-4    stopped      —\n"
        )
        mock_run.return_value = _proc(stdout=table)
        bots = self.client.list_bots()
        assert [b.name for b in bots] == ["default", "research"]
        assert bots[0].model == "deepseek/deepseek-v4-flash"
        assert bots[1].model == "anthropic/claude-sonnet-4"

    @patch("subprocess.run")
    def test_list_bots_is_cached(self, mock_run):
        mock_run.return_value = _proc(stdout="default\n")
        self.client.list_bots()
        self.client.list_bots()
        assert mock_run.call_count == 1  # second call served from cache

    # ---- chat -------------------------------------------------------- #
    @patch("subprocess.run")
    def test_chat(self, mock_run):
        mock_run.return_value = _proc(stdout="Hello, I'm the research bot. How can I help?")
        msg = self.client.chat("research", "What's the weather?")
        assert isinstance(msg, BotMessage)
        assert "research" in msg.bot
        assert "Hello" in msg.content
        args = mock_run.call_args.args[0]
        assert args[:5] == ["/fake/hermes", "-p", "research", "chat", "-Q"]
        assert "What's the weather?" in args  # passed as a literal arg, unquoted

    @patch("subprocess.run")
    def test_chat_with_session(self, mock_run):
        mock_run.return_value = _proc(stdout="ok")
        self.client.chat("research", "hi", session="Standup")
        args = mock_run.call_args.args[0]
        assert "-c" in args and "Standup" in args

    @patch("subprocess.run")
    def test_chat_to_bot(self, mock_run):
        mock_run.return_value = _proc(stdout="got it")
        msg = self.client.chat_to_bot("research", "writer", "draft the intro")
        assert msg.bot == "writer"
        assert msg.session == "Agent Inbox"
        args = mock_run.call_args.args[0]
        assert args[:6] == ["/fake/hermes", "-p", "writer", "chat", "-c", "Agent Inbox"]
        assert "Message from research: draft the intro" in args

    # ---- lifecycle --------------------------------------------------- #
    @patch("subprocess.run")
    def test_create_bot(self, mock_run):
        mock_run.return_value = _proc(stdout="created")
        bot = self.client.create_bot("newbot", title="New", description="does things")
        assert isinstance(bot, Bot)
        assert bot.name == "newbot"
        assert bot.title == "New"
        args = mock_run.call_args.args[0]
        assert args[:4] == ["/fake/hermes", "profile", "create", "newbot"]
        assert "--description" in args

    @patch("subprocess.run")
    def test_create_bot_with_clone_and_model(self, mock_run):
        mock_run.return_value = _proc(stdout="ok")
        self.client.create_bot("clone1", clone_from="default", model="x/y")
        calls = [c.args[0] for c in mock_run.call_args_list]
        create_call = calls[0]
        assert "--clone-from" in create_call and "default" in create_call
        # a second call sets the model via scoped config
        model_call = calls[1]
        assert model_call[:3] == ["/fake/hermes", "-p", "clone1"]
        assert model_call[3:] == ["config", "set", "model.default", "x/y"]

    @patch("subprocess.run")
    def test_delete_bot(self, mock_run):
        mock_run.return_value = _proc(stdout="deleted")
        self.client.delete_bot("oldbot")
        args = mock_run.call_args.args[0]
        assert args == ["/fake/hermes", "profile", "delete", "oldbot", "-y"]

    @patch("subprocess.run")
    def test_get_bot_info(self, mock_run):
        show = (
            "Profile: research\n"
            "Path:    /home/x\n"
            "Model:   deepseek/deepseek-v4-flash (openrouter)\n"
            "Gateway: running\n"
        )
        # get_bot_info runs `profile show` then `profile describe`.
        mock_run.side_effect = [
            _proc(stdout=show),
            _proc(stdout="A research assistant."),
        ]
        bot = self.client.get_bot_info("research")
        assert bot.name == "research"
        assert bot.model == "deepseek/deepseek-v4-flash"  # provider stripped
        assert bot.description == "A research assistant."

    @patch("subprocess.run")
    def test_edit_bot_config(self, mock_run):
        mock_run.side_effect = [
            _proc(stdout="ok"),  # config set model
            _proc(stdout="ok"),  # profile describe --text
            _proc(stdout="Profile: research\nModel: x/y (openrouter)\n"),  # show
            _proc(stdout="new desc"),  # describe read
        ]
        bot = self.client.edit_bot_config("research", model="x/y", description="new desc")
        assert bot.model == "x/y"

    # ---- logs -------------------------------------------------------- #
    @patch("subprocess.run")
    def test_get_bot_logs(self, mock_run):
        mock_run.return_value = _proc(stdout="line1\nline2\nline3")
        logs = self.client.get_bot_logs("research", limit=3)
        assert logs == ["line1", "line2", "line3"]
        args = mock_run.call_args.args[0]
        assert args == ["/fake/hermes", "-p", "research", "logs", "agent", "-n", "3"]

    @patch("subprocess.run")
    def test_get_bot_logs_empty(self, mock_run):
        mock_run.return_value = _proc(stdout="")
        assert self.client.get_bot_logs("research") == []

    # ---- errors ------------------------------------------------------ #
    @patch("subprocess.run")
    def test_bot_not_found(self, mock_run):
        mock_run.return_value = _proc(
            returncode=1, stderr="Error: profile 'nonexistent' not found"
        )
        with pytest.raises(BotNotFoundError):
            self.client.chat("nonexistent", "hi")

    @patch("subprocess.run")
    def test_generic_cli_error(self, mock_run):
        mock_run.return_value = _proc(returncode=2, stderr="boom")
        with pytest.raises(BotCLIError):
            self.client.chat("research", "hi")

    @patch("subprocess.run")
    def test_binary_missing(self, mock_run):
        mock_run.side_effect = FileNotFoundError()
        with pytest.raises(BotCLIError):
            self.client.list_bots()

    @patch("subprocess.run")
    def test_timeout(self, mock_run):
        import subprocess as _sp

        mock_run.side_effect = _sp.TimeoutExpired(cmd="hermes", timeout=30)
        with pytest.raises(BotCLIError):
            self.client.list_bots()

    # ---- input validation (security boundary) ------------------------ #
    @patch("subprocess.run")
    def test_invalid_name_rejected(self, mock_run):
        with pytest.raises(BotCLIError):
            self.client.chat("../etc/passwd", "hi")
        with pytest.raises(BotCLIError):
            self.client.delete_bot("bad name")
        mock_run.assert_not_called()  # never reached the CLI

    @patch("subprocess.run")
    def test_hermes_home_exported(self, mock_run):
        mock_run.return_value = _proc(stdout="default\n")
        client = BotClient(hermes_bin="/fake/hermes", hermes_home="/tmp/hh")
        client.list_bots()
        env = mock_run.call_args.kwargs["env"]
        assert env["HERMES_HOME"] == "/tmp/hh"
