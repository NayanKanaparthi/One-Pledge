"""`RetryTape`: resume a FAILED submission from the status it failed at (SPEC 13.8, SPEC_DECISIONS 26).

Allowed for every failure code except KEY_VERSION_MISMATCH and CERTIFICATE_INVALID: a
rejection is usually a configuration fault an operator fixes and then retries, and every
stage is idempotent, so if the cause persists the tape fails again with the same code.
The retry clears the failure fields and the attempt count and is audited.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from onepledge_bank_agent.application.authorisation import Authoriser
from onepledge_bank_agent.application.ports import UnitOfWorkFactory
from onepledge_bank_agent.application.requester import Requester
from onepledge_bank_agent.application.runtime import Runtime
from onepledge_bank_agent.domain.access_policy import Operation
from onepledge_bank_agent.domain.audit import AuditDraft
from onepledge_bank_agent.domain.enums import AuditAction, AuditObjectType, AuditOutcome
from onepledge_bank_agent.domain.errors import IllegalTransitionError, NotFoundError, TapeNotRetryableError
from onepledge_bank_agent.domain.tape import TapeSubmission


@dataclass(frozen=True)
class RetryCommand:
    """Which submission, and who asks."""

    submission_id: uuid.UUID
    requester: Requester


class RetryTape:
    """Puts a FAILED submission back at its resume status."""

    def __init__(self, units: UnitOfWorkFactory, authoriser: Authoriser, runtime: Runtime) -> None:
        self._units = units
        self._authoriser = authoriser
        self._clock = runtime.clock

    def execute(self, command: RetryCommand) -> TapeSubmission:
        """The submission after the retry; `TapeNotRetryableError` when it is not FAILED or not retryable."""
        self._authoriser.require(command.requester, Operation.RETRY_TAPE)
        with self._units() as unit:
            tape = unit.tapes.get(command.submission_id, for_update=True)
            if tape is None:
                raise NotFoundError
            code = tape.progress.failure_code
            try:
                resumed = tape.resume(self._clock.now())
            except IllegalTransitionError:
                raise TapeNotRetryableError from None
            unit.tapes.save(tape)
            unit.record(
                AuditDraft(
                    AuditAction.TAPE_RETRY_REQUESTED,
                    command.requester.actor,
                    AuditObjectType.TAPE_SUBMISSION,
                    command.submission_id,
                    AuditOutcome.SUCCESS,
                    {
                        "failure_code": code.value if code else None,
                        "resume_status": resumed.value,
                        **command.requester.detail(),
                    },
                    command.requester.request_id,
                )
            )
            unit.commit()
        return tape
