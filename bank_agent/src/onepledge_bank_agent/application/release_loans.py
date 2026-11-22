"""`ReleaseLoans`: send the release requests of a RELEASE submission to the registry (SPEC 13.4, 14.6).

Eligible: the loans of items with disposition RELEASE_REQUESTED that are still LOCKED.
A release reuses the loan's stored ASSET code and its key version; no field code is
sent and nothing is tokenised.
"""

from __future__ import annotations

import uuid

from onepledge_bank_agent.application.ports import UnitOfWork
from onepledge_bank_agent.application.request_planning import Candidate
from onepledge_bank_agent.application.tape_requests import TapeRequests
from onepledge_bank_agent.domain.enums import LoanState, TokenKind
from onepledge_bank_agent.domain.tape import TapeSubmission


class ReleaseCandidates:
    """The loans of a release request: LOCKED, with their stored ASSET code."""

    def candidates(self, unit: UnitOfWork, tape: TapeSubmission) -> list[Candidate]:
        """Items to release whose loan is LOCKED, with the newest stored ASSET code."""
        items = unit.items.release_requested(tape.submission_id)
        loans = unit.loans.get_many(item.loan_id for item in items)
        tokens = unit.tokens.for_loans(loans)
        found = []
        for item in items:
            assets = [t for t in tokens.get(item.loan_id, ()) if t.kind is TokenKind.ASSET]
            if loans[item.loan_id].state is not LoanState.LOCKED or not assets:
                continue
            asset = max(assets, key=lambda token: token.key_version)
            found.append(Candidate(item.row_number, item.loan_id, asset.value.value, asset.key_version))
        return found


class ReleaseLoans:
    """Plans and sends the release requests of one TOKENIZED RELEASE submission."""

    def __init__(self, requests: TapeRequests) -> None:
        self._requests = requests
        self._selector = ReleaseCandidates()

    def submit(self, submission_id: uuid.UUID) -> None:
        """Plan, send and complete the submission's release requests."""
        self._requests.run(submission_id, self._selector)
