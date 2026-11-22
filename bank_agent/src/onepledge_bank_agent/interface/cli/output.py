"""The one output helper of the command line: aligned text by default, JSON with `--json` (SPEC 19).

stdout carries only the command's result: aligned text, or exactly one JSON document
with `--json` (`"ok": true` and the facts, or `"ok": false` and the error). Log lines
and the one sentence of an error go to stderr (SPEC_DECISIONS 20, 27). Tables have a
title line, column headers, aligned columns, a count line and a closing line. Hashes
are shown as their first 12 characters followed by `...`.

Human readable lines are at most 100 columns (SPEC_DECISIONS 78). The last column of a
table, and the value of a name and value pair, wraps at spaces onto continuation lines
that start under that column, so the other columns stay aligned and no word or
identifier is ever cut; any other long line wraps onto lines indented two more spaces.
A long identifier in a table is shortened like a hash (`shorten`). `--wide` turns the
limit and the shortening off. The call frames `--debug` prints are diagnostic text and
are printed as they are: a file path is never wrapped.
"""

from __future__ import annotations

import json
import sys
import textwrap
from collections.abc import Mapping, Sequence
from typing import Final, TextIO

from onepledge_bank_agent.domain.canonical_json import JsonValue

HASH_PREFIX = 12
WIDTH = 100
MIN_WRAP_COLUMN = 24
"""Below this many columns left for the last cell, the row wraps as a whole line instead."""
USAGE_STATUS = 2
"""The exit status of a usage error (argparse)."""
USAGE_CODE: Final = "USAGE"
USAGE_MESSAGE: Final = "The command line is not valid. See the usage text."


def _wrap(text: str, width: int, indent: str) -> list[str]:
    return textwrap.wrap(
        text, width, subsequent_indent=indent, break_long_words=False, break_on_hyphens=False
    )


def fit(text: str, wide: bool = False) -> str:
    """Lines of at most 100 columns: a longer one wraps onto lines indented two more spaces."""
    if wide:
        return text
    lines: list[str] = []
    for line in text.split("\n"):
        if len(line) <= WIDTH:
            lines.append(line)
            continue
        indent = line[: len(line) - len(line.lstrip(" "))]
        lines += _wrap(line, WIDTH, indent + "  ")
    return "\n".join(lines)


def hanging(prefix: str, last: str, wide: bool = False) -> list[str]:
    """`prefix` followed by `last`; when that is over 100 columns, `last` wraps onto lines that start
    under its first character, so the columns of `prefix` stay aligned."""
    line = (prefix + last).rstrip()
    available = WIDTH - len(prefix)
    if wide or len(line) <= WIDTH or available < MIN_WRAP_COLUMN:
        return fit(line, wide).split("\n")
    wrapped = _wrap(last, available, "")
    return [prefix + wrapped[0], *(" " * len(prefix) + part for part in wrapped[1:])]


def short_hash(value: str) -> str:
    """The first 12 characters of a hash followed by `...`."""
    return value[:HASH_PREFIX] + "..."


def shorten(value: str, wide: bool = False) -> str:
    """A long identifier as SPEC 19 shows a hash (12 characters and `...`); in full with `--wide`."""
    return value if wide or len(value) <= HASH_PREFIX + 3 else short_hash(value)


def counted(count: int, singular: str, plural: str) -> str:
    """`1 stage`, `2 stages`: the count with the right form of the noun (SPEC_DECISIONS 23 (e))."""
    return f"{count:,} {singular if count == 1 else plural}"


class Output:
    """Writes command results to stdout and one sentence errors to stderr."""

    def __init__(
        self,
        out: TextIO | None = None,
        err: TextIO | None = None,
        *,
        as_json: bool = False,
        wide: bool = False,
    ) -> None:
        self._out = out if out is not None else sys.stdout
        self._err = err if err is not None else sys.stderr
        self.as_json = as_json
        self.wide = wide

    def line(self, text: str) -> None:
        """One line of text, without trailing spaces, wrapped at 100 columns unless `--wide`."""
        self._out.write(fit(text.rstrip(), self.wide) + "\n")

    def _lines(self, lines: Sequence[str]) -> None:
        self._out.write("".join(line.rstrip() + "\n" for line in lines))

    def identifier(self, value: str) -> str:
        """A long identifier shortened for a table, unless `--wide` (SPEC_DECISIONS 78)."""
        return shorten(value, self.wide)

    def error(self, sentence: str) -> None:
        """One sentence on stderr, wrapped at 100 columns unless `--wide`."""
        self._err.write(fit(sentence, self.wide) + "\n")

    def usage_failure(self) -> None:
        """The USAGE document on stdout after argparse printed its usage text (SPEC_DECISIONS 78)."""
        self.json({"ok": False, "error": {"code": USAGE_CODE, "message": USAGE_MESSAGE}})

    def detail(self, text: str) -> None:
        """Diagnostic text on stderr (the traceback of `--debug`)."""
        self._err.write(text if text.endswith("\n") else text + "\n")

    def json(self, value: JsonValue) -> None:
        """The JSON form of a result, indented, keys in order."""
        self._out.write(json.dumps(value, indent=2, ensure_ascii=True) + "\n")

    def success(self, facts: Mapping[str, JsonValue]) -> None:
        """The `--json` document of a successful command."""
        self.json({"ok": True, **facts})

    def failure(self, code: str, sentence: str) -> int:
        """Report a failed command: the sentence on stderr and, with `--json`, the error document."""
        self.error(sentence)
        if self.as_json:
            self.json({"ok": False, "error": {"code": code, "message": sentence}})
        return 1

    def pairs(self, title: str, items: Mapping[str, str]) -> None:
        """A title and aligned `name  value` lines."""
        width = max((len(name) for name in items), default=0)
        self.line(title)
        for name, value in items.items():
            self._lines(hanging(f"  {name.ljust(width)}  ", value, self.wide))

    def table(
        self,
        title: str,
        headers: Sequence[str],
        rows: Sequence[Sequence[str]],
        count_label: tuple[str, str],
        closing: str,
    ) -> None:
        """A title, headers, aligned rows, a count line (singular and plural label) and a closing line.
        The last column wraps under itself when a row would be over 100 columns."""
        widths = [len(header) for header in headers]
        for row in rows:
            widths = [max(width, len(cell)) for width, cell in zip(widths, row, strict=True)]
        self.line(title)
        for cells in (headers, *rows):
            leading = zip(cells[:-1], widths[:-1], strict=True)
            prefix = "  " + "".join(cell.ljust(width) + "  " for cell, width in leading)
            self._lines(hanging(prefix, cells[-1], self.wide))
        self.line(counted(len(rows), *count_label))
        self.line(closing)
