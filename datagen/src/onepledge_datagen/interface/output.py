"""Console output of the generator's command line (SPEC 19, SPEC_DECISIONS 20, 23 d).

stdout carries only the result: aligned text, or with `--json` exactly one JSON
document. One sentence errors go to stderr. `run_guarded` is the last resort handler.
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from collections.abc import Callable, Sequence
from typing import TextIO

UNEXPECTED_ERROR = "Unexpected error. Run with --debug for details."

type Json = bool | int | str | list[Json] | dict[str, Json] | None


def common_options() -> argparse.ArgumentParser:
    """`--json`, `--quiet` and `--debug`, taken by both commands."""
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--json", action="store_true", help="print the result as one JSON document")
    parser.add_argument("--quiet", action="store_true", help="log warnings and errors only")
    parser.add_argument("--debug", action="store_true", help="print the traceback of an unexpected error")
    return parser


class Console:
    """The only place the command line writes text."""

    def __init__(self, out: TextIO | None = None, err: TextIO | None = None) -> None:
        self._out = out if out is not None else sys.stdout
        self._err = err if err is not None else sys.stderr
        self.json_mode = False

    def line(self, text: str = "") -> None:
        """One line of the text result (nothing in JSON mode)."""
        if not self.json_mode:
            self._out.write(text + "\n")

    def result(self, document: dict[str, Json]) -> None:
        """The JSON result of a successful command (only in JSON mode)."""
        if self.json_mode:
            self._write_json({"ok": True, **document})

    def failure(self, code: str, sentence: str, extra: dict[str, Json] | None = None) -> None:
        """The sentence on stderr and, in JSON mode, the failure document on stdout."""
        self.error(sentence)
        if self.json_mode:
            self._write_json({"ok": False, "error": {"code": code, "message": sentence}, **(extra or {})})

    def traceback(self) -> None:
        """The traceback of the exception being handled, on stderr (`--debug` only)."""
        self._err.write(traceback.format_exc())

    def _write_json(self, document: dict[str, Json]) -> None:
        self._out.write(json.dumps(document, ensure_ascii=False, sort_keys=True) + "\n")

    def table(self, headers: tuple[str, ...], rows: Sequence[tuple[str, ...]]) -> None:
        """Aligned columns with a header line; numbers are right aligned."""
        widths = [max(len(h), *(len(r[i]) for r in rows)) if rows else len(h) for i, h in enumerate(headers)]
        numeric = [
            all(r[i].replace(",", "").isdigit() for r in rows) and bool(rows) for i in range(len(headers))
        ]

        def render(cells: tuple[str, ...]) -> str:
            """One aligned line: numbers right aligned, text left aligned."""
            parts = [c.rjust(w) if n else c.ljust(w) for c, w, n in zip(cells, widths, numeric, strict=True)]
            return "  ".join(parts).rstrip()

        self.line(render(headers))
        for row in rows:
            self.line(render(row))

    def error(self, sentence: str) -> None:
        """One sentence on stderr."""
        self._err.write(sentence + "\n")


def run_guarded(action: Callable[[], int], console: Console, debug: bool) -> int:
    """Run a command; an unexpected exception is one sentence and exit 1, with the traceback on `--debug`."""
    try:
        return action()
    except Exception:  # The last resort handler of SPEC_DECISIONS 23 (d): never a bare traceback.
        if debug:
            console.traceback()
        console.failure("UNEXPECTED_ERROR", UNEXPECTED_ERROR)
        return 1
