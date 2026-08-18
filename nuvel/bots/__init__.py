"""nuvel.bots — programmatic Hermes "bots" (a bot *is* a Hermes profile).

A "bot" in nuvel is a thin, friendly name for a Hermes profile living under
``$HERMES_HOME/.hermes/profiles/<name>/``. This package wraps the ``hermes``
CLI so you can create, inspect, delete, and talk to bots — and have bots talk
to each other — from Python instead of the shell.

Public API::

    from nuvel.bots import BotClient, Bot, BotMessage

    client = BotClient()
    for bot in client.list_bots():
        print(bot.name, bot.model)

    reply = client.chat("research", "What did arXiv publish today?")
    print(reply.content)

Every method shells out to ``hermes`` via :mod:`subprocess` (never a shell,
so there is no command-injection surface) and parses its text output into the
typed models in :mod:`nuvel.bots.types`.
"""
from __future__ import annotations

from .client import BotClient
from .errors import (
    BotCLIError,
    BotError,
    BotNotFoundError,
    FleetError,
    SkillNotFoundError,
)
from .fleet import BotDeployResult, FleetDeployer, FleetDeployResult, FleetStatus
from .skills import SkillManager
from .types import Bot, BotMessage, InstalledSkill, SkillInfo

__all__ = [
    "BotClient",
    "Bot",
    "BotMessage",
    "BotError",
    "BotNotFoundError",
    "BotCLIError",
    "SkillManager",
    "SkillInfo",
    "InstalledSkill",
    "SkillNotFoundError",
    "FleetDeployer",
    "FleetDeployResult",
    "BotDeployResult",
    "FleetStatus",
    "FleetError",
]
