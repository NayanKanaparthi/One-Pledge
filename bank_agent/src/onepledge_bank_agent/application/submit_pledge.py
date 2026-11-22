"""`SubmitPledge`: send the codes of a BACK_BOOK or PLEDGE submission to the registry (SPEC 13.4).

Eligible: the PENDING loans of the submission's accepted items that hold all eight
codes at one key version. Each item carries the ASSET code and the seven field codes.
"""

from __future__ import annotations

import uuid

from onepledge_bank_agent.application.ports import UnitOfWork
from onepledge_bank_agent.application.request_planning import Candidate
from onepledge_bank_agent.application.tape_requests import TapeRequests
from onepledge_bank_agent.application.tokenize_loans import complete_version
from onepledge_bank_agent.domain.enums import LoanState, TokenKind
from onepledge_bank_agent.domain.tape import TapeSubmission


class PledgeCandidates:
    """The loans of a lock request: PENDING and fully tokenised."""

    def candidates(self, unit: UnitOfWork, tape: TapeSubmission) -> list[Candidate]:
        """Accepted items whose loan is PENDING with eight codes, with the ASSET code at that version."""
        items = unit.items.accepted(tape.submission_id)
        loans = unit.loans.get_many(item.loan_id for item in items)
        tokens = unit.tokens.for_loans(loans)
        found = []
        for item in items:
            stored = tokens.get(item.loan_id, ())
            version = complete_version(stored)
            if loans[item.loan_id].state is not LoanState.PENDING or version is None:
                continue
            asset = next(t for t in stored if t.kind is TokenKind.ASSET and t.key_version == version)
            found.append(Candidate(item.row_number, item.loan_id, asset.value.value, version))
        return found


class SubmitPledge:
    """Plans and sends the lock requests of one TOKENIZED BACK_BOOK or PLEDGE submission."""

    def __init__(self, requests: TapeRequests) -> None:
        self._requests = requests
        self._selector = PledgeCandidates()

    def submit(self, submission_id: uuid.UUID) -> None:
        """Plan, send and complete the submission's lock requests."""
        self._requests.run(submission_id, self._selector)
