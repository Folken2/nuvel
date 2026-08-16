"""The :class:`PluginRegistry` — discovers, loads, and aggregates plugins."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path

from .exceptions import AgentPluginError, ComponentDiscoveryError, ManifestError
from .manifest import PluginManifest
from .mcp_reader import McpServerEntry, read_mcp_config
from .skill_discovery import DiscoveredSkill, discover_skills


@dataclass
class PluginLoadError:
    """A non-fatal error tied to a specific plugin + component."""

    plugin_name: str
    component: str  # "manifest" | "skills" | "mcp"
    message: str


@dataclass
class PluginInfo:
    """The fully-loaded state of a single plugin."""

    manifest: PluginManifest
    root: Path
    skills: list[DiscoveredSkill] = field(default_factory=list)
    mcp_servers: dict[str, McpServerEntry] = field(default_factory=dict)
    errors: list[PluginLoadError] = field(default_factory=list)


class PluginRegistry:
    """Discovers and loads Agent Plugins from one or more directories."""

    def __init__(
        self,
        plugin_dirs: list[Path] | None = None,
        plugin_data_dir: Path | None = None,
    ) -> None:
        self.plugin_dirs: list[Path] = [Path(p) for p in (plugin_dirs or [])]
        self.plugin_data_dir: Path | None = (
            Path(plugin_data_dir) if plugin_data_dir is not None else None
        )
        self._plugins: dict[str, PluginInfo] = {}
        self._errors: list[PluginLoadError] = []
        self._lock = threading.Lock()

    # -- discovery ---------------------------------------------------------

    def discover_plugins(self) -> None:
        """Scan every ``plugin_dir`` for subdirectories containing plugin.json."""
        for base in self.plugin_dirs:
            if not base.is_dir():
                continue
            for child in sorted(base.iterdir()):
                if not child.is_dir():
                    continue
                if not (child / "plugin.json").is_file():
                    continue
                try:
                    self.load_plugin(child)
                except ManifestError:
                    # Fatal manifest error: already recorded on self.errors,
                    # skip this plugin and continue with the rest.
                    continue

    def load_plugin(self, path: Path) -> PluginInfo:
        """Load a single plugin directory, isolating component failures.

        A fatal manifest error is raised (the caller in ``discover_plugins``
        records it and moves on); component errors are captured on the
        returned :class:`PluginInfo`.
        """
        path = Path(path)

        try:
            manifest = PluginManifest.load(path)
        except ManifestError as exc:
            err = PluginLoadError(
                plugin_name=path.name, component="manifest", message=str(exc)
            )
            with self._lock:
                self._errors.append(err)
            raise

        info = PluginInfo(manifest=manifest, root=path)

        # Report (but do not fail on) unknown manifest fields.
        for uf in manifest.unknown_fields:
            info.errors.append(
                PluginLoadError(
                    plugin_name=manifest.name,
                    component="manifest",
                    message=f"unknown field ignored: {uf!r}",
                )
            )

        # PLUGIN_DATA base for this plugin (created on demand).
        plugin_data: Path | None = None
        if self.plugin_data_dir is not None:
            plugin_data = self.plugin_data_dir / manifest.name
            plugin_data.mkdir(parents=True, exist_ok=True)

        # --- skills (isolated) ---
        try:
            info.skills = discover_skills(path)
        except (ComponentDiscoveryError, AgentPluginError) as exc:
            info.skills = []
            info.errors.append(
                PluginLoadError(
                    plugin_name=manifest.name, component="skills", message=str(exc)
                )
            )

        # --- mcp (isolated) ---
        try:
            info.mcp_servers = read_mcp_config(path, plugin_data)
        except AgentPluginError as exc:  # pragma: no cover - reader is lenient
            info.mcp_servers = {}
            info.errors.append(
                PluginLoadError(
                    plugin_name=manifest.name, component="mcp", message=str(exc)
                )
            )

        with self._lock:
            self._plugins[manifest.name] = info

        return info

    # -- aggregation -------------------------------------------------------

    def get_skills(self) -> list[DiscoveredSkill]:
        with self._lock:
            plugins = list(self._plugins.values())
        skills: list[DiscoveredSkill] = []
        for info in plugins:
            skills.extend(info.skills)
        return skills

    def get_mcp_servers(self) -> dict[str, list[McpServerEntry]]:
        """Return MCP servers grouped by server name across all plugins."""
        with self._lock:
            plugins = list(self._plugins.values())
        grouped: dict[str, list[McpServerEntry]] = {}
        for info in plugins:
            for name, entry in info.mcp_servers.items():
                grouped.setdefault(name, []).append(entry)
        return grouped

    def get_plugin(self, name: str) -> PluginInfo | None:
        with self._lock:
            return self._plugins.get(name)

    def get_all_plugins(self) -> list[PluginInfo]:
        with self._lock:
            return list(self._plugins.values())

    @property
    def errors(self) -> list[PluginLoadError]:
        """Fatal errors encountered during discovery (skipped plugins)."""
        with self._lock:
            return list(self._errors)


__all__ = ["PluginRegistry", "PluginInfo", "PluginLoadError"]
