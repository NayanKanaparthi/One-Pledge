"""The SUBMITTED stage: the halt rule, then the use case registered for the tape's kind (SPEC 13.4, 15.4).

Before anything is planned or sent, the halt rule is checked (SPEC 15.4, SPEC_DECISIONS
56): when `HALT_ON_REGISTRY_INTEGRITY_FAILURE` is on and any of these holds, nothing is
sent and the submission fails with REGISTRY_INTEGRITY_BROKEN:

1. a received checkpoint failed verification (`checkpoints_seen.verified` false);
2. a registry request has a failed certificate row and no verified one (decision 61);
3. a registry message of type CHECKPOINT is quarantined (decision 57);
4. the bank is blind: its last complete outbox sync is missing or older than
   `OUTBOX_BLIND_HALT_SECONDS` (decision 101). Before refusing for this reason alone, `check`
   runs one outbox sync to catch up, so a bank reads the outbox to its end before its first
   submission. It is the only condition that clears by itself.

The same rule stops a resubmission and a case position (`HaltRule.check`); a release is
accepted and its tape fails here like any other. Otherwise BACK_BOOK and PLEDGE go to
`SubmitPledge`, RELEASE to `ReleaseLoans`.
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


class OutboxWatch(Protocol):
    """The outbox sync as the halt rule sees it: whether the bank is blind, and a catch-up sync."""

    def blind(self) -> bool:
        """The last complete sync is missing or older than `OUTBOX_BLIND_HALT_SECONDS`."""
        ...

    def run(self, wait: bool = False) -> object:
        """One sync; with `wait` a sync running elsewhere is waited for."""
        ...


class HaltRule:
    """Refuses every state changing call to a registry that failed an integrity check, unless switched off."""

    def __init__(
        self, units: UnitOfWorkFactory, enabled: bool, log: OperationalLog, outbox: OutboxWatch
    ) -> None:
        self._units = units
        self._enabled = enabled
        self._log = log
        self._outbox = outbox

    def active(self) -> bool:
        """True when the rule is on and one of its four conditions holds."""
        return self._enabled and (self._broken() or self._outbox.blind())

    def check(self, submission_id: uuid.UUID | None = None) -> None:
        """Raise `RegistryIntegrityBrokenError` when the rule is active, after one catch-up sync if blind."""
        if self._enabled and not self._broken() and self._outbox.blind():
            self._outbox.run(wait=True)
        if self.active():
            submission = str(submission_id) if submission_id is not None else None
            self._log.warning(LogEvent.HALT_RULE_APPLIED, submission_id=submission)
            raise RegistryIntegrityBrokenError

    def _broken(self) -> bool:
        """One of the three conditions that hold for good."""
        with self._units() as unit:
            return (
                unit.evidence.checkpoints.any_failed()
                or unit.evidence.certificates.any_unproven()
                or unit.evidence.quarantine.any_checkpoint()
            )


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
