"""Usage insights for the operator: client profiles and the usage report.

Everything here reads or writes the Postgres schema (``nuvel_mcp.db``) as the
owning role; it is run from the CLI (``nuvel-mcp clients …``,
``nuvel-mcp report``), never by an MCP caller.

``client_profiles`` holds what the operator knows about who is behind an OAuth
client (person, company, notes). It is operator-entered, bounded in length,
and the only personal data the insights add; ``DELETE`` the row to forget it.
"""

from __future__ import annotations

import re

CLIENT_ID_RE = re.compile(r"\A[A-Za-z0-9_.:@-]{1,128}\Z")
PROFILE_LIMITS = {"person": 200, "company": 200, "notes": 2000}


def validate_profile(client_id: str, fields: dict) -> str | None:
    """An error message for a bad ``clients label`` input, else ``None``."""
    if not CLIENT_ID_RE.fullmatch(client_id or ""):
        return "client_id must be 1-128 characters of [A-Za-z0-9_.:@-]"
    if not fields:
        return "give at least one of --person, --company or --notes"
    for name, value in fields.items():
        limit = PROFILE_LIMITS[name]
        if value is not None and len(value) > limit:
            return f"--{name} is longer than {limit} characters"
        if value is not None and any(ord(ch) < 32 and ch not in "\t" for ch in value):
            return f"--{name} must not contain control characters"
    return None


def label_client(db, client_id: str, fields: dict) -> bool:
    """Create or update the profile of *client_id* with *fields* (``person``,
    ``company``, ``notes``; ``None`` clears one, absent keys are kept).
    Returns whether the id is known (an OAuth client or seen in usage)."""
    columns = [name for name in PROFILE_LIMITS if name in fields]
    table = db.table("client_profiles")
    values = [fields[name] for name in columns]
    updates = ", ".join(f"{name} = EXCLUDED.{name}" for name in columns)
    with db.connection() as conn:
        conn.execute(
            f"INSERT INTO {table} (client_id, {', '.join(columns)}) "
            f"VALUES (%s, {', '.join(['%s'] * len(columns))}) "
            f"ON CONFLICT (client_id) DO UPDATE SET {updates}, updated_at = now()",
            (client_id, *values))
        return conn.execute(
            f"SELECT EXISTS (SELECT 1 FROM {db.table('oauth_clients')} WHERE id = %s) "
            f"OR EXISTS (SELECT 1 FROM {db.table('usage_events')} WHERE tenant = %s)",
            (client_id, client_id)).fetchone()[0]


def list_clients(db, days: int = 30) -> list[dict]:
    """Every client id known from OAuth, profiles or usage, with its profile,
    last usage event and number of events in the last *days* days."""
    t = db.table
    sql = f"""
        WITH ids AS (
            SELECT id AS client_id FROM {t('oauth_clients')}
            UNION SELECT client_id FROM {t('client_profiles')}
            UNION SELECT DISTINCT tenant FROM {t('usage_events')} WHERE tenant IS NOT NULL
        ), seen AS (
            SELECT tenant, max(at) AS last_seen,
                   count(*) FILTER (WHERE at >= now() - make_interval(days => %s)) AS events
            FROM {t('usage_events')} WHERE tenant IS NOT NULL GROUP BY tenant
        )
        SELECT ids.client_id, c.record->>'client_name', p.person, p.company, p.notes,
               s.last_seen, coalesce(s.events, 0)
        FROM ids
        LEFT JOIN {t('client_profiles')} p ON p.client_id = ids.client_id
        LEFT JOIN {t('oauth_clients')} c ON c.id = ids.client_id
        LEFT JOIN seen s ON s.tenant = ids.client_id
        ORDER BY s.last_seen DESC NULLS LAST, ids.client_id"""
    with db.connection() as conn:
        rows = conn.execute(sql, (days,)).fetchall()
    return [{"client_id": r[0], "name": r[1], "person": r[2], "company": r[3], "notes": r[4],
             "last_seen": r[5].isoformat(timespec="seconds") if r[5] else None,
             f"events_{days}d": r[6]} for r in rows]


def format_table(headers: list[str], rows: list[list]) -> str:
    """A plain-text table: left-aligned text, right-aligned numbers."""
    cells = [["-" if v is None else _cell(v) for v in row] for row in rows]
    widths = [max([len(h)] + [len(r[i]) for r in cells]) for i, h in enumerate(headers)]
    numeric = [all(isinstance(row[i], (int, float)) or row[i] is None for row in rows) and rows
               for i in range(len(headers))]

    def line(values):
        return "  ".join(v.rjust(w) if num else v.ljust(w)
                         for v, w, num in zip(values, widths, numeric)).rstrip()

    return "\n".join([line(headers)] + [line(r) for r in cells])


def _cell(value) -> str:
    if isinstance(value, float):
        return f"{value:.1f}"
    return str(value)
