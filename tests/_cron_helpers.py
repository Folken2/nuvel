"""Shared test helpers for cron tests.

Each test module scaffolds a tiny agent into a tmpdir, then imports the
agent package (``agent_test``) by adding its parent directory to
``sys.path``. We use a unique agent name per test module so multiple
suites can coexist in the same process without clashing on
``sys.modules``.
"""

from __future__ import annotations

import importlib
import os
import shutil
import sys
import tempfile
from pathlib import Path

from nuvel.backends.adk.scaffold import scaffold_agent


class CronAgent:
    """Holds the scaffolded agent's tmpdir and imported modules."""

    def __init__(self, name: str = "cron-agent"):
        self.name = name
        self.package = name.replace("-", "_")
        self.tmpdir = tempfile.mkdtemp(prefix="nuvel-cron-")
        self.cron_dir = Path(self.tmpdir) / "cron-state"
        self.cron_dir.mkdir(parents=True, exist_ok=True)

        result = scaffold_agent(self.name, output_dir=self.tmpdir, with_telegram=True)
        if result["status"] != "ok":
            raise RuntimeError(result.get("message"))
        self.agent_root = Path(result["path"])  # tmpdir/<name>
        self.pkg_dir = self.agent_root / self.package

        # Add the agent root (so the package is importable) and ensure
        # NUVEL_CRON_DIR is redirected before any cron module loads.
        if str(self.agent_root) not in sys.path:
            sys.path.insert(0, str(self.agent_root))
        os.environ["NUVEL_CRON_DIR"] = str(self.cron_dir)

        # Pre-import the cron submodules. They use {{agent_package}} -> the
        # rendered package name, so each scaffold yields a real importable
        # package.
        self.storage = importlib.import_module(f"{self.package}.cron.storage")
        importlib.reload(self.storage)
        self.schedule = importlib.import_module(f"{self.package}.cron.schedule")
        self.service_mod = importlib.import_module(f"{self.package}.cron.service")
        importlib.reload(self.service_mod)
        self.delivery = importlib.import_module(f"{self.package}.cron.delivery")
        self.scheduler = importlib.import_module(f"{self.package}.cron.scheduler")
        importlib.reload(self.scheduler)
        self.tools = importlib.import_module(f"{self.package}.cron.tools")
        importlib.reload(self.tools)
        self.routes = importlib.import_module(f"{self.package}.cron.routes")
        importlib.reload(self.routes)

    def cleanup(self) -> None:
        # Drop sys.modules entries for the package so subsequent scaffolds
        # in the same process don't accidentally import a stale tree.
        prefix = f"{self.package}."
        for mod in list(sys.modules):
            if mod == self.package or mod.startswith(prefix):
                sys.modules.pop(mod, None)
        try:
            sys.path.remove(str(self.agent_root))
        except ValueError:
            pass
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        os.environ.pop("NUVEL_CRON_DIR", None)
