"""``python -m nuvel.evalv2`` — a friendly liveness check for Phase 1."""
from __future__ import annotations

from . import SCHEMA_VERSION


def main() -> None:
    print(f"evalv2 module loaded (schema {SCHEMA_VERSION})")


if __name__ == "__main__":
    main()
