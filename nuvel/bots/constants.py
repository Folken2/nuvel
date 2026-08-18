"""Defaults and well-known keys for :mod:`nuvel.bots`.

Centralised so the CLI command shapes live in exactly one place — if Hermes
renames a flag, this is the only file to touch.
"""
from __future__ import annotations

import re

#: Console script used to drive Hermes. Overridable per-:class:`BotClient`.
HERMES_BIN = "hermes"

#: Environment variable Hermes reads to locate its home directory. When a
#: caller passes ``hermes_home=`` we export this so every CLI call is scoped
#: to that install instead of the ambient one.
HERMES_HOME_ENV = "HERMES_HOME"

#: Named session used for bot-to-bot delivery (``chat -c "Agent Inbox"``).
AGENT_INBOX_SESSION = "Agent Inbox"

#: Hermes config key holding a profile's default model.
MODEL_CONFIG_KEY = "model.default"

#: Default timeout (seconds) for management CLI calls (list/create/show/...).
DEFAULT_TIMEOUT = 30

#: Chat can invoke an LLM, so it gets a longer default budget.
CHAT_TIMEOUT = 120

#: How long (seconds) a cached ``profile list`` result stays fresh.
LIST_CACHE_TTL = 30

#: Valid Hermes profile names: lowercase alphanumeric plus ``-``/``_``.
#: Enforced before a name reaches the CLI so callers get a clean Python
#: error instead of an opaque Hermes failure.
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
