"""Console output of the generator's command line: aligned lines to stdout, one sentence errors to stderr."""

from __future__ import annotations

import sys
from collections.abc import Sequence
from typing import TextIO


class Console:
    """The only place the command line writes text."""

    def __init__(self, out: TextIO | None = None, err: TextIO | None = None) -> None:
        self._out = out if out is not None else sys.stdout
        self._err = err if err is not None else sys.stderr

    def line(self, text: str = "") -> None:
        """One line of output."""
        self._out.write(text + "\n")

    def table(self, headers: tuple[str, ...], rows: Sequence[tuple[str, ...]]) -> None:
        """Aligned columns with a header line; numbers are right aligned."""
        widths = [max(len(h), *(len(r[i]) for r in rows)) if rows else len(h) for i, h in enumerate(headers)]
        numeric = [
            all(r[i].replace(",", "").isdigit() for r in rows) and bool(rows) for i in range(len(headers))
        ]

        def render(cells: tuple[str, ...]) -> str:
            parts = [c.rjust(w) if n else c.ljust(w) for c, w, n in zip(cells, widths, numeric, strict=True)]
            return "  ".join(parts).rstrip()

        self.line(render(headers))
        for row in rows:
            self.line(render(row))

    def error(self, sentence: str) -> None:
        """One sentence on stderr."""
        self._err.write(sentence + "\n")
