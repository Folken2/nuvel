"""Error hierarchy for :mod:`nuvel.bots`.

A single base (:class:`BotError`) lets callers catch everything bot mode can
raise with one ``except``; the subclasses let you tell "no such bot" apart
from "the hermes CLI blew up".
"""
from __future__ import annotations


class BotError(Exception):
    """Base class for every bot-mode error."""


class BotNotFoundError(BotError):
    """The requested bot (Hermes profile) does not exist."""


class BotCLIError(BotError):
    """The ``hermes`` CLI could not be run, timed out, or returned non-zero."""


class SkillNotFoundError(BotError):
    """A requested skill could not be resolved in the skills hub.

    Also raised when a flat (category-less) name matches more than one skill;
    the message then lists the ``category/name`` candidates to disambiguate.
    """


class FleetError(BotError):
    """A fleet manifest was missing, malformed, or otherwise unusable."""
