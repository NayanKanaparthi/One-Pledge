"""Console output of the command line tools of this package (TEST DOUBLES package).

Every line a command prints goes through `CliOutput`, so there is no bare print.
stdout carries only the result: aligned text, or with `--json` exactly one JSON
document. Error sentences go to stderr. Human readable lines wrap at 100 columns onto
indented continuation lines unless `--wide` (SPEC_DECISIONS 78). `parse_arguments` is the
one place a usage error with `--json` becomes its JSON document, and `run_guarded` is the
last resort handler of every command: an unexpected exception becomes one sentence and
one `error` log record (SPEC_DECISIONS 20, 23 d, 78).
"""

from __future__ import annotations

import argparse
import json
import sys
import textwrap
from collections.abc import Callable, Sequence
from typing import TextIO

from onepledge_contract_fakes.canonical_json import JsonValue
from onepledge_contract_fakes.logging_setup import LogEvent, configure_logging, frame_lines, get_logger

UNEXPECTED_ERROR = "Unexpected error. Run with --debug for details."
USAGE_DOCUMENT: dict[str, JsonValue] = {
    "ok": False,
    "error": {"code": "USAGE", "message": "The command line is not valid. See the usage text."},
}
WIDTH = 100
USAGE_STATUS = 2  # argparse exits with 2 on a usage error
DEBUG_HELP = "print the exception type and call frames"
WIDE_HELP = "do not wrap human readable output at 100 columns"


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
    parser: argparse.ArgumentParser, argv: Sequence[str] | None, output: CliOutput
) -> argparse.Namespace:
    """Parse the command line; a usage error with `--json` also prints the USAGE document on stdout.

    argparse prints the usage text and the error on stderr and exits 2, unchanged.
    """
    arguments = list(sys.argv[1:] if argv is None else argv)
    try:
        return parser.parse_args(arguments)
    except SystemExit as exit_:
        if exit_.code == USAGE_STATUS and "--json" in arguments:
            output.usage_failure()
        raise


def plural(count: int, singular: str, plural_form: str | None = None) -> str:
    """`1 problem`, `2 problems`, `1,000 examples`: the count with the right noun."""
    noun = singular if count == 1 else (plural_form or singular + "s")
    return f"{count:,} {noun}"


QUIET_WITHOUT_LOGS = "no effect: this command writes no log lines"


def common_options(result: bool, logs: bool) -> argparse.ArgumentParser:
    """Options every subcommand takes: `--debug`, and `--json` and `--quiet` when it prints a result.

    `--quiet` is on every result printing command for one uniform interface; where the
    command writes no log lines its help says so (SPEC_DECISIONS 43.12).
    """
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--debug", action="store_true", help=DEBUG_HELP)
    parser.add_argument("--wide", action="store_true", help=WIDE_HELP)
    if result:
        parser.add_argument("--json", action="store_true", help="print the result as one JSON document")
        quiet_help = "log warnings and errors only" if logs else QUIET_WITHOUT_LOGS
        parser.add_argument("--quiet", action="store_true", help=quiet_help)
    return parser


class CliOutput:
    """Writes the result to stdout and one sentence errors to stderr."""

    def __init__(self, out: TextIO | None = None, err: TextIO | None = None) -> None:
        self._out = out if out is not None else sys.stdout
        self._err = err if err is not None else sys.stderr
        self.json_mode = False
        self.wide = False
        # Where log records go: stderr, except for `serve`, whose log lines are its stdout.
        self.logs_to_stdout = False

    @property
    def log_stream(self) -> TextIO:
        """The stream of this command's log records."""
        return self._out if self.logs_to_stdout else self._err

    def line(self, text: str = "") -> None:
        """One line of the text result (nothing in JSON mode)."""
        if not self.json_mode:
            self._out.write(fit(text, self.wide) + "\n")
            self._out.flush()

    def error(self, sentence: str) -> None:
        """One sentence describing what is wrong and what to do, on stderr."""
        self._err.write(fit(sentence, self.wide) + "\n")
        self._err.flush()

    def usage_failure(self) -> None:
        """The USAGE document on stdout, whatever the mode (SPEC_DECISIONS 78)."""
        self._write_json(USAGE_DOCUMENT)

    def result(self, document: dict[str, JsonValue]) -> None:
        """The JSON result of a successful command (only in JSON mode)."""
        if self.json_mode:
            self._write_json({"ok": True, **document})

    def failure(self, code: str, sentence: str, extra: dict[str, JsonValue] | None = None) -> None:
        """The sentence on stderr and, in JSON mode, the failure document on stdout."""
        self.error(sentence)
        if self.json_mode:
            self._write_json({"ok": False, "error": {"code": code, "message": sentence}, **(extra or {})})

    def traceback(self, error: BaseException) -> None:
        """The exception type and its frames on stderr (`--debug` only, SPEC_DECISIONS 53.12).

        No exception message, no source line and no local variable is printed, the same
        rule as the `stack` log key, so the output is safe on any stream.
        """
        self._err.write(f"{type(error).__name__}, raised at:\n")
        for frame in frame_lines(error.__traceback__):
            self._err.write(f"  {frame}\n")
        self._err.flush()

    def _write_json(self, document: dict[str, JsonValue]) -> None:
        self._out.write(json.dumps(document, ensure_ascii=False, sort_keys=True) + "\n")
        self._out.flush()


def run_guarded(action: Callable[[], int], output: CliOutput, debug: bool, service: str) -> int:
    """Run a command; an unexpected exception is one record, one sentence and exit 1.

    The `error` record carries `exception_type` and `stack` (frames only) and goes to the
    command's log stream; `--debug` also prints the frames (SPEC_DECISIONS 78).
    """
    try:
        return action()
    except Exception as error:  # The last resort handler of SPEC_DECISIONS 23 (d): never a bare traceback.
        if debug:
            output.traceback(error)
        configure_logging(service, "ERROR", output.log_stream)
        get_logger(__name__).error(LogEvent.UNHANDLED_EXCEPTION, exc_info=error)
        output.failure("UNEXPECTED_ERROR", UNEXPECTED_ERROR)
        return 1
