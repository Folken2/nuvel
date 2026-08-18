"""nuvel bots — manage and talk to Hermes-backed bots from the CLI.

A thin argparse wrapper over :class:`nuvel.bots.BotClient`, registered onto the
top-level ``nuvel`` parser the same way ``traces``/``pricing``/``evalv2`` are::

    nuvel bots list                    # list all bots
    nuvel bots create <name>           # create a new bot
    nuvel bots delete <name>           # delete a bot
    nuvel bots chat <name> <message>   # send a message to a bot
    nuvel bots info <name>             # show a bot's details
    nuvel bots logs <name>             # show recent bot logs
    nuvel bots send <from> <to> <msg>  # bot-to-bot message

Every command is a thin wrapper: resolve a client, call one method, print. The
``--json`` flag emits a machine-readable object for scripting/agents; ``--verbose``
prints full tracebacks instead of a one-line error. Bot-mode errors
(:class:`BotError` and subclasses) are caught centrally in :func:`_dispatch`.
"""
from __future__ import annotations

import argparse
import dataclasses
import itertools
import json
import os
import shutil
import sys
import traceback
from pathlib import Path

from .client import BotClient
from .errors import BotError, BotNotFoundError
from .fleet import FleetDeployer
from .skills import SkillManager

# Common install locations probed when ``hermes`` is not on PATH.
_HERMES_FALLBACKS = (
    "/opt/hermes/bin/hermes",
    "/usr/local/bin/hermes",
)


# --------------------------------------------------------------------------- #
# client / output helpers
# --------------------------------------------------------------------------- #
def _resolve_hermes_bin(explicit: str | None) -> str:
    """Return a path to the ``hermes`` executable.

    Precedence: an explicit ``--hermes-bin`` > the first ``hermes`` on ``PATH``
    > a known fallback location. Falls back to the bare name ``"hermes"`` so the
    resulting :class:`BotCLIError` names the binary the user expected.
    """
    if explicit:
        return explicit
    found = shutil.which("hermes")
    if found:
        return found
    for candidate in _HERMES_FALLBACKS:
        if Path(candidate).is_file():
            return candidate
    return "hermes"


def _client(args: argparse.Namespace) -> BotClient:
    return BotClient(hermes_bin=_resolve_hermes_bin(args.hermes_bin))


def _emit(args: argparse.Namespace, payload, text: str) -> None:
    """Print ``payload`` as JSON when ``--json`` is set, else the ``text`` view."""
    if args.json:
        print(json.dumps(payload, indent=2, default=str))
    else:
        print(text)


def _bot_dict(bot) -> dict:
    return dataclasses.asdict(bot)


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #
def _cmd_list(args: argparse.Namespace) -> int:
    bots = _client(args).list_bots()
    payload = [_bot_dict(b) for b in bots]
    if not bots:
        _emit(args, payload, "No bots found.")
        return 0
    width = max(len(b.name) for b in bots)
    lines = [f"  {b.name:<{width}}  {b.model or '—'}" for b in bots]
    _emit(args, payload, "\n".join(lines))
    return 0


def _skill_manager(args: argparse.Namespace) -> SkillManager:
    """Build a SkillManager, honouring an optional ``--hub`` override."""
    return SkillManager(hub_path=getattr(args, "hub", None))


def _format_skills(skills) -> str:
    """Render skills grouped by category with aligned descriptions."""
    if not skills:
        return "No skills found."
    lines: list[str] = []
    ordered = sorted(skills, key=lambda s: (s.category, s.name))
    for category, group in itertools.groupby(ordered, key=lambda s: s.category):
        group = list(group)
        lines.append(category)
        width = max(len(s.name) for s in group)
        for s in group:
            lines.append(f"  {s.name:<{width}}  {s.description or '—'}")
    return "\n".join(lines)


