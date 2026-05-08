"""Example custom tool — replace or delete once you have real ones."""

from __future__ import annotations

from typing import Any


def lookup_user(args: dict[str, Any]) -> str:
    """Look up a user by ID. Replace with a real implementation.

    This runs on YOUR server, not in Anthropic's container. That means
    you can use API keys, internal libraries, database connections —
    anything in your host environment.
    """
    user_id = args["user_id"]
    # Real implementation: call your CRM, DB, etc. with whatever creds
    # this process has access to.
    return f"User {user_id}: Jane Doe (jane@example.com), enterprise plan."
