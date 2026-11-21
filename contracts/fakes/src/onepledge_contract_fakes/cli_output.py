"""Console output helper for the command line tools of this package (TEST DOUBLES).

Every line a command prints goes through this class, so there is no bare print.
"""

from __future__ import annotations

import sys
from typing import TextIO


class CliOutput:
    """Writes result lines to stdout and one sentence errors to stderr."""

    def __init__(self, out: TextIO | None = None, err: TextIO | None = None) -> None:
        self._out = out if out is not None else sys.stdout
        self._err = err if err is not None else sys.stderr

    def line(self, text: str = "") -> None:
        """One line of normal output."""
        self._out.write(text + "\n")
        self._out.flush()

    def error(self, sentence: str) -> None:
        """One sentence describing what is wrong and what to do."""
        self._err.write(sentence + "\n")
        self._err.flush()