def _cmd_create(args: argparse.Namespace) -> int:
    # --list-skills short-circuits: show the hub and exit without creating.
    if getattr(args, "list_skills", False):
        skills = _skill_manager(args).list_skills()
        payload = [dataclasses.asdict(s) for s in skills]
        _emit(args, payload, _format_skills(skills))
        return 0

    refs = [r.strip() for r in (args.skills or "").split(",") if r.strip()]
    if refs:
        # Route the internal SkillManager at an explicit --hub when one is given.
        if getattr(args, "hub", None):
            os.environ["NUVEL_SKILLS_HUB"] = args.hub
        bot, installed = _client(args).create_bot_with_skills(
            args.name,
            refs,
            description=args.description or "",
            model=args.model,
            clone_from=args.clone_from,
        )
        payload = {"bot": _bot_dict(bot), "installed": [dataclasses.asdict(s) for s in installed]}
        names = ", ".join(f"{s.category}/{s.name}" for s in installed) or "none"
        _emit(args, payload, f"Created bot '{bot.name}' with skills: {names}.")
        return 0

    bot = _client(args).create_bot(
        args.name,
        description=args.description or "",
        model=args.model,
        clone_from=args.clone_from,
    )
    _emit(args, _bot_dict(bot), f"Created bot '{bot.name}'.")
    return 0


def _cmd_skills_list(args: argparse.Namespace) -> int:
    skills = _skill_manager(args).list_skills(category=args.category)
    payload = [dataclasses.asdict(s) for s in skills]
    _emit(args, payload, _format_skills(skills))
    return 0


def _cmd_skills_search(args: argparse.Namespace) -> int:
    skills = _skill_manager(args).search_skills(args.query)
    payload = [dataclasses.asdict(s) for s in skills]
    _emit(args, payload, _format_skills(skills))
    return 0


def _cmd_delete(args: argparse.Namespace) -> int:
    _client(args).delete_bot(args.name)
    _emit(args, {"name": args.name, "deleted": True}, f"Deleted bot '{args.name}'.")
    return 0


def _cmd_chat(args: argparse.Namespace) -> int:
    reply = _client(args).chat(args.name, args.message)
    _emit(args, dataclasses.asdict(reply), reply.content)
    return 0


def _cmd_info(args: argparse.Namespace) -> int:
    bot = _client(args).get_bot_info(args.name)
    lines = [
        f"name:        {bot.name}",
        f"model:       {bot.model or '—'}",
        f"description: {bot.description or '—'}",
    ]
    _emit(args, _bot_dict(bot), "\n".join(lines))
    return 0


def _cmd_logs(args: argparse.Namespace) -> int:
    logs = _client(args).get_bot_logs(args.name, limit=args.limit)
    text = "\n".join(logs) if logs else "(no logs)"
    _emit(args, {"name": args.name, "logs": logs}, text)
    return 0


def _cmd_send(args: argparse.Namespace) -> int:
    reply = _client(args).chat_to_bot(args.from_bot, args.to_bot, args.message)
    _emit(args, dataclasses.asdict(reply), reply.content)
    return 0


# --------------------------------------------------------------------------- #
# fleet commands
# --------------------------------------------------------------------------- #
def _fleet_deployer(args: argparse.Namespace) -> FleetDeployer:
    return FleetDeployer(hermes_bin=_resolve_hermes_bin(args.hermes_bin))


def _format_bot_rows(bots) -> list[str]:
    """Render per-bot deploy/status rows (status, skills, error)."""
    lines: list[str] = []
    for b in bots:
        row = f"  {b.name}: {b.status}"
        if b.skills_installed:
            row += f" [{', '.join(b.skills_installed)}]"
        if b.error:
            row += f" — {b.error}"
        lines.append(row)
    return lines


def _format_governance_rows(status) -> list[str]:
    """Render the vision / manager / routines summary lines, when present."""
    lines: list[str] = []
    if getattr(status, "manager", None):
        lines.append(f"  manager: {status.manager}")
    if getattr(status, "has_vision", False):
        lines.append(f"  vision:  {status.vision_path or 'set'}")
    for routine in getattr(status, "routines", None) or []:
        detail = f"  routine: {routine['bot']} @ {routine['schedule']} — {routine['task']}"
        if routine.get("error"):
            detail += f" (error: {routine['error']})"
        lines.append(detail)
    return lines


