"""Console output of the command line tools of this package (TEST DOUBLES package).

Every line a command prints goes through `CliOutput`, so there is no bare print.
stdout carries only the result: aligned text, or with `--json` exactly one JSON
document. Error sentences go to stderr. `run_guarded` is the last resort handler of
every command: an unexpected exception becomes one sentence (SPEC_DECISIONS 20, 23 d).
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from collections.abc import Callable
from typing import TextIO

from onepledge_contract_fakes.canonical_json import JsonValue

UNEXPECTED_ERROR = "Unexpected error. Run with --debug for details."


def plural(count: int, singular: str, plural_form: str | None = None) -> str:
    """`1 problem`, `2 problems`, `1,000 examples`: the count with the right noun."""
    noun = singular if count == 1 else (plural_form or singular + "s")
    return f"{count:,} {noun}"


def common_options(result: bool) -> argparse.ArgumentParser:
    """Options every subcommand takes: `--debug`, and `--json` and `--quiet` when it prints a result."""
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--debug", action="store_true", help="print the traceback of an unexpected error")
    if result:
        parser.add_argument("--json", action="store_true", help="print the result as one JSON document")
        parser.add_argument("--quiet", action="store_true", help="log warnings and errors only")
    return parser


class CliOutput:
    """Writes the result to stdout and one sentence errors to stderr."""

    def __init__(self, out: TextIO | None = None, err: TextIO | None = None) -> None:
        self._out = out if out is not None else sys.stdout
        self._err = err if err is not None else sys.stderr
        self.json_mode = False

    def line(self, text: str = "") -> None:
        """One line of the text result (nothing in JSON mode)."""
        if not self.json_mode:
            self._out.write(text + "\n")
            self._out.flush()

    def error(self, sentence: str) -> None:
        """One sentence describing what is wrong and what to do, on stderr."""
        self._err.write(sentence + "\n")
        self._err.flush()

    def result(self, document: dict[str, JsonValue]) -> None:
        """The JSON result of a successful command (only in JSON mode)."""
        if self.json_mode:
            self._write_json({"ok": True, **document})

    def failure(self, code: str, sentence: str, extra: dict[str, JsonValue] | None = None) -> None:
        """The sentence on stderr and, in JSON mode, the failure document on stdout."""
        self.error(sentence)
        if self.json_mode:
            self._write_json({"ok": False, "error": {"code": code, "message": sentence}, **(extra or {})})

    def traceback(self) -> None:
        """The traceback of the exception being handled, on stderr (`--debug` only)."""
        self._err.write(traceback.format_exc())
        self._err.flush()

    def _write_json(self, document: dict[str, JsonValue]) -> None:
        self._out.write(json.dumps(document, ensure_ascii=False, sort_keys=True) + "\n")
        self._out.flush()


def run_guarded(action: Callable[[], int], output: CliOutput, debug: bool) -> int:
    """Run a command; an unexpected exception is one sentence and exit 1, with the traceback on `--debug`."""
    try:
        return action()
    except Exception:  # The last resort handler of SPEC_DECISIONS 23 (d): never a bare traceback.
        if debug:
            output.traceback()
        output.failure("UNEXPECTED_ERROR", UNEXPECTED_ERROR)
        return 1
