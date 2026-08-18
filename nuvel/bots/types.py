"""Typed models returned by :class:`nuvel.bots.client.BotClient`.

These are plain dataclasses — no behaviour, no I/O — so callers can treat
them as immutable-ish value objects and serialize them easily.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class Bot:
    """A Hermes profile, surfaced as a nuvel "bot".

    ``title`` has no native Hermes counterpart; it is a nuvel-side label kept
    on the object for convenience. ``model`` is reported by ``hermes profile
    show`` (provider prefix included, e.g. ``deepseek/deepseek-v4-flash``).
    """

    name: str
    title: str = ""
    description: str = ""
    model: Optional[str] = None
    skills: list[str] = field(default_factory=list)
    toolsets: list[str] = field(default_factory=list)


@dataclass
class SkillInfo:
    """A skill available in the Nuvel Skills Hub.

    ``category`` is the top-level hub directory the skill lives under (e.g.
    ``hr``); ``name`` comes from the skill's ``SKILL.md`` frontmatter. ``tags``
    are the ``metadata.hermes.tags`` list, used for search.
    """

    name: str
    category: str
    description: str
    tags: list[str] = field(default_factory=list)
    version: str = ""


@dataclass
class InstalledSkill:
    """A skill copied into a bot's Hermes profile.

    ``path`` is the destination directory
    (``<hermes_home>/profiles/<bot>/skills/<category>/<name>``).
    """

    name: str
    category: str
    path: str


@dataclass
class BotMessage:
    """A single response produced by a bot.

    ``content`` is the final text the bot returned. ``session`` is the Hermes
    session name/id the exchange happened in, when known.
    """

    bot: str
    content: str
    session: Optional[str] = None
    timestamp: datetime = field(default_factory=datetime.now)
