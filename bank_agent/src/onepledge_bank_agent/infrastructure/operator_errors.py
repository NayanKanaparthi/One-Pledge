"""`OperatorError`: a command cannot run; it carries one sentence saying what is wrong and what to do."""

from __future__ import annotations


class OperatorError(Exception):
    """A problem an operator fixes (configuration, connection, files). Never holds a secret or loan value."""

    def __init__(self, sentence: str) -> None:
        super().__init__(sentence)
        self.sentence = sentence
