"""The one output helper of the command line: aligned text by default, JSON with `--json` (SPEC 19).

Tables have a title line, column headers, aligned columns, a count line and a closing
line. Hashes are shown as their first 12 characters followed by `...`. Errors go to
stderr as one sentence.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping, Sequence
from typing import TextIO

from onepledge_bank_agent.domain.canonical_json import JsonValue

HASH_PREFIX = 12


def short_hash(value: str) -> str:
    """The first 12 characters of a hash followed by `...`."""
    return value[:HASH_PREFIX] + "..."


class Output:
    """Writes command results to stdout and one sentence errors to stderr."""

    def __init__(
        self, out: TextIO | None = None, err: TextIO | None = None, *, as_json: bool = False
    ) -> None:
        self._out = out if out is not None else sys.stdout
        self._err = err if err is not None else sys.stderr
        self.as_json = as_json

    def line(self, text: str) -> None:
        """One line of text, without trailing spaces."""
        self._out.write(text.rstrip() + "\n")

    def error(self, sentence: str) -> None:
        """One sentence on stderr."""
        self._err.write(sentence + "\n")

    def json(self, value: JsonValue) -> None:
        """The JSON form of a result, indented, keys in order."""
        self._out.write(json.dumps(value, indent=2, ensure_ascii=True) + "\n")

    def pairs(self, title: str, items: Mapping[str, str]) -> None:
        """A title and aligned `name  value` lines."""
        width = max((len(name) for name in items), default=0)
        self.line(title)
        for name, value in items.items():
            self.line(f"  {name.ljust(width)}  {value}")

    def table(
        self,
        title: str,
        headers: Sequence[str],
        rows: Sequence[Sequence[str]],
        count_label: str,
        closing: str,
    ) -> None:
        """A title, headers, aligned rows, a count line and a closing line."""
        widths = [len(header) for header in headers]
        for row in rows:
            widths = [max(width, len(cell)) for width, cell in zip(widths, row, strict=True)]
        self.line(title)
        self.line(
            "  " + "  ".join(header.ljust(width) for header, width in zip(headers, widths, strict=True))
        )
        for row in rows:
            self.line("  " + "  ".join(cell.ljust(width) for cell, width in zip(row, widths, strict=True)))
        self.line(f"{len(rows):,} {count_label}")
        self.line(closing)
