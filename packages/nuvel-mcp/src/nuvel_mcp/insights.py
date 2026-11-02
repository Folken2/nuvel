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
import unicodedata

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


def _rate(part, whole) -> float:
    return round(part / whole, 4) if whole else 0.0


def _ms(value):
    return None if value is None else round(float(value), 1)


def build_report(db, days: int, known_tools) -> dict:
    """The usage report for the last *days* days, as plain JSON-able data.

    *known_tools* is the server's tool list; ``tools_never_called`` is the part
    of it with no ``tools/call`` in the window.
    """
    t = db.table
    usage = f"{t('usage_events')} WHERE at >= %(since)s"
    with db.connection() as conn:
        now = conn.execute("SELECT now()").fetchone()[0]
        p = {"since": now - _days(days)}

        def q(sql):
            return conn.execute(sql, p).fetchall()

        calls, errors, active = q(
            f"SELECT count(*), count(*) FILTER (WHERE outcome <> 'ok'), "
            f"count(DISTINCT tenant) FROM {usage}")[0]
        clients = q(f"""
            SELECT u.tenant, p.person, p.company, count(*),
                   count(*) FILTER (WHERE u.outcome <> 'ok'), max(u.at)
            FROM {t('usage_events')} u
            LEFT JOIN {t('client_profiles')} p ON p.client_id = u.tenant
            WHERE u.at >= %(since)s
            GROUP BY 1, 2, 3 ORDER BY 4 DESC, 1 NULLS LAST""")
        per_day = q(f"""
            SELECT to_char(date_trunc('day', at AT TIME ZONE 'UTC'), 'YYYY-MM-DD'),
                   count(*), count(*) FILTER (WHERE outcome <> 'ok')
            FROM {usage} GROUP BY 1 ORDER BY 1""")
        methods = q(f"""
            SELECT method, count(*), count(*) FILTER (WHERE outcome <> 'ok')
            FROM {usage} GROUP BY 1 ORDER BY 2 DESC, 1""")
        tools = q(f"""
            SELECT tool, count(*), count(*) FILTER (WHERE outcome <> 'ok'),
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY duration_ms),
                   percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_ms)
            FROM {usage} AND method = 'tools/call' GROUP BY 1 ORDER BY 2 DESC, 1""")
        tool_errors = q(f"""
            SELECT tool, coalesce(error_code::text, outcome), count(*)
            FROM {usage} AND method = 'tools/call' AND outcome <> 'ok'
            GROUP BY 1, 2 ORDER BY 1, 3 DESC, 2""")
        apps = q(f"""
            SELECT client_name, client_version, count(*), count(DISTINCT tenant)
            FROM {usage} GROUP BY 1, 2 ORDER BY 3 DESC, 1 NULLS LAST, 2""")
        countries = q(f"""
            SELECT country, count(*), count(DISTINCT tenant)
            FROM {usage} AND country IS NOT NULL GROUP BY 1 ORDER BY 2 DESC, 1""")
        runs = q(f"""
            SELECT plugin_name, skill_name, count(*),
                   count(*) FILTER (WHERE outcome = 'success'),
                   count(*) FILTER (WHERE outcome = 'partial'),
                   count(*) FILTER (WHERE outcome = 'failure'),
                   count(*) FILTER (WHERE outcome IN ('blocked', 'skipped')),
                   count(*) FILTER (WHERE user_corrected)
            FROM {t('skill_runs')} WHERE started_at >= %(since)s
            GROUP BY 1, 2 ORDER BY 3 DESC, 1, 2""")
        # recipe_signal/2 carries a status; older signals an event.
        recipes = q(f"""
            SELECT category, coalesce(record->>'status', event), count(*),
                   count(DISTINCT recipe_id), count(DISTINCT install_id)
            FROM {t('recipe_signals')} WHERE at >= %(since)s
            GROUP BY 1, 2 ORDER BY 1, 2""")
        recipe_totals = {row[0]: row[1:] for row in q(f"""
            SELECT category, count(*), count(DISTINCT recipe_id), count(DISTINCT install_id)
            FROM {t('recipe_signals')} WHERE at >= %(since)s GROUP BY 1""")}

    top_errors: dict = {}
    for tool, error, count in tool_errors:
        top_errors.setdefault(tool, [])
        if len(top_errors[tool]) < 3:
            top_errors[tool].append({"error": error, "count": count})
    called = {row[0] for row in tools}
    by_category: dict = {}
    for category, status, count, _, _ in recipes:
        by_category.setdefault(category, {})[status] = count

    return {
        "window": {"days": days, "since": p["since"].isoformat(timespec="seconds"),
                   "until": now.isoformat(timespec="seconds")},
        "totals": {"calls": calls, "errors": errors, "error_rate": _rate(errors, calls),
                   "active_clients": active},
        "active_clients": [
            {"client_id": r[0], "person": r[1], "company": r[2], "calls": r[3],
             "errors": r[4], "last_seen": r[5].isoformat(timespec="seconds")}
            for r in clients],
        "calls_per_day": [{"day": r[0], "calls": r[1], "errors": r[2]} for r in per_day],
        "methods": [{"method": r[0], "calls": r[1], "errors": r[2]} for r in methods],
        "tools": [
            {"tool": r[0], "calls": r[1], "errors": r[2], "error_rate": _rate(r[2], r[1]),
             "p50_ms": _ms(r[3]), "p95_ms": _ms(r[4]), "top_errors": top_errors.get(r[0], [])}
            for r in tools],
        "client_apps": [{"client_name": r[0], "client_version": r[1], "calls": r[2],
                         "clients": r[3]} for r in apps],
        "countries": [{"country": r[0], "calls": r[1], "clients": r[2]} for r in countries],
        "skill_runs": [
            {"plugin": r[0], "skill": r[1], "runs": r[2], "success": r[3], "partial": r[4],
             "failure": r[5], "not_run": r[6], "corrected": r[7],
             "correction_rate": _rate(r[7], r[2])}
            for r in runs],
        "tools_never_called": sorted(set(known_tools) - called),
        "recipes": [
            {"category": category, "signals": total[0], "recipes": total[1],
             "installs": total[2], "statuses": by_category.get(category, {})}
            for category, total in sorted(recipe_totals.items())],
    }