def _cmd_fleet_deploy(args: argparse.Namespace) -> int:
    result = _fleet_deployer(args).deploy(args.manifest)
    header = f"Fleet '{result.fleet_name}': {'ok' if result.success else 'FAILED'}"
    text = "\n".join([header, *_format_bot_rows(result.bots), *_format_governance_rows(result)])
    _emit(args, dataclasses.asdict(result), text)
    return 0 if result.success else 1


def _cmd_fleet_list(args: argparse.Namespace) -> int:
    fleets = _fleet_deployer(args).list_fleets()
    text = "\n".join(fleets) if fleets else "No fleets deployed."
    _emit(args, fleets, text)
    return 0


def _cmd_fleet_status(args: argparse.Namespace) -> int:
    status = _fleet_deployer(args).status(args.name)
    if status is None:
        _emit(args, None, f"No fleet named '{args.name}'.")
        return 1
    header = f"Fleet '{status.fleet_name}' ({status.company or '—'})"
    text = "\n".join([header, *_format_bot_rows(status.bots), *_format_governance_rows(status)])
    _emit(args, dataclasses.asdict(status), text)
    return 0


def _cmd_fleet_destroy(args: argparse.Namespace) -> int:
    _fleet_deployer(args).destroy(args.manifest)
    _emit(args, {"manifest": args.manifest, "destroyed": True}, "Fleet destroyed.")
    return 0


def _cmd_fleet_update_vision(args: argparse.Namespace) -> int:
    _fleet_deployer(args).update_vision(args.name, args.source)
    _emit(
        args,
        {"fleet": args.name, "updated": True},
        f"Updated VISION.md for fleet '{args.name}'.",
    )
    return 0


# --------------------------------------------------------------------------- #
# dispatch / registration
# --------------------------------------------------------------------------- #
def _dispatch(args: argparse.Namespace) -> int:
    """Run ``args._bots_func`` with uniform bot-error handling."""
    try:
        return args._bots_func(args)
    except BotNotFoundError as exc:
        print(f"Error: bot not found: {exc}", file=sys.stderr)
        return 1
    except BotError as exc:
        if args.verbose:
            traceback.print_exc()
        else:
            print(f"Error: {exc}", file=sys.stderr)
        return 1


def _add_common_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--hermes-bin",
        default=None,
        help="Path to the hermes executable (default: found on PATH).",
    )
    parser.add_argument(
        "--json", action="store_true", help="Emit machine-readable JSON on stdout."
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Show full tracebacks on error."
    )


