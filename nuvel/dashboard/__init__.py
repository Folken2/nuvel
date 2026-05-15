"""nuvel dashboard — local web command center over the JSONL trace stream.

Embedded as the `nuvel dashboard` subcommand. See
`docs/superpowers/specs/2026-05-15-traces-dashboard-design.md`.
"""

from nuvel.dashboard.cli import register

__all__ = ["register"]
