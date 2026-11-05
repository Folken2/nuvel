"""docs/reference/cli.md documents exactly the commands the parser has.

The commands come from walking nuvel.cli.build_parser(), so a new command,
flag or argument fails here until the reference mentions it, and a command
removed from the parser fails here until the reference drops it.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import pytest

from nuvel.cli import FROZEN_COMMANDS, build_parser

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / "docs/reference/cli.md"
HEADING = re.compile(r"^(#{2,6}) `(nuvel(?: [a-z0-9-]+)*)`$")


def _walk(parser: argparse.ArgumentParser, path: str = "nuvel"):
    """Yield (command path, parser, is_leaf) for every command, frozen ones included."""
    subparsers = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]
    yield path, parser, not subparsers
    for action in subparsers:
        # dict.items, not the visible iteration, so frozen commands are walked too.
        for name, child in dict.items(action.choices):
            yield from _walk(child, f"{path} {name}")


def _visible(path: str) -> bool:
    words = path.split()
    return not (len(words) > 1 and words[1] in FROZEN_COMMANDS)


def _arguments(parser: argparse.ArgumentParser):
    """(advertised, suppressed) argument spellings: flags as `--x`, positionals as `<x>`."""
    advertised, suppressed = [], []
    for action in parser._actions:
        if isinstance(action, (argparse._SubParsersAction, argparse._HelpAction)):
            continue
        names = action.option_strings or [f"<{action.metavar or action.dest}>"]
        (suppressed if action.help == argparse.SUPPRESS else advertised).extend(names)
    return advertised, suppressed


COMMANDS = {path: (parser, leaf) for path, parser, leaf in _walk(build_parser())}
VISIBLE = sorted(path for path in COMMANDS if _visible(path))
LEAVES = [path for path in VISIBLE if COMMANDS[path][1]]


def _sections() -> dict[str, str]:
    """Each `nuvel ...` heading's text, up to the next heading at its level or above."""
    lines = REFERENCE.read_text(encoding="utf-8").splitlines()
    starts = []
    for number, line in enumerate(lines):
        if line.startswith("#"):
            level = len(line) - len(line.lstrip("#"))
            match = HEADING.match(line)
            starts.append((number, level, match.group(2) if match else None))
    sections = {}
    for index, (number, level, command) in enumerate(starts):
        if command is None:
            continue
        assert command not in sections, f"{command} has two headings"
        end = next((n for n, lvl, _ in starts[index + 1:] if lvl <= level), len(lines))
        sections[command] = "\n".join(lines[number + 1:end])
    return sections


SECTIONS = _sections()


def test_walk_is_not_vacuous():
    assert {"nuvel agent create", "nuvel mcp client create", "nuvel bots skills search"} <= set(LEAVES)
    assert "--with-telegram" in _arguments(COMMANDS["nuvel agent create"][0])[0]
    assert set(FROZEN_COMMANDS) and all(f"nuvel {name}" in COMMANDS for name in FROZEN_COMMANDS)


def test_every_command_has_a_heading():
    assert sorted(set(VISIBLE) - set(SECTIONS)) == []


def test_reference_documents_no_command_the_parser_lacks():
    assert sorted(set(SECTIONS) - set(VISIBLE)) == []


@pytest.mark.parametrize("command", VISIBLE)
def test_every_flag_and_argument_is_documented(command):
    section = SECTIONS.get(command, "")
    advertised, _ = _arguments(COMMANDS[command][0])
    assert [name for name in advertised if f"`{name}`" not in section] == []


@pytest.mark.parametrize("command", LEAVES)
def test_every_command_documents_exit_codes_and_an_example(command):
    section = SECTIONS.get(command, "")
    assert "**Exit codes:**" in section
    assert re.search(rf"```console\n\$ (?:cd \S+ && )?{re.escape(command)}\b", section)


def test_help_and_version_are_documented():
    assert [name for name in ("`-h`", "`--help`", "`--version`") if name not in SECTIONS["nuvel"]] == []


def test_frozen_capabilities_are_absent():
    text = REFERENCE.read_text(encoding="utf-8")
    hidden = [f"nuvel {name}" for name in FROZEN_COMMANDS]
    for path, (parser, _) in COMMANDS.items():
        hidden += _arguments(parser)[1]
    assert hidden
    assert [term for term in hidden if term.strip("<>") in text] == []
