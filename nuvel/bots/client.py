"""``BotClient`` — a thin, typed wrapper over the Hermes CLI.

Design notes
------------
* **No shell.** Every call goes through :func:`subprocess.run` with an argument
  *list* and ``shell=False`` (the default). Arguments are handed straight to
  ``execve``, so a message like ``"; rm -rf /"`` is just a harmless string —
  there is no shell to interpret it. Profile *names* are additionally validated
  against :data:`~nuvel.bots.constants.NAME_RE` before they reach the CLI, which
  is the real trust boundary (a name is used to build paths/sessions).
* **Profile selection** uses the global ``-p <name>`` flag placed *before* the
  subcommand, e.g. ``hermes -p research chat -Q -q "hi"``.
* **Parsing** turns Hermes' human-readable tables into the dataclasses in
  :mod:`nuvel.bots.types`.
"""
from __future__ import annotations

import os
import re
import shlex
import subprocess
import time

from .constants import (
    AGENT_INBOX_SESSION,
    CHAT_TIMEOUT,
    DEFAULT_TIMEOUT,
    HERMES_BIN,
    HERMES_HOME_ENV,
    LIST_CACHE_TTL,
    MODEL_CONFIG_KEY,
    NAME_RE,
)
from .errors import BotCLIError, BotNotFoundError
from .types import Bot, BotMessage

# Substrings Hermes uses when it can't find a profile, matched case-insensitively
# against stderr so we can raise the precise BotNotFoundError.
_NOT_FOUND_HINTS = ("not found", "does not exist", "no such profile", "unknown profile")


