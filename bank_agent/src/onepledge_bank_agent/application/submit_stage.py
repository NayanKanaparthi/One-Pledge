"""The SUBMITTED stage: the halt rule, then the use case registered for the tape's kind (SPEC 13.4, 15.4).

Before anything is planned or sent, the halt rule is checked: when
`HALT_ON_REGISTRY_INTEGRITY_FAILURE` is on and any received checkpoint failed
verification, nothing is sent and the submission fails with REGISTRY_INTEGRITY_BROKEN.
Otherwise BACK_BOOK and PLEDGE go to `SubmitPledge`, RELEASE to `ReleaseLoans`.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from types import MappingProxyType
from typing import Protocol

from onepledge_bank_agent.application.events import LogEvent
from onepledge_bank_agent.application.ports import OperationalLog, UnitOfWorkFactory
from onepledge_bank_agent.domain.dependency_errors import RegistryIntegrityBrokenError
from onepledge_bank_agent.domain.enums import TapeKind, TapeStatus


class SubmissionUseCase(Protocol):
    """A use case that sends one kind of submission to the registry."""

    def submit(self, submission_id: uuid.UUID) -> None:
        """Plan, send and complete the submission's requests."""
        ...


class HaltRule:
    """Refuses to send anything once a received checkpoint failed verification, unless switched off."""

    def __init__(self, units: UnitOfWorkFactory, enabled: bool, log: OperationalLog) -> None:
        self._units = units
        self._enabled = enabled
        self._log = log

    def check(self, submission_id: uuid.UUID | None = None) -> None:
        """Raise `RegistryIntegrityBrokenError` when the rule is on and a checkpoint failed."""
        if not self._enabled:
            return
        with self._units() as unit:
            broken = unit.checkpoints.any_failed()
        if broken:
            submission = str(submission_id) if submission_id is not None else None
            self._log.warning(LogEvent.HALT_RULE_APPLIED, submission_id=submission)
            raise RegistryIntegrityBrokenError


class SubmitStage:
    """Runs the submit stage of one TOKENIZED submission."""

    reached = TapeStatus.SUBMITTED

    def __init__(
        self, units: UnitOfWorkFactory, halt: HaltRule, by_kind: Mapping[TapeKind, SubmissionUseCase]
    ) -> None:
        self._units = units
        self._halt = halt
        self._by_kind = MappingProxyType(dict(by_kind))

    def run(self, submission_id: uuid.UUID) -> None:
        """Check the halt rule, then send the submission with the use case of its kind."""
        with self._units() as unit:
            tape = unit.tapes.get(submission_id)
        if tape is None or tape.status is not TapeStatus.TOKENIZED:
            return
        self._halt.check(submission_id)
        self._by_kind[tape.facts.kind].submit(submission_id)