def register(subparsers: argparse._SubParsersAction) -> None:
    """Register the ``bots`` subcommand tree onto the top-level parser."""
    p = subparsers.add_parser(
        "bots",
        help="Manage and talk to Hermes-backed bots.",
    )
    sub = p.add_subparsers(dest="bots_command", required=True)

    p_list = sub.add_parser("list", help="List all bots.")
    _add_common_flags(p_list)
    p_list.set_defaults(_bots_func=_cmd_list)

    p_create = sub.add_parser("create", help="Create a new bot.")
    p_create.add_argument("name", help="Bot name (lowercase alphanumeric, - or _).")
    p_create.add_argument("--model", default=None, help="Default model for the bot.")
    p_create.add_argument("--description", default="", help="Bot description.")
    p_create.add_argument(
        "--clone-from", default=None, help="Seed config/skills from an existing bot."
    )
    p_create.add_argument(
        "--skills",
        default="",
        help="Comma-separated skill refs to install (e.g. hr/payroll,customer/triage).",
    )
    p_create.add_argument(
        "--list-skills",
        action="store_true",
        help="List available hub skills and exit without creating.",
    )
    p_create.add_argument(
        "--hub", default=None, help="Path to a local skills-hub checkout."
    )
    _add_common_flags(p_create)
    p_create.set_defaults(_bots_func=_cmd_create)

    p_delete = sub.add_parser("delete", help="Delete a bot.")
    p_delete.add_argument("name", help="Bot name to delete.")
    _add_common_flags(p_delete)
    p_delete.set_defaults(_bots_func=_cmd_delete)

    p_chat = sub.add_parser("chat", help="Send a message to a bot.")
    p_chat.add_argument("name", help="Bot to talk to.")
    p_chat.add_argument("message", help="Message to send.")
    _add_common_flags(p_chat)
    p_chat.set_defaults(_bots_func=_cmd_chat)

    p_info = sub.add_parser("info", help="Show a bot's details.")
    p_info.add_argument("name", help="Bot to inspect.")
    _add_common_flags(p_info)
    p_info.set_defaults(_bots_func=_cmd_info)

    p_logs = sub.add_parser("logs", help="Show recent bot logs.")
    p_logs.add_argument("name", help="Bot whose logs to show.")
    p_logs.add_argument(
        "-n", "--limit", type=int, default=10, help="Number of log lines (default: 10)."
    )
    _add_common_flags(p_logs)
    p_logs.set_defaults(_bots_func=_cmd_logs)

    p_send = sub.add_parser("send", help="Send a message from one bot to another.")
    p_send.add_argument("from_bot", metavar="from", help="Sending bot.")
    p_send.add_argument("to_bot", metavar="to", help="Receiving bot.")
    p_send.add_argument("message", help="Message to deliver.")
    _add_common_flags(p_send)
    p_send.set_defaults(_bots_func=_cmd_send)

    p_skills = sub.add_parser("skills", help="Browse the Nuvel skills hub.")
    skills_sub = p_skills.add_subparsers(dest="skills_command", required=True)

    p_sk_list = skills_sub.add_parser("list", help="List available skills.")
    p_sk_list.add_argument(
        "--category", default=None, help="Only show skills in this category."
    )
    p_sk_list.add_argument(
        "--hub", default=None, help="Path to a local skills-hub checkout."
    )
    _add_common_flags(p_sk_list)
    p_sk_list.set_defaults(_bots_func=_cmd_skills_list)

    p_sk_search = skills_sub.add_parser(
        "search", help="Search skills by name, description or tag."
    )
    p_sk_search.add_argument("query", help="Search text.")
    p_sk_search.add_argument(
        "--hub", default=None, help="Path to a local skills-hub checkout."
    )
    _add_common_flags(p_sk_search)
    p_sk_search.set_defaults(_bots_func=_cmd_skills_search)

    p.set_defaults(func=_dispatch)


def register_fleet(subparsers: argparse._SubParsersAction) -> None:
    """Register the ``fleet`` subcommand tree onto the top-level parser."""
    p = subparsers.add_parser(
        "fleet",
        help="Deploy and manage fleets of bots from a YAML manifest.",
    )
    sub = p.add_subparsers(dest="fleet_command", required=True)

    p_deploy = sub.add_parser("deploy", help="Deploy all bots from a manifest.")
    p_deploy.add_argument("manifest", help="Path to the fleet manifest (YAML).")
    _add_common_flags(p_deploy)
    p_deploy.set_defaults(_bots_func=_cmd_fleet_deploy)

    p_list = sub.add_parser("list", help="List deployed fleets.")
    _add_common_flags(p_list)
    p_list.set_defaults(_bots_func=_cmd_fleet_list)

    p_status = sub.add_parser("status", help="Show a fleet's deployment status.")
    p_status.add_argument("name", help="Fleet name.")
    _add_common_flags(p_status)
    p_status.set_defaults(_bots_func=_cmd_fleet_status)

    p_destroy = sub.add_parser("destroy", help="Delete all bots in a manifest.")
    p_destroy.add_argument("manifest", help="Path to the fleet manifest (YAML).")
    _add_common_flags(p_destroy)
    p_destroy.set_defaults(_bots_func=_cmd_fleet_destroy)

    p_vision = sub.add_parser(
        "update-vision", help="Update a fleet's VISION.md (constitution)."
    )
    p_vision.add_argument("name", help="Fleet name.")
    p_vision.add_argument(
        "source", help="Inline markdown (starting with '#'/'---') or a path to a file."
    )
    _add_common_flags(p_vision)
    p_vision.set_defaults(_bots_func=_cmd_fleet_update_vision)

    p.set_defaults(func=_dispatch)
