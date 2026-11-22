"""`OperatorError`: a command cannot run; it carries one sentence saying what is wrong and what to do."""

from __future__ import annotations

CONFIGURATION_ERROR = "CONFIGURATION_ERROR"
DATABASE_UNAVAILABLE = "DATABASE_UNAVAILABLE"
SCHEMA_NOT_CURRENT = "SCHEMA_NOT_CURRENT"
INSTALLATION_ERROR = "INSTALLATION_ERROR"
FILE_ERROR = "FILE_ERROR"
OPERATION_FAILED = "OPERATION_FAILED"


class OperatorError(Exception):
    """A problem an operator fixes (configuration, connection, files). Never holds a secret or loan value.

    `code` is the upper snake case code of the `--json` error document (SPEC_DECISIONS 20).
    """

    def __init__(self, sentence: str, code: str) -> None:
        super().__init__(sentence)
        self.sentence = sentence
        self.code = code
