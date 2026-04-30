"""
Path resolution for {{agent_name}}.

All writable surfaces (SOUL.md, AWAKENING.md, skills/, memory/) can be
overridden via env vars so a deployment volume (Railway / Fly / Render)
works without code changes.

Env vars:
    SOUL_FILE       path to SOUL.md         (default: {{agent_package}}/soul/SOUL.md)
    AWAKENING_FILE  path to AWAKENING.md   (default: {{agent_package}}/soul/AWAKENING.md, persona only)
    SKILLS_DIR      path to skills/         (default: {{agent_package}}/skills/)
    MEMORY_DIR      path to memory/         (default: ./memory, read by state/memory.py)

The in-repo locations are the **seed** — what fresh deploys/dev clones
start from. The env-overridden locations are the **runtime state** — what
the running agent reads and writes.
"""

from __future__ import annotations

import os
from pathlib import Path

_PKG = Path(__file__).parent.parent  # {{agent_package}}/

# ── Seed locations (immutable, in-repo) ──────────────────────────────
SEED_SOUL_FILE: Path = _PKG / "soul" / "SOUL.md"
SEED_AWAKENING_FILE: Path = _PKG / "soul" / "AWAKENING.md"
SEED_SKILLS_DIR: Path = _PKG / "skills"
SEED_MEMORY_DIR: Path = _PKG.parent / "memory"


# ── Runtime locations (env-overridable) ──────────────────────────────
def soul_file() -> Path:
    return Path(os.getenv("SOUL_FILE", str(SEED_SOUL_FILE)))


def awakening_file() -> Path:
    return Path(os.getenv("AWAKENING_FILE", str(SEED_AWAKENING_FILE)))


def skills_dir() -> Path:
    return Path(os.getenv("SKILLS_DIR", str(SEED_SKILLS_DIR)))


def memory_dir() -> Path:
    return Path(os.getenv("MEMORY_DIR", str(SEED_MEMORY_DIR)))
