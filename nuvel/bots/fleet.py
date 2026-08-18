"""``FleetDeployer`` — provision and manage many bots from one YAML manifest.

A *fleet* is a named group of bots described by a YAML manifest::

    name: acme-support
    company: Acme Corp
    hub: /opt/data/skills            # optional local skills-hub checkout
    default_model: deepseek/deepseek-v4-flash
    bots:
      - name: triage-bot
        role: Customer Support Triage
        description: Classifies and routes incoming customer requests
        model: anthropic/claude-sonnet-4   # optional; overrides default_model
        skills:
          - customer/triage-agent

:meth:`FleetDeployer.deploy` walks the manifest, creating each bot (via
:class:`~nuvel.bots.client.BotClient`) and installing its skills (via
:class:`~nuvel.bots.skills.SkillManager`). A bot that already exists is left
untouched; a bot that fails is recorded and does not abort the rest of the fleet.

Every deploy writes a tracking record to ``<hermes_home>/fleets/<name>.json`` so
the fleet can later be inspected (:meth:`status`), enumerated (:meth:`list_fleets`)
or torn down (:meth:`destroy`).
"""
from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

import yaml

from .client import BotClient
from .errors import BotError, BotNotFoundError, FleetError
from .skills import SkillManager

#: Default Hermes home; its ``fleets/`` dir holds the tracking records.
DEFAULT_HERMES_HOME = Path.home() / ".hermes"


# --------------------------------------------------------------------------- #
# result / status models
# --------------------------------------------------------------------------- #
@dataclass
class BotDeployResult:
    """Outcome of provisioning a single bot within a fleet deploy.

    ``status`` is one of ``"created"``, ``"already_exists"`` or ``"failed"``.
    ``skills_installed`` holds the ``category/name`` refs that were copied in;
    ``error`` carries the failure message when ``status == "failed"``.
    """

    name: str
    status: str
    skills_installed: list[str] = field(default_factory=list)
    error: str | None = None


@dataclass
class FleetDeployResult:
    """Aggregate outcome of a :meth:`FleetDeployer.deploy` call.

    ``success`` is ``True`` only when no bot ended in the ``"failed"`` state.
    """

    fleet_name: str
    bots: list[BotDeployResult]
    success: bool
    started_at: datetime
    completed_at: datetime
    #: Governance layer — populated when the manifest declares them.
    has_vision: bool = False
    vision_path: str | None = None
    manager: str | None = None
    routines: list[dict] = field(default_factory=list)


@dataclass
class FleetStatus:
    """A previously deployed fleet, reconstructed from its tracking file."""

    fleet_name: str
    company: str
    bots: list[BotDeployResult]
    success: bool
    started_at: datetime
    completed_at: datetime
    #: Governance layer — the fleet constitution, manager bot and routines.
    has_vision: bool = False
    vision_path: str | None = None
    manager: str | None = None
    routines: list[dict] = field(default_factory=list)


@dataclass
class _FleetManifest:
    """The parsed YAML manifest (``bots``/``manager`` kept as raw dicts)."""

    name: str
    company: str
    hub: str | None
    default_model: str | None
    bots: list[dict]
    vision: str | None = None
    manager: dict | None = None
    #: Directory the manifest lives in — anchors relative ``vision`` file paths.
    base_dir: Path = field(default_factory=Path)


