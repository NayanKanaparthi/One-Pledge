"""Malformed command lines and what a usage error must look like (SPEC_DECISIONS 121).

Each package's test runs every command with the lines `malformed` derives from one valid
command line and checks the outcome with `usage_problems`: exit 2, every line of stderr at
most 100 columns, the sentence free of the characters of a Python repr and of anything the
operator typed, and exactly one JSON usage document on stdout when an argument starting with
`--json` was given (nothing on stdout otherwise).
"""

from __future__ import annotations

import io
import json
from collections.abc import Sequence
from dataclasses import dataclass

WIDTH = 100
USAGE_STATUS = 2
# Typed by the "operator" of every malformed line; none of it may come back.
CANARY = 'ZQX\'[canary]("q"){}'
CANARY_MARK = "canary"
REPR_CHARACTERS = frozenset("'\"[](){}")
USAGE_DOCUMENT = {
    "ok": False,
    "error": {"code": "USAGE", "message": "The command line is not valid. See the usage text."},
}


@dataclass(frozen=True)
class Option:
    """One option of a valid command line: its name, its value (None for a flag), whether the
    command checks the value (a number, a choice) so that a nonsense value is a usage error, and
    whether it is required."""

    name: str
    value: str | None = None
    checked: bool = False
    required: bool = True

    def words(self) -> list[str]:
        """The option as it is typed."""
        return [self.name] if self.value is None else [self.name, self.value]


def _line(command: Sequence[str], options: Sequence[Option]) -> list[str]:
    return [*command, *(word for option in options for word in option.words())]


def malformed(command: Sequence[str], options: Sequence[Option]) -> dict[str, list[str]]:
    """About a dozen malformed command lines derived from the valid line `command options`.

    `command` is the subcommand path (`["generate"]`, `["db", "migrate"]`); every command of
    layer 1 takes `--debug`, which the flag variants use.
    """
    valid = _line(command, options)
    lines = {
        "no command": [],
        "unknown command": [CANARY],
        "unknown option": [*valid, "--no-such-option"],
        "unknown option typed with repr characters": [*valid, f"--{CANARY}"],
        "extra positional argument": [*valid, CANARY],
        "flag given twice": [*valid, "--debug", "--debug"],
        "flag given a value": [*valid, "--debug=yes"],
        "json given a value": [*valid, "--json=1"],
        "json given twice": [*valid, "--json", "--json"],
    }
    valued = [option for option in options if option.value is not None]
    if valued:
        first = valued[0]
        rest = [option for option in options if option is not first]
        lines["option given twice"] = [*valid, *first.words()]
        lines["option abbreviated"] = [*command, first.name[:4], str(first.value), *_line([], rest)]
        lines["option without its value"] = [*_line(command, rest), first.name]
        if first.required:
            lines["required option missing"] = _line(command, rest)
    for option in options:
        if option.checked:
            others = [other for other in options if other is not option]
            lines[f"{option.name} typed with repr characters"] = [
                *_line(command, others),
                option.name,
                CANARY,
            ]
            lines[f"{option.name} far too long"] = [*_line(command, others), option.name, "9" * 300]
    return lines


class UnwrappingStream(io.StringIO):
    """A text stream that keeps each write as it was before wrapping at 100 columns.

    Every sentence is written in one call, wrapped onto lines indented two more spaces than its
    first; within one write those continuation lines are joined back, so a test can compare a
    sentence naming a long temporary path as one line.
    """

    def write(self, text: str) -> int:
        """Write `text` with its continuation lines joined to the line they continue."""
        lines = text.split("\n")
        indent = " " * (len(lines[0]) - len(lines[0].lstrip(" "))) + "  "
        joined = [lines[0]]
        for line in lines[1:]:
            if line.startswith(indent) and line.strip():
                joined[-1] = f"{joined[-1]} {line.strip()}"
            else:
                joined.append(line)
        return super().write("\n".join(joined))


def wants_json(argv: Sequence[str]) -> bool:
    """Whether the line asks for the JSON form of a usage error (any argument starting `--json`)."""
    return any(argument.startswith("--json") for argument in argv)


def usage_problems(argv: Sequence[str], code: object, out: str, err: str) -> list[str]:
    """Everything wrong with the outcome of one malformed line; empty when it is right."""
    problems: list[str] = []
    if code != USAGE_STATUS:
        problems.append(f"exit code {code}, not {USAGE_STATUS}")
    lines = err.splitlines()
    problems.extend(f"stderr line of {len(line)} columns" for line in lines if len(line) > WIDTH)
    start = next(
        (index for index, line in enumerate(lines) if not line.startswith(("usage:", " "))), len(lines)
    )
    sentence = " ".join(line.strip() for line in lines[start:])
    if not sentence.endswith("."):
        problems.append("no sentence after the usage text")
    if REPR_CHARACTERS & set(sentence):
        problems.append(f"repr characters in the sentence: {sentence}")
    if CANARY_MARK in err or "999999999" in err:
        problems.append("stderr repeats what was typed")
    if wants_json(argv):
        try:
            document = json.loads(out)
        except json.JSONDecodeError:
            problems.append("stdout is not one JSON document")
        else:
            if document != USAGE_DOCUMENT:
                problems.append("stdout is not the USAGE document")
    elif out:
        problems.append("stdout is not empty")
    return problems