def _days(days: int):
    from datetime import timedelta

    return timedelta(days=days)


def format_report(report: dict) -> str:
    """The report as plain-text tables."""
    w, totals = report["window"], report["totals"]
    out = [f"Usage report: last {w['days']} day(s), {w['since'][:16]} to {w['until'][:16]} UTC",
           f"{totals['calls']} call(s), {totals['errors']} error(s) "
           f"({totals['error_rate']:.1%}), {totals['active_clients']} active client(s)."]

    def section(title, headers, rows, empty="none"):
        out.append("")
        out.append(title)
        out.append(format_table(headers, rows) if rows else f"  ({empty})")

    if not totals["calls"]:
        out.append("No usage events in this window.")
    section("Active clients", ["CLIENT_ID", "PERSON", "COMPANY", "CALLS", "ERRORS", "LAST_SEEN"],
            [[c["client_id"] or "(no auth)", c["person"], c["company"], c["calls"], c["errors"],
              c["last_seen"][:16].replace("T", " ")] for c in report["active_clients"]])
    section("Calls per day", ["DAY", "CALLS", "ERRORS"],
            [[d["day"], d["calls"], d["errors"]] for d in report["calls_per_day"]])
    section("Methods", ["METHOD", "CALLS", "ERRORS"],
            [[m["method"], m["calls"], m["errors"]] for m in report["methods"]])
    section("Tools", ["TOOL", "CALLS", "ERRORS", "ERR%", "P50_MS", "P95_MS", "TOP_ERRORS"],
            [[t["tool"], t["calls"], t["errors"], round(t["error_rate"] * 100, 1),
              t["p50_ms"], t["p95_ms"],
              ", ".join(f"{e['error']} x{e['count']}" for e in t["top_errors"]) or None]
             for t in report["tools"]])
    section("Client apps", ["CLIENT_APP", "VERSION", "CALLS", "CLIENTS"],
            [[a["client_name"] or "(not sent)", a["client_version"], a["calls"], a["clients"]]
             for a in report["client_apps"]])
    section("Countries", ["COUNTRY", "CALLS", "CLIENTS"],
            [[c["country"], c["calls"], c["clients"]] for c in report["countries"]],
            empty="no country recorded; see NUVEL_MCP_GEOIP_CSV")
    section("Skill runs", ["PLUGIN", "SKILL", "RUNS", "OK", "PARTIAL", "FAIL", "NOT_RUN",
                           "CORRECTED%"],
            [[r["plugin"], r["skill"], r["runs"], r["success"], r["partial"], r["failure"],
              r["not_run"], round(r["correction_rate"] * 100, 1)] for r in report["skill_runs"]])
    out.append("")
    out.append("Tools never called")
    out.append("  " + (", ".join(report["tools_never_called"]) or "(every tool was called)"))
    section("Recipe adoption", ["CATEGORY", "RECIPES", "INSTALLS", "SIGNALS", "STATUSES"],
            [[r["category"], r["recipes"], r["installs"], r["signals"],
              ", ".join(f"{k} {v}" for k, v in sorted(r["statuses"].items()))]
             for r in report["recipes"]])
    # Table cells are already clean; this also covers the free-text lines.
    return "\n".join(printable(line) for line in "\n".join(out).split("\n"))


def format_table(headers: list[str], rows: list[list]) -> str:
    """A plain-text table: left-aligned text, right-aligned numbers."""
    headers = [_cell(h) for h in headers]
    cells = [["-" if v is None else _cell(v) for v in row] for row in rows]
    widths = [max([len(h)] + [len(r[i]) for r in cells]) for i, h in enumerate(headers)]
    numeric = [all(isinstance(row[i], (int, float)) or row[i] is None for row in rows) and rows
               for i in range(len(headers))]

    def line(values):
        return "  ".join(v.rjust(w) if num else v.ljust(w)
                         for v, w, num in zip(values, widths, numeric)).rstrip()

    return "\n".join([line(headers)] + [line(r) for r in cells])


#: C0 controls, DEL and C1 controls: never written to the operator's terminal,
#: so a value from the database (an OAuth client_name from dynamic client
#: registration, say) cannot carry ANSI / OSC escape sequences.
_CONTROL_CHARS = dict.fromkeys([*range(0x20), *range(0x7f, 0xa0)])


def printable(text: str) -> str:
    """*text* without control characters (C0, DEL, C1) or Unicode format
    characters (category Cf: bidi overrides, zero-width), which can spoof rows."""
    return "".join(ch for ch in text.translate(_CONTROL_CHARS)
                   if unicodedata.category(ch) != "Cf")


def _cell(value) -> str:
    if isinstance(value, float):
        return f"{value:.1f}"
    return printable(str(value))
