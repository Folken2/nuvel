"""
Structured per-user profile.

The consolidation ("dream") pass distils a user's accumulated raw memories
into a compact structured profile — ``summary`` / ``role`` / ``interests`` /
``durable_facts`` — persisted as JSON alongside the markdown memory. The
instruction builder loads it back as a stable ``## User Profile`` block in the
session-context tier of the prompt (see ``prompt/instructions``).

Storage: ``<memory_dir>/profiles/<user>.json``. The default user id is
``default`` so single-tenant agents work without any user plumbing.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from ..config.paths import memory_dir

logger = logging.getLogger(__name__)

PROFILES_DIR = "profiles"
_PROFILE_KEYS = ("summary", "role", "interests", "durable_facts")


def _slug(user_id: str | None) -> str:
    uid = (user_id or "default").strip() or "default"
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in uid)[:80]


def _profile_path(user_id: str | None) -> Path:
    return memory_dir() / PROFILES_DIR / f"{_slug(user_id)}.json"


def normalize_profile(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Coerce an arbitrary dict into the profile schema (missing keys dropped)."""
    raw = raw or {}
    out: dict[str, Any] = {}
    summary = str(raw.get("summary", "") or "").strip()
    if summary:
        out["summary"] = summary
    role = str(raw.get("role", "") or "").strip()
    if role:
        out["role"] = role
    for key in ("interests", "durable_facts"):
        vals = raw.get(key) or []
        if isinstance(vals, str):
            vals = [vals]
        cleaned = [str(v).strip() for v in vals if str(v).strip()]
        if cleaned:
            out[key] = cleaned
    return out


def save_user_profile(profile: dict[str, Any], *, user_id: str | None = None) -> Path:
    """Persist a normalized profile for ``user_id``. Returns the file path."""
    path = _profile_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(normalize_profile(profile), indent=2), encoding="utf-8")
    return path


def load_user_profile(user_id: str | None = None) -> dict[str, Any]:
    """Load the stored profile for ``user_id`` (empty dict when none)."""
    path = _profile_path(user_id)
    if not path.is_file():
        return {}
    try:
        return normalize_profile(json.loads(path.read_text(encoding="utf-8")))
    except Exception as exc:
        logger.warning("failed to read profile %s: %s", path, exc)
        return {}


def render_profile_block(profile: dict[str, Any]) -> str:
    """Render a normalized profile as a ``## User Profile`` markdown block."""
    profile = normalize_profile(profile)
    if not profile:
        return ""
    lines = ["## User Profile"]
    if profile.get("summary"):
        lines.append(profile["summary"])
    if profile.get("role"):
        lines.append(f"- **Role:** {profile['role']}")
    if profile.get("interests"):
        lines.append("- **Interests:** " + ", ".join(profile["interests"]))
    if profile.get("durable_facts"):
        lines.append("- **Durable facts:**")
        lines.extend(f"  - {fact}" for fact in profile["durable_facts"])
    return "\n".join(lines)


def load_user_profile_block(user_id: str | None = None) -> str:
    """Convenience: load + render the profile block for ``user_id``."""
    return render_profile_block(load_user_profile(user_id))


__all__ = [
    "save_user_profile",
    "load_user_profile",
    "load_user_profile_block",
    "render_profile_block",
    "normalize_profile",
]
