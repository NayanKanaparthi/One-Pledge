"""Named errors of the generator. Each carries a code; none carries a loan value."""

from __future__ import annotations

from onepledge_datagen.domain.text import plural


class DataGenError(Exception):
    """Base class of every error the generator reports."""

    code = "DATAGEN_ERROR"

    def __init__(self, sentence: str) -> None:
        super().__init__(sentence)
        self.sentence = sentence


class PlanError(DataGenError):
    """A profile asks for something the data set cannot provide (a generator defect)."""

    code = "PLAN_ERROR"


class InvariantsViolatedError(DataGenError):
    """The data set breaks at least one invariant of SPEC 5.8."""

    code = "INVARIANTS_VIOLATED"

    def __init__(self, violations: list[str]) -> None:
        super().__init__(f"The data set violates {plural(len(violations), 'invariant')}.")
        self.violations = violations


class OutputDirectoryError(DataGenError):
    """The output directory cannot be used."""

    code = "OUTPUT_DIRECTORY"


class DataSetUnreadableError(DataGenError):
    """A data directory is missing files or holds files that cannot be parsed."""

    code = "DATA_SET_UNREADABLE"
