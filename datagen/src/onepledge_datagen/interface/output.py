"""Console output of the generator's command line (SPEC 19, SPEC_DECISIONS 20, 23 d).

stdout carries only the result: aligned text, or with `--json` exactly one JSON
document. One sentence errors go to stderr. Human readable lines wrap at 100 columns onto
indented continuation lines unless `--wide`; `parse_arguments` is the one place a usage
error with `--json` becomes its JSON document; `run_guarded` is the last resort handler,
which also writes one `error` record (SPEC_DECISIONS 78).
"""

from __future__ import annotations

import argparse
import json
import sys
import textwrap
import traceback
from collections.abc import Callable, Sequence
from types import TracebackType
from typing import TextIO

from onepledge_datagen.domain.text_values import is_ascii_digits

UNEXPECTED_ERROR = "Unexpected error. Run with --debug for details."
SERVICE_NAME = "datagen"
WIDTH = 100
USAGE_STATUS = 2  # argparse exits with 2 on a usage error
USAGE_MESSAGE = "The command line is not valid. See the usage text."
DEBUG_HELP = "print the exception type and call frames"
WIDE_HELP = "do not wrap human readable output at 100 columns"

type Json = bool | int | str | list[Json] | dict[str, Json] | None


QUIET_WITHOUT_LOGS = "no effect: this command writes no log lines but the record of an unexpected error"


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
        lines.extend(
            textwrap.wrap(
                line,
                WIDTH,
                subsequent_indent=indent + "  ",
                break_long_words=False,
                break_on_hyphens=False,
            )
        )
    return "\n".join(lines)


def parse_arguments(
    parser: argparse.ArgumentParser, argv: Sequence[str] | None, console: Console
) -> argparse.Namespace:
    """Parse the command line; a usage error with `--json` also prints the USAGE document on stdout.

    argparse prints the usage text and the error on stderr and exits 2, unchanged.
    """
    arguments = list(sys.argv[1:] if argv is None else argv)
    try:
        return parser.parse_args(arguments)
    except SystemExit as exit_:
        if exit_.code == USAGE_STATUS and "--json" in arguments:
            console.usage_failure()
        raise


def common_options() -> argparse.ArgumentParser:
    """`--json`, `--quiet` and `--debug`, taken by both commands.

    The generator writes no log lines; `--quiet` is kept for one interface across every
    result printing command of layer 1, and its help says it has no effect (SPEC_DECISIONS 43.12).
    """
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--json", action="store_true", help="print the result as one JSON document")
    parser.add_argument("--quiet", action="store_true", help=QUIET_WITHOUT_LOGS)
    parser.add_argument("--debug", action="store_true", help=DEBUG_HELP)
    parser.add_argument("--wide", action="store_true", help=WIDE_HELP)
    return parser


def frame_lines(tb: TracebackType | None) -> list[str]:
    """`file:line function` per frame: never a message, a source line or a local variable."""
    return [
        f"{frame.f_code.co_filename}:{line} {frame.f_code.co_name}" for frame, line in traceback.walk_tb(tb)
    ]


class Console:
    """The only place the command line writes text."""

    def __init__(self, out: TextIO | None = None, err: TextIO | None = None) -> None:
        self._out = out if out is not None else sys.stdout
        self._err = err if err is not None else sys.stderr
        self.json_mode = False
        self.wide = False

    def line(self, text: str = "") -> None:
        """One line of the text result (nothing in JSON mode)."""
        if not self.json_mode:
            self._out.write(fit(text, self.wide) + "\n")

    def result(self, document: dict[str, Json]) -> None:
        """The JSON result of a successful command (only in JSON mode)."""
        if self.json_mode:
            self._write_json({"ok": True, **document})

    def failure(self, code: str, sentence: str, extra: dict[str, Json] | None = None) -> None:
        """The sentence on stderr and, in JSON mode, the failure document on stdout."""
        self.error(sentence)
        if self.json_mode:
            self._write_json({"ok": False, "error": {"code": code, "message": sentence}, **(extra or {})})

    def traceback(self, error: BaseException) -> None:
        """The exception type and its frames on stderr (`--debug` only, SPEC_DECISIONS 53.12).

        No exception message, no source line and no local variable is printed.
        """
        self._err.write(f"{type(error).__name__}, raised at:\n")
        for frame in frame_lines(error.__traceback__):
            self._err.write(f"  {frame}\n")

    def _write_json(self, document: dict[str, Json]) -> None:
        self._out.write(json.dumps(document, ensure_ascii=False, sort_keys=True) + "\n")

    def table(self, headers: tuple[str, ...], rows: Sequence[tuple[str, ...]]) -> None:
        """Aligned columns with a header line; numbers are right aligned."""
        widths = [max(len(h), *(len(r[i]) for r in rows)) if rows else len(h) for i, h in enumerate(headers)]
        numeric = [
            all(is_ascii_digits(r[i].replace(",", "")) for r in rows) and bool(rows)
            for i in range(len(headers))
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
        self._err.write(fit(sentence, self.wide) + "\n")

    def usage_failure(self) -> None:
        """The USAGE document on stdout, whatever the mode (SPEC_DECISIONS 78)."""
        self._write_json({"ok": False, "error": {"code": "USAGE", "message": USAGE_MESSAGE}})

    def error_record(self, error: BaseException) -> None:
        """The one `error` log record of an unexpected failure: type and frames, never a message.

        The generator reads no clock (SPEC 5.1 determinism), so the record has no `ts`.
        """
        record = {
            "level": "error",
            "event": "unhandled_exception",
            "service": SERVICE_NAME,
            "exception_type": type(error).__name__,
            "stack": "\n".join(frame_lines(error.__traceback__)),
        }
        self._err.write(json.dumps(record, sort_keys=False) + "\n")


def run_guarded(action: Callable[[], int], console: Console, debug: bool) -> int:
    """Run a command; an unexpected exception is one sentence and exit 1, with its frames on `--debug`."""
    try:
        return action()
    except Exception as error:  # The last resort handler of SPEC_DECISIONS 23 (d): never a bare traceback.
        if debug:
            console.traceback(error)
        console.error_record(error)
        console.failure("UNEXPECTED_ERROR", UNEXPECTED_ERROR)
        return 1