class FleetDeployer:
    """Deploy and manage a fleet of Hermes bots from a YAML manifest.

    Parameters mirror :class:`~nuvel.bots.client.BotClient`: ``hermes_bin`` and
    ``hermes_home`` are forwarded to it, and ``hermes_home`` also anchors the
    ``fleets/`` tracking directory.
    """

    def __init__(self, hermes_bin: str = "hermes", hermes_home: str | None = None) -> None:
        self._hermes_home = hermes_home
        self._client = BotClient(hermes_bin=hermes_bin, hermes_home=hermes_home)
        self._skill_mgr = SkillManager()
        home = Path(hermes_home).expanduser() if hermes_home else DEFAULT_HERMES_HOME
        self._home = home
        self._fleets_dir = home / "fleets"
        self._profiles_dir = home / "profiles"

    # ------------------------------------------------------------------ #
    # manifest loading
    # ------------------------------------------------------------------ #
    def _load_manifest(self, manifest_path: str) -> _FleetManifest:
        """Read + validate a YAML fleet manifest into a :class:`_FleetManifest`."""
        path = Path(manifest_path).expanduser()
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise FleetError(f"manifest not found: {manifest_path}") from exc
        except yaml.YAMLError as exc:
            raise FleetError(f"invalid YAML in {manifest_path}: {exc}") from exc
        if not isinstance(raw, dict):
            raise FleetError(f"manifest {manifest_path} must be a YAML mapping")
        name = raw.get("name")
        if not name:
            raise FleetError(f"manifest {manifest_path} is missing a 'name'")
        bots = raw.get("bots") or []
        if not isinstance(bots, list):
            raise FleetError(f"manifest {manifest_path} 'bots' must be a list")
        for bot in bots:
            if not isinstance(bot, dict) or not bot.get("name"):
                raise FleetError(f"every bot in {manifest_path} needs a 'name'")
        manager = raw.get("manager")
        if manager is not None:
            if not isinstance(manager, dict) or not manager.get("name"):
                raise FleetError(f"manager in {manifest_path} needs a 'name'")
        vision = raw.get("vision")
        return _FleetManifest(
            name=str(name),
            company=str(raw.get("company") or ""),
            hub=raw.get("hub"),
            default_model=raw.get("default_model"),
            bots=bots,
            vision=str(vision) if vision is not None else None,
            manager=manager,
            base_dir=path.parent,
        )

    # ------------------------------------------------------------------ #
    # deploy
    # ------------------------------------------------------------------ #
    def deploy(self, manifest_path: str) -> FleetDeployResult:
        """Read a YAML manifest and provision all its bots.

        For each bot: skip it if a profile of that name already exists, else
        create it and install its skills. Failures are isolated per bot. The
        result is persisted to the fleet tracking file before it is returned.
        """
        manifest = self._load_manifest(manifest_path)
        skill_mgr = SkillManager(hub_path=manifest.hub) if manifest.hub else self._skill_mgr

        started_at = datetime.now()
        existing = {b.name for b in self._client.list_bots()}
        results = [self._deploy_bot(spec, manifest, existing, skill_mgr) for spec in manifest.bots]

        # Governance layer: manager bot, fleet constitution, scheduled routines.
        manager = self._deploy_manager(manifest, existing, skill_mgr)
        vision_path = self._deploy_vision(manifest, manager)
        routines = self._deploy_routines(manifest, results)
        completed_at = datetime.now()

        result = FleetDeployResult(
            fleet_name=manifest.name,
            bots=results,
            success=all(b.status != "failed" for b in results),
            started_at=started_at,
            completed_at=completed_at,
            has_vision=vision_path is not None,
            vision_path=str(vision_path) if vision_path else None,
            manager=manager,
            routines=routines,
        )
        self._save_record(manifest, result)
        return result

    def _deploy_bot(
        self,
        spec: dict,
        manifest: _FleetManifest,
        existing: set[str],
        skill_mgr: SkillManager,
    ) -> BotDeployResult:
        """Provision one bot; never raises — failures become a result row."""
        name = str(spec["name"])
        if name in existing:
            return BotDeployResult(name=name, status="already_exists")
        skill_refs = spec.get("skills") or []
        try:
            self._client.create_bot(
                name,
                description=str(spec.get("description") or ""),
                model=spec.get("model") or manifest.default_model,
            )
            installed: list[str] = []
            if skill_refs:
                copied = skill_mgr.install_skills(name, skill_refs, hermes_home=self._hermes_home)
                installed = [f"{s.category}/{s.name}" for s in copied]
            return BotDeployResult(name=name, status="created", skills_installed=installed)
        except BotError as exc:
            return BotDeployResult(name=name, status="failed", error=str(exc))

    # ------------------------------------------------------------------ #
    # governance — manager bot
    # ------------------------------------------------------------------ #
    def _deploy_manager(
        self,
        manifest: _FleetManifest,
        existing: set[str],
        skill_mgr: SkillManager,
    ) -> str | None:
        """Provision the fleet's manager bot and write its SOUL.md.

        The manager is created (unless it already exists) with the fleet-management
        skills installed, then given a SOUL.md describing the fleet it supervises.
        Returns the manager's name, or ``None`` when the manifest declares none.
        """
        spec = manifest.manager
        if not spec:
            return None
        name = str(spec["name"])
        if name not in existing:
            self._client.create_bot(
                name,
                description=str(spec.get("description") or ""),
                model=spec.get("model") or manifest.default_model,
            )
            skill_mgr.install_skills(
                name, ["fleet/manage", "fleet/troubleshoot"], hermes_home=self._hermes_home
            )
        self._write_manager_soul(name, manifest)
        return name

    def _write_manager_soul(self, manager_name: str, manifest: _FleetManifest) -> None:
        """Write a fleet-aware SOUL.md into the manager's profile directory."""
        bot_names = [str(b["name"]) for b in manifest.bots]
        roster = ", ".join(bot_names) if bot_names else "no bots yet"
        lines = [
            f"You are the manager of the {manifest.name} fleet.",
            f"You manage {len(bot_names)} bot(s): {roster}.",
            "",
            "## Fleet Topology",
        ]
        for spec in manifest.bots:
            role = spec.get("role") or spec.get("description") or ""
            entry = f"- {spec['name']}"
            if role:
                entry += f": {role}"
            lines.append(entry)
        soul_dir = self._profiles_dir / manager_name
        soul_dir.mkdir(parents=True, exist_ok=True)
        (soul_dir / "SOUL.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    # ------------------------------------------------------------------ #
    # governance — vision (fleet constitution)
    # ------------------------------------------------------------------ #
    def _deploy_vision(self, manifest: _FleetManifest, manager: str | None) -> Path | None:
        """Materialise VISION.md and symlink it into every bot profile.

        Returns the path of the canonical fleet VISION.md, or ``None`` when the
        manifest carries no ``vision``.
        """
        if not manifest.vision:
            return None
        vision_file = self._write_fleet_vision(
            manifest.name, manifest.vision, manifest.base_dir
        )
        targets = [str(b["name"]) for b in manifest.bots]
        if manager:
            targets.append(manager)
        for bot_name in targets:
            self._link_vision(bot_name, vision_file)
        return vision_file

    def _write_fleet_vision(
        self, fleet_name: str, vision_source: str, base_dir: Path
    ) -> Path:
        """Write the fleet constitution to ``<home>/fleets/<name>/VISION.md``."""
        content = self._read_vision_source(vision_source, base_dir)
        vision_dir = self._fleets_dir / fleet_name
        vision_dir.mkdir(parents=True, exist_ok=True)
        vision_file = vision_dir / "VISION.md"
        vision_file.write_text(content, encoding="utf-8")
        return vision_file

    @staticmethod
    def _read_vision_source(vision_source: str, base_dir: Path) -> str:
        """Resolve a ``vision`` value to markdown text.

        A value beginning with ``#`` or ``---`` is treated as inline markdown;
        anything else is a path (relative to ``base_dir`` unless absolute).
        """
        stripped = vision_source.lstrip()
        if stripped.startswith("#") or stripped.startswith("---"):
            return vision_source
        path = Path(vision_source).expanduser()
        if not path.is_absolute():
            path = base_dir / path
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise FleetError(f"vision file not found: {vision_source}") from exc

    def _link_vision(self, bot_name: str, vision_file: Path) -> None:
        """Point ``<profiles>/<bot>/VISION.md`` at the fleet VISION.md.

        Refreshes an existing link/file so re-deploys stay current.
        """
        link = self._profiles_dir / bot_name / "VISION.md"
        link.parent.mkdir(parents=True, exist_ok=True)
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(vision_file.resolve())

    def update_vision(self, fleet_name: str, vision_source: str) -> None:
        """Update VISION.md for an existing fleet.

        ``vision_source`` can be inline markdown or a file path. The canonical
        fleet VISION.md is rewritten and every tracked bot's symlink refreshed.
        """
        record = self._read_record(fleet_name)
        if record is None:
            raise FleetError(f"no fleet named {fleet_name!r}")
        vision_file = self._write_fleet_vision(fleet_name, vision_source, Path.cwd())
        targets = [b["name"] for b in record.get("bots", [])]
        if record.get("manager"):
            targets.append(record["manager"])
        for bot_name in targets:
            self._link_vision(bot_name, vision_file)
        record["has_vision"] = True
        record["vision_path"] = str(vision_file)
        self._record_path(fleet_name).write_text(
            json.dumps(record, indent=2), encoding="utf-8"
        )

    # ------------------------------------------------------------------ #
    # governance — routines (scheduled tasks)
    # ------------------------------------------------------------------ #
    def _deploy_routines(
        self, manifest: _FleetManifest, results: list[BotDeployResult]
    ) -> list[dict]:
        """Create a Hermes cron job for every ``routines`` entry in the manifest.

        Routines for a bot that failed to deploy are skipped. A cron failure is
        isolated per routine (recorded under ``error``) so it never aborts the
        deploy — this also covers a Hermes build whose ``cron create`` rejects
        the arguments we pass.
        """
        deployed = {r.name for r in results if r.status != "failed"}
        routines: list[dict] = []
        for spec in manifest.bots:
            name = str(spec["name"])
            for routine in spec.get("routines") or []:
                schedule = str(routine.get("schedule") or "").strip()
                task = str(routine.get("task") or "").strip()
                if not schedule or not task:
                    continue
                entry: dict = {"bot": name, "schedule": schedule, "task": task, "job_id": None}
                if name not in deployed:
                    entry["error"] = "bot was not deployed"
                    routines.append(entry)
                    continue
                try:
                    job_id = self._client.create_cron_job(
                        name, schedule, task, name=f"fleet-{manifest.name}-{name}"
                    )
                    entry["job_id"] = job_id or None
                except BotError as exc:
                    entry["error"] = str(exc)
                routines.append(entry)
        return routines

    # ------------------------------------------------------------------ #
    # tracking file
    # ------------------------------------------------------------------ #
    def _record_path(self, fleet_name: str) -> Path:
        return self._fleets_dir / f"{fleet_name}.json"

    def _save_record(self, manifest: _FleetManifest, result: FleetDeployResult) -> None:
        """Persist a deploy result to ``<hermes_home>/fleets/<name>.json``."""
        self._fleets_dir.mkdir(parents=True, exist_ok=True)
        record = {
            "fleet_name": result.fleet_name,
            "company": manifest.company,
            "success": result.success,
            "started_at": result.started_at.isoformat(),
            "completed_at": result.completed_at.isoformat(),
            "bots": [asdict(b) for b in result.bots],
            "has_vision": result.has_vision,
            "vision_path": result.vision_path,
            "manager": result.manager,
            "routines": result.routines,
        }
        self._record_path(result.fleet_name).write_text(
            json.dumps(record, indent=2), encoding="utf-8"
        )

    def _read_record(self, fleet_name: str) -> dict | None:
        """Return the raw tracking record for ``fleet_name``, or ``None``."""
        path = self._record_path(fleet_name)
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def status(self, fleet_name: str) -> FleetStatus | None:
        """Return the tracked status for ``fleet_name``, or ``None`` if unknown."""
        data = self._read_record(fleet_name)
        if data is None:
            return None
        return FleetStatus(
            fleet_name=data["fleet_name"],
            company=data.get("company", ""),
            bots=[BotDeployResult(**b) for b in data.get("bots", [])],
            success=data.get("success", False),
            started_at=datetime.fromisoformat(data["started_at"]),
            completed_at=datetime.fromisoformat(data["completed_at"]),
            has_vision=data.get("has_vision", False),
            vision_path=data.get("vision_path"),
            manager=data.get("manager"),
            routines=data.get("routines", []),
        )

    def list_fleets(self) -> list[str]:
        """List deployed fleet names (from the tracking directory)."""
        if not self._fleets_dir.is_dir():
            return []
        return sorted(p.stem for p in self._fleets_dir.glob("*.json"))

    # ------------------------------------------------------------------ #
    # destroy
    # ------------------------------------------------------------------ #
    def destroy(self, manifest_path: str) -> None:
        """Delete every bot named in the manifest and drop its tracking file.

        Bots that are already gone are ignored, so ``destroy`` is safe to run
        repeatedly.
        """
        manifest = self._load_manifest(manifest_path)
        record = self._read_record(manifest.name)

        # Tear down scheduled routines first, using the tracked job ids.
        if record:
            for routine in record.get("routines", []):
                job_id = routine.get("job_id")
                if not job_id:
                    continue
                try:
                    self._client.remove_cron_job(routine["bot"], job_id)
                except BotError:
                    pass  # job already gone / cron unsupported — keep tearing down

        for spec in manifest.bots:
            try:
                self._client.delete_bot(str(spec["name"]))
            except BotNotFoundError:
                pass  # already gone — nothing to do

        # The manager bot lives outside ``bots`` — remove it too.
        manager = record.get("manager") if record else None
        if manager:
            try:
                self._client.delete_bot(manager)
            except BotNotFoundError:
                pass

        # Drop the fleet's VISION.md directory (bot symlinks die with the bots).
        vision_dir = self._fleets_dir / manifest.name
        if vision_dir.is_dir():
            shutil.rmtree(vision_dir, ignore_errors=True)

        self._record_path(manifest.name).unlink(missing_ok=True)