class BotClient:
    """Create, inspect, delete, and converse with Hermes-backed bots.

    Parameters
    ----------
    hermes_bin:
        Path to (or name of) the ``hermes`` executable. Defaults to ``"hermes"``.
    hermes_home:
        When set, exported as ``HERMES_HOME`` for every call so the client is
        pinned to a specific Hermes install rather than the ambient one.
    """

    def __init__(self, hermes_bin: str = HERMES_BIN, hermes_home: str | None = None) -> None:
        self.hermes_bin = hermes_bin
        self.hermes_home = hermes_home
        # {"profiles": (timestamp, [Bot, ...])} — see _cached_list_output.
        self._cache: dict[str, tuple[float, str]] = {}

    # ------------------------------------------------------------------ #
    # low-level CLI plumbing
    # ------------------------------------------------------------------ #
    def _run_hermes(self, args: list[str], timeout: int = DEFAULT_TIMEOUT) -> str:
        """Run ``hermes <args>`` and return stripped stdout.

        Raises :class:`BotCLIError` on any non-zero exit, missing binary, or
        timeout, and :class:`BotNotFoundError` when stderr looks like a
        missing-profile error.
        """
        env = os.environ.copy()
        if self.hermes_home:
            env[HERMES_HOME_ENV] = self.hermes_home
        try:
            result = subprocess.run(
                [self.hermes_bin, *args],
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
            )
        except FileNotFoundError as exc:
            raise BotCLIError(f"hermes binary not found at '{self.hermes_bin}'") from exc
        except subprocess.TimeoutExpired as exc:
            # shlex.join gives a faithful, copy-pasteable rendering of the call.
            raise BotCLIError(
                f"hermes {shlex.join(args)} timed out after {timeout}s"
            ) from exc

        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            if any(hint in stderr.lower() for hint in _NOT_FOUND_HINTS):
                raise BotNotFoundError(stderr or "profile not found")
            raise BotCLIError(f"hermes {shlex.join(args)} failed: {stderr}")
        return (result.stdout or "").strip()

    @staticmethod
    def _validate_name(name: str) -> str:
        """Ensure ``name`` is a legal Hermes profile name (defence in depth)."""
        if not name or not NAME_RE.match(name):
            raise BotCLIError(
                f"invalid bot name {name!r}: must be lowercase alphanumeric, "
                "optionally with '-' or '_'"
            )
        return name

    # ------------------------------------------------------------------ #
    # listing / inspection
    # ------------------------------------------------------------------ #
    def _cached_list_output(self) -> str:
        """Return raw ``profile list`` stdout, memoised for ``LIST_CACHE_TTL``s."""
        cached = self._cache.get("profiles")
        if cached and (time.monotonic() - cached[0]) < LIST_CACHE_TTL:
            return cached[1]
        out = self._run_hermes(["profile", "list"])
        self._cache["profiles"] = (time.monotonic(), out)
        return out

    def list_bots(self) -> list[Bot]:
        """List all bots (Hermes profiles).

        Parses the ``hermes profile list`` table. The active-profile marker
        (``◆``) and column separators are stripped; the model column, when
        present, is carried onto the :class:`Bot`.
        """
        out = self._cached_list_output()
        bots: list[Bot] = []
        for line in out.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            # Skip the header and the box-drawing separator rows.
            if stripped.startswith("Profile") or re.match(r"^[─—\-\s]+$", stripped):
                continue
            cols = re.split(r"\s{2,}", stripped)
            name_match = re.search(r"[a-z0-9][a-z0-9_-]*", cols[0])
            if not name_match:
                continue
            name = name_match.group(0)
            model = None
            if len(cols) > 1 and cols[1] and cols[1] != "—":
                model = cols[1]
            bots.append(Bot(name=name, model=model))
        return bots

    def get_bot_info(self, bot_name: str) -> Bot:
        """Return a :class:`Bot` populated from ``hermes profile show``.

        The stored description (``hermes profile describe``) is folded in when
        one is set.
        """
        self._validate_name(bot_name)
        out = self._run_hermes(["profile", "show", bot_name])
        fields = self._parse_show(out)
        model = fields.get("model")
        if model:
            # "deepseek/... (openrouter)" -> drop the provider annotation.
            model = model.split(" (", 1)[0].strip() or None
        bot = Bot(name=fields.get("profile", bot_name), model=model)
        desc = self._read_description(bot_name)
        if desc:
            bot.description = desc
        return bot

    @staticmethod
    def _parse_show(out: str) -> dict[str, str]:
        """Parse the ``Key: value`` block emitted by ``profile show``."""
        fields: dict[str, str] = {}
        for line in out.splitlines():
            if ":" in line:
                key, _, value = line.partition(":")
                fields[key.strip().lower()] = value.strip()
        return fields

    def _read_description(self, bot_name: str) -> str:
        """Return the profile's stored description, or ``""`` if none."""
        out = self._run_hermes(["profile", "describe", bot_name])
        # Hermes prints "(no description set for 'x')" when empty.
        if out.startswith("(") and "no description" in out.lower():
            return ""
        return out

    # ------------------------------------------------------------------ #
    # lifecycle
    # ------------------------------------------------------------------ #
    def create_bot(
        self,
        name: str,
        title: str = "",
        description: str = "",
        model: str | None = None,
        clone_from: str | None = None,
    ) -> Bot:
        """Create a new bot (``hermes profile create``).

        ``clone_from`` seeds config/skills from an existing profile. ``model``
        is applied after creation via a scoped ``config set``. ``title`` has no
        Hermes equivalent and is retained only on the returned object.
        """
        self._validate_name(name)
        args = ["profile", "create", name]
        if clone_from:
            self._validate_name(clone_from)
            args += ["--clone-from", clone_from]
        if description:
            args += ["--description", description]
        self._run_hermes(args)
        self._cache.pop("profiles", None)  # invalidate the list cache

        if model:
            self._set_config(name, MODEL_CONFIG_KEY, model)
        return Bot(name=name, title=title, description=description, model=model)

    def create_bot_with_skills(
        self,
        name: str,
        skill_refs: list[str],
        title: str = "",
        description: str = "",
        model: str | None = None,
        clone_from: str | None = None,
    ) -> tuple[Bot, list["InstalledSkill"]]:
        """Create a bot and install hub skills into it in one call.

        Skills are resolved and copied by :class:`~nuvel.bots.skills.SkillManager`
        (hub auto-discovered / cloned as needed) into this client's Hermes home.
        Returns the new :class:`Bot` alongside the list of installed skills.
        """
        from .skills import SkillManager

        bot = self.create_bot(
            name,
            title=title,
            description=description,
            model=model,
            clone_from=clone_from,
        )
        mgr = SkillManager()
        installed = mgr.install_skills(name, skill_refs, hermes_home=self.hermes_home)
        bot.skills = [s.name for s in installed]
        return bot, installed

    def delete_bot(self, name: str) -> None:
        """Delete a bot (``hermes profile delete -y``)."""
        self._validate_name(name)
        self._run_hermes(["profile", "delete", name, "-y"])
        self._cache.pop("profiles", None)

    def edit_bot_config(
        self,
        bot_name: str,
        model: str | None = None,
        title: str | None = None,
        description: str | None = None,
    ) -> Bot:
        """Update a bot's model and/or description in place.

        ``title`` is accepted for API symmetry but not persisted (Hermes has no
        title field); it is reflected on the returned :class:`Bot`.
        """
        self._validate_name(bot_name)
        if model is not None:
            self._set_config(bot_name, MODEL_CONFIG_KEY, model)
        if description is not None:
            self._run_hermes(
                ["profile", "describe", bot_name, "--text", description]
            )
        self._cache.pop("profiles", None)
        bot = self.get_bot_info(bot_name)
        if title is not None:
            bot.title = title
        return bot

    def _set_config(self, bot_name: str, key: str, value: str) -> None:
        """Set a profile-scoped Hermes config value."""
        self._run_hermes(["-p", bot_name, "config", "set", key, value])

    # ------------------------------------------------------------------ #
    # conversation
    # ------------------------------------------------------------------ #
    def chat(
        self, bot_name: str, message: str, session: str | None = None, timeout: int = CHAT_TIMEOUT
    ) -> BotMessage:
        """Send a one-shot message to a bot and return its reply.

        Maps to ``hermes -p <bot> chat -Q -q <message>`` (``-Q`` = quiet, so
        only the final response is captured). When ``session`` is given, the
        exchange continues that named session (``-c <session>``).
        """
        self._validate_name(bot_name)
        args = ["-p", bot_name, "chat", "-Q"]
        if session:
            args += ["-c", session]
        args += ["-q", message]
        out = self._run_hermes(args, timeout=timeout)
        return BotMessage(bot=bot_name, content=out, session=session)

    def chat_to_bot(
        self, from_bot: str, to_bot: str, message: str, timeout: int = CHAT_TIMEOUT
    ) -> BotMessage:
        """Deliver a message from one bot to another's Agent Inbox.

        Maps to ``hermes -p <to> chat -c "Agent Inbox" -Q -q "Message from
        <from>: <message>"``.
        """
        self._validate_name(from_bot)
        self._validate_name(to_bot)
        payload = f"Message from {from_bot}: {message}"
        args = ["-p", to_bot, "chat", "-c", AGENT_INBOX_SESSION, "-Q", "-q", payload]
        out = self._run_hermes(args, timeout=timeout)
        return BotMessage(bot=to_bot, content=out, session=AGENT_INBOX_SESSION)

    # ------------------------------------------------------------------ #
    # logs
    # ------------------------------------------------------------------ #
    def get_bot_logs(self, bot_name: str, limit: int = 10) -> list[str]:
        """Return up to ``limit`` recent agent-log lines for a bot.

        Maps to ``hermes -p <bot> logs agent -n <limit>``.
        """
        self._validate_name(bot_name)
        out = self._run_hermes(["-p", bot_name, "logs", "agent", "-n", str(limit)])
        if not out:
            return []
        return out.splitlines()

    # ------------------------------------------------------------------ #
    # scheduled jobs (cron)
    # ------------------------------------------------------------------ #
    def create_cron_job(
        self, bot_name: str, schedule: str, task: str, name: str | None = None
    ) -> str:
        """Schedule a recurring task for a bot and return its job id.

        Maps to ``hermes -p <bot> cron create <schedule> <task> [--name <name>]``.
        The ``schedule`` is any expression Hermes accepts (``"30m"``, ``"every
        2h"`` or a 5-field crontab like ``"0 8 * * 1-5"``). The job id is parsed
        from the ``Created job: <id>`` line; ``""`` is returned if it is absent.
        """
        self._validate_name(bot_name)
        args = ["-p", bot_name, "cron", "create", schedule, task]
        if name:
            args += ["--name", name]
        out = self._run_hermes(args)
        for line in out.splitlines():
            stripped = line.strip()
            if stripped.lower().startswith("created job:"):
                return stripped.split(":", 1)[1].strip()
        return ""

    def remove_cron_job(self, bot_name: str, job_id: str) -> None:
        """Remove a scheduled job (``hermes -p <bot> cron remove <job_id>``).

        Cron jobs are profile-scoped, so removal needs the owning ``bot_name``.
        """
        self._validate_name(bot_name)
        self._run_hermes(["-p", bot_name, "cron", "remove", job_id])
