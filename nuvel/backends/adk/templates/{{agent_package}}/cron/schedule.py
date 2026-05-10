"""Schedule-string parser.

Four input shapes are accepted; everything else raises ``ValueError``:

* relative one-shot:  ``30m``, ``2h``, ``1d`` (also ``45s``)
* interval:            ``every 30m``, ``every 2h``, ``every 1d``
* cron expression:     ``0 9 * * *`` (5 fields)
* ISO timestamp:       ``2026-12-15T09:00:00`` (one-shot, naive=UTC)

The parser is *pure*: it returns a :class:`ParsedSchedule` describing the
schedule semantics; the scheduler is responsible for computing concrete
``next_run_at`` values from it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal


ScheduleKind = Literal["one_shot_offset", "interval", "cron", "one_shot_at"]


@dataclass
class ParsedSchedule:
    kind: ScheduleKind
    raw: str
    # Populated according to ``kind``:
    seconds: int | None = None        # one_shot_offset / interval
    cron_expr: str | None = None      # cron
    at: datetime | None = None        # one_shot_at (timezone-aware UTC)

    @property
    def is_recurring(self) -> bool:
        return self.kind in ("interval", "cron")


_DURATION_RE = re.compile(r"^\s*(\d+)\s*([smhd])\s*$", re.IGNORECASE)
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}
_CRON_RE = re.compile(r"^\s*(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s*$")


def _parse_duration(token: str) -> int:
    m = _DURATION_RE.match(token)
    if not m:
        raise ValueError(
            f"Bad duration {token!r}. Use forms like 30s, 30m, 2h, 1d."
        )
    n, unit = int(m.group(1)), m.group(2).lower()
    if n <= 0:
        raise ValueError(f"Duration must be positive: {token!r}")
    return n * _UNIT_SECONDS[unit]


def parse_schedule(text: str) -> ParsedSchedule:
    """Parse a user-supplied schedule string.

    Raises:
        ValueError: with a helpful message if the string is unrecognized.
    """
    if not text or not text.strip():
        raise ValueError("Schedule must not be empty.")
    s = text.strip()

    # interval: "every <duration>"
    if s.lower().startswith("every "):
        rest = s[6:].strip()
        seconds = _parse_duration(rest)
        return ParsedSchedule(kind="interval", raw=s, seconds=seconds)

    # ISO timestamp: contains T or matches YYYY-MM-DD
    if "T" in s or re.match(r"^\d{4}-\d{2}-\d{2}", s):
        try:
            dt = datetime.fromisoformat(s)
        except ValueError as exc:
            raise ValueError(f"Bad ISO timestamp {s!r}: {exc}") from exc
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return ParsedSchedule(kind="one_shot_at", raw=s, at=dt)

    # cron expression: 5 whitespace-separated fields with a digit/glob char
    if _CRON_RE.match(s) and any(c.isdigit() or c in "*/," for c in s):
        # Validate via croniter if available; we do a strict check at compute time too.
        try:
            from croniter import croniter
            if not croniter.is_valid(s):
                raise ValueError(f"Invalid cron expression: {s!r}")
        except ImportError as exc:  # pragma: no cover — croniter is required
            raise ValueError(
                "croniter is required for cron expressions. Add 'croniter' to requirements."
            ) from exc
        return ParsedSchedule(kind="cron", raw=s, cron_expr=s)

    # relative duration
    if _DURATION_RE.match(s):
        seconds = _parse_duration(s)
        return ParsedSchedule(kind="one_shot_offset", raw=s, seconds=seconds)

    raise ValueError(
        f"Unrecognized schedule {s!r}. Use one of: '30m', 'every 1h', "
        "'0 9 * * *', or an ISO timestamp like '2026-12-15T09:00:00'."
    )


def compute_next_run(
    parsed: ParsedSchedule, *, now: datetime, last_run_at: datetime | None = None
) -> datetime | None:
    """Compute the next ``next_run_at`` for ``parsed``.

    Returns ``None`` if the schedule is one-shot and has already fired
    (caller should mark the job ``completed``).
    """
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    if parsed.kind == "one_shot_offset":
        # Compute relative to creation time on first call: we use ``now`` as
        # the anchor when ``last_run_at`` is None. After a successful run,
        # one-shots return None.
        if last_run_at is not None:
            return None
        return now + timedelta(seconds=parsed.seconds or 0)

    if parsed.kind == "one_shot_at":
        if last_run_at is not None:
            return None
        return parsed.at

    if parsed.kind == "interval":
        anchor = last_run_at or now
        return anchor + timedelta(seconds=parsed.seconds or 0)

    if parsed.kind == "cron":
        from croniter import croniter
        anchor = last_run_at or now
        c = croniter(parsed.cron_expr or "", anchor)
        return c.get_next(datetime).replace(tzinfo=timezone.utc)

    raise ValueError(f"Unknown schedule kind: {parsed.kind!r}")
