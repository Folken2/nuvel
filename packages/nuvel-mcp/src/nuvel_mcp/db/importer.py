"""Copy the file stores into Postgres (``nuvel-mcp db import``).

Traces, signals and recipe versions go through the stores' dedupe, so a
re-run inserts nothing. OAuth is a one-time move: once the database has held any
OAuth state it is the authority, and the file is not imported again (unless
forced), because re-importing would bring back clients and keys revoked in the
database since. Unreadable lines and files are counted as ``skipped``, never
fatal.
"""

from __future__ import annotations

import json
from pathlib import Path

from nuvel_mcp.db import Database
from nuvel_mcp.db.stores import OAUTH_TABLES, PostgresRecipeStore, PostgresTraceStore
from nuvel_mcp.recipes import RECIPES_DIRNAME
from nuvel_mcp.traces import RUNS_DIRNAME


def _jsonl(path: Path):
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        yield None
        return
    for line in lines:
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            yield None
            continue
        yield record if isinstance(record, dict) else None


def _import_oauth(db: Database, path: Path, counts: dict, force: bool) -> bool:
    """Insert the file's OAuth rows; ``False`` when skipped because the
    database already holds OAuth state and *force* is off."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        counts["skipped"] += 1
        return True
    if not isinstance(data, dict):
        counts["skipped"] += 1
        return True
    inserted = 0
    with db.connection() as conn:
        state = db.table("oauth_state")
        # The lock also serialises against running servers' OAuth writes. Every
        # OAuth write bumps the counter, so 0 means "never held OAuth state";
        # emptiness is not enough (deleting the last client empties the tables).
        version = conn.execute(
            f"SELECT version FROM {state} WHERE id = 1 FOR UPDATE").fetchone()[0]
        if version > 0 and not force:
            return False
        for key, table in OAUTH_TABLES.items():
            rows = data.get(key)
            if not isinstance(rows, dict):
                continue
            for row_id, record in rows.items():
                if not isinstance(record, dict):
                    counts["skipped"] += 1
                    continue
                done = conn.execute(
                    f"INSERT INTO {db.table(table)} (id, client_id, record) VALUES (%s, %s, %s) "
                    "ON CONFLICT (id) DO NOTHING RETURNING id",
                    (row_id, record.get("client_id"), db.jsonb(record))).fetchone()
                if done:
                    counts[table] += 1
                    inserted += 1
        if inserted:
            # Running servers reload their OAuth snapshot on the next request.
            conn.execute(f"UPDATE {state} SET version = version + 1 WHERE id = 1")
    return True


def _append_all(records, append, counts: dict, table: str) -> None:
    for record in records:
        if record is None:
            counts["skipped"] += 1
            continue
        try:
            if append(record):
                counts[table] += 1
        except (KeyError, TypeError, ValueError):
            counts["skipped"] += 1


def import_files(db: Database, *, oauth_store: Path | None, feedback_dir: Path,
                 force_oauth: bool = False) -> dict:
    """Import what exists; return rows inserted per table, ``skipped`` and
    ``oauth_imported`` (``False`` when the database already had OAuth state).

    *force_oauth* merges the file's OAuth rows in anyway (rows whose id is
    already present are left alone; deleted ones come back)."""
    counts = {table: 0 for table in (*OAUTH_TABLES.values(), "skill_runs",
                                      "recipe_signals", "recipes")}
    counts["skipped"] = 0
    oauth_imported = True
    if oauth_store is not None and oauth_store.is_file():
        oauth_imported = _import_oauth(db, oauth_store, counts, force_oauth)

    traces = PostgresTraceStore(db)
    for path in sorted((feedback_dir / RUNS_DIRNAME).glob("*/*.jsonl")):
        _append_all(_jsonl(path), traces.append, counts, "skill_runs")

    recipes = PostgresRecipeStore(db)
    base = feedback_dir / RECIPES_DIRNAME
    for path in sorted((base / "signals").glob("*.jsonl")):
        _append_all(_jsonl(path), recipes.append_signal, counts, "recipe_signals")
    docs = []
    for path in sorted((base / "recipes").glob("*/*/v*.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            doc = None
        docs.append(doc if isinstance(doc, dict) else None)
    _append_all(docs, recipes.put_recipe, counts, "recipes")
    counts["oauth_imported"] = oauth_imported
    return counts
