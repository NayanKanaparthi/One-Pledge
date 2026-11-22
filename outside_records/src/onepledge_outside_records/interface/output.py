"""Console output of `onepledge-outside` and its last resort handler (SPEC 19, SPEC_DECISIONS 20, 23 d, e).

stdout carries only the result: aligned text, or with `--json` exactly one JSON
document. Error sentences go to stderr, and log lines of every command except
`serve` go to stderr too.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from typing import TextIO

from onepledge_outside_records.infrastructure.logging_setup import frame_lines

UNEXPECTED_ERROR = "Unexpected error. Run with --debug for details."

type Json = bool | int | str | list[Json] | dict[str, Json] | None


def plural(count: int, singular: str, plural_form: str | None = None) -> str:
    """`1 row`, `2 rows`, `86,211 rows`: the count with the right noun."""
    noun = singular if count == 1 else (plural_form or singular + "s")
    return f"{count:,} {noun}"


QUIET_WITHOUT_LOGS = "no effect: this command writes no log lines"


def common_options(result: bool, logs: bool) -> argparse.ArgumentParser:
    """`--debug` for every command; `--json` and `--quiet` for the ones that print a result.

    `--quiet` is on every result printing command for one uniform interface; where the
    command writes no log lines its help says so (SPEC_DECISIONS 43.12).
    """
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "--debug",
        action="store_true",
        help="on an unexpected error, print the exception type and the frames (file, line, function)",
    )
    if result:
        parser.add_argument("--json", action="store_true", help="print the result as one JSON document")
        quiet_help = "log warnings and errors only" if logs else QUIET_WITHOUT_LOGS
        parser.add_argument("--quiet", action="store_true", help=quiet_help)
    return parser


class Console:
    """Writes the result to stdout and one sentence errors to stderr."""

    def __init__(self, out: TextIO | None = None, err: TextIO | None = None) -> None:
        self._out = out if out is not None else sys.stdout
        self._err = err if err is not None else sys.stderr
        self.json_mode = False

    @property
    def err(self) -> TextIO:
        """The stream for errors and for the log lines of non-serving commands."""
        return self._err

    def line(self, text: str) -> None:
        """One line of the text result (nothing in JSON mode)."""
        if not self.json_mode:
            self._out.write(text + "\n")
            self._out.flush()

    def error(self, sentence: str) -> None:
        """One sentence on stderr."""
        self._err.write(sentence + "\n")
        self._err.flush()

    def result(self, document: dict[str, Json]) -> None:
        """The JSON result of a successful command (only in JSON mode)."""
        if self.json_mode:
            self._write_json({"ok": True, **document})

    def failure(self, code: str, sentence: str) -> None:
        """The sentence on stderr and, in JSON mode, the failure document on stdout."""
        self.error(sentence)
        if self.json_mode:
            self._write_json({"ok": False, "error": {"code": code, "message": sentence}})

    def traceback(self, error: BaseException) -> None:
        """The exception type and its frames on stderr (`--debug` only, SPEC_DECISIONS 53.12).

        No exception message, no source line and no local variable is printed, the same
        rule as the `stack` log key, so the output is safe on any stream.
        """
        self._err.write(f"{type(error).__name__}, raised at:\n")
        for frame in frame_lines(error.__traceback__):
            self._err.write(f"  {frame}\n")
        self._err.flush()

    def _write_json(self, document: dict[str, Json]) -> None:
        self._out.write(json.dumps(document, ensure_ascii=False, sort_keys=True) + "\n")
        self._out.flush()


def run_guarded(action: Callable[[], int], console: Console, debug: bool) -> int:
    """Run a command; an unexpected exception is one sentence and exit 1, with its frames on `--debug`."""
    try:
        return action()
    except Exception as error:  # The last resort handler of SPEC_DECISIONS 23 (d): never a bare traceback.
        if debug:
            console.traceback(error)
        console.failure("UNEXPECTED_ERROR", UNEXPECTED_ERROR)
        return 1
