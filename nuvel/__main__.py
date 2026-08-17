"""Enable ``python -m nuvel`` to run the CLI (e.g. ``python -m nuvel mcp serve``)."""

import sys

from nuvel.cli import main

if __name__ == "__main__":
    sys.exit(main())
