"""
First-boot seeding.

If the runtime paths point at a fresh deployment volume (or any empty
target), copy the in-repo seed files in once. Subsequent boots find the
targets populated and no-op.

Locally, the runtime paths default to the in-repo paths, so this is a no-op.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from .paths import (
    SEED_AWAKENING_FILE,
    SEED_MEMORY_DIR,
    SEED_SKILLS_DIR,
    SEED_SOUL_FILE,
    awakening_file,
    memory_dir,
    skills_dir,
    soul_file,
)

logger = logging.getLogger(__name__)


def _seed_file(seed: Path, target: Path) -> bool:
    if seed.resolve() == target.resolve():
        return False
    if target.exists():
        return False
    if not seed.is_file():
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(seed, target)
    logger.info("Seeded %s → %s", seed, target)
    return True


def _seed_dir(seed: Path, target: Path) -> bool:
    if seed.resolve() == target.resolve():
        return False
    if target.exists() and any(target.iterdir()):
        return False
    if not seed.is_dir():
        return False
    target.mkdir(parents=True, exist_ok=True)
    for child in seed.iterdir():
        dest = target / child.name
        if child.is_dir():
            shutil.copytree(child, dest)
        else:
            shutil.copy2(child, dest)
    logger.info("Seeded dir %s → %s", seed, target)
    return True


def seed_volume_if_empty() -> dict:
    """Run once at agent boot. Idempotent."""
    results = {
        "soul": _seed_file(SEED_SOUL_FILE, soul_file()),
        "awakening": _seed_file(SEED_AWAKENING_FILE, awakening_file()),
        "skills": _seed_dir(SEED_SKILLS_DIR, skills_dir()),
        "memory": _seed_dir(SEED_MEMORY_DIR, memory_dir()),
    }
    seeded = [k for k, v in results.items() if v]
    if seeded:
        logger.info("First-boot seeding: %s", ", ".join(seeded))
    return results
