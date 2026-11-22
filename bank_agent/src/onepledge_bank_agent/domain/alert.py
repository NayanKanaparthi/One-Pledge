"""`Alert`: this bank's view of one registry case on one of its loans (SPEC 8.5, 14).

Only the registry's case outcome closes an alert; a bank user can start a review and
record the bank's position, nothing more.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from datetime import datetime
from types import MappingProxyType

from onepledge_bank_agent.domain.enums import (
    AlertRole,
    AlertStatus,
    BankPosition,
    CaseStatus,
    ExplanationSource,
    MatchBasis,
    MatchKind,
    TokenKind,
)
from onepledge_bank_agent.domain.errors import (
    AlertTransitionInvalidError,
    IllegalTransitionError,
    InvalidValueError,
    PositionAlreadyRecordedError,
)

MAX_NOTE_LENGTH = 500
ALERT_TRANSITIONS: frozenset[tuple[AlertStatus | None, AlertStatus]] = frozenset(
    {
        (None, AlertStatus.NEW),
        (AlertStatus.NEW, AlertStatus.REVIEWING),
        (AlertStatus.REVIEWING, AlertStatus.REVIEWING),
        (AlertStatus.NEW, AlertStatus.CLEARED),
        (AlertStatus.REVIEWING, AlertStatus.CLEARED),
        (AlertStatus.NEW, AlertStatus.ESCALATED),
        (AlertStatus.REVIEWING, AlertStatus.ESCALATED),
    }
)
_OPEN_STATUSES = frozenset({AlertStatus.NEW, AlertStatus.REVIEWING})
_CLOSING = MappingProxyType(
    {
        CaseStatus.CLEARED_DATA_ERROR: AlertStatus.CLEARED,
        CaseStatus.CLEARED_LATE_RELEASE: AlertStatus.CLEARED,
        CaseStatus.CONFIRMED: AlertStatus.ESCALATED,
    }
)


@dataclass(frozen=True)
class MatchFacts:
    """What the registry said about the match. No loan value."""

    role: AlertRole
    match_kind: MatchKind
    match_basis: MatchBasis
    kinds_matched: tuple[TokenKind, ...]

    @property
    def kinds_not_matched(self) -> tuple[TokenKind, ...]:
        """The remaining kinds, in the fixed kind order."""
        return tuple(kind for kind in TokenKind if kind not in self.kinds_matched)


@dataclass(frozen=True)
class AlertIdentity:
    """Which alert, about which case, on which loan."""

    alert_id: uuid.UUID
    registry_case_id: uuid.UUID
    loan_id: uuid.UUID


@dataclass(frozen=True)
class Review:
    """Who reviewed, when, with an optional note, and the position once recorded."""

    reviewed_by: uuid.UUID | None = None
    reviewed_at: datetime | None = None
    note: str | None = None
    bank_position: BankPosition | None = None
    position_submitted_at: datetime | None = None


@dataclass(frozen=True)
class AlertRecord:
    """Everything stored about an alert."""

    alert_id: uuid.UUID
    registry_case_id: uuid.UUID
    loan_id: uuid.UUID
    match: MatchFacts
    explanation_text: str
    explanation_source: ExplanationSource
    status: AlertStatus
    case_status: CaseStatus
    received_at: datetime
    review: Review


def _check_note(note: str | None) -> None:
    if note is not None and len(note) > MAX_NOTE_LENGTH:
        raise InvalidValueError("note", "TOO_LONG")


class Alert:
    """An alert with the transitions of SPEC 8.5."""

    def __init__(self, record: AlertRecord) -> None:
        self._record = record

    @classmethod
    def raise_new(
        cls,
        identity: AlertIdentity,
        match: MatchFacts,
        explanation: tuple[str, ExplanationSource],
        at: datetime,
    ) -> Alert:
        """(none) to NEW, case OPEN."""
        text, source = explanation
        return cls(
            AlertRecord(
                identity.alert_id,
                identity.registry_case_id,
                identity.loan_id,
                match,
                text,
                source,
                AlertStatus.NEW,
                CaseStatus.OPEN,
                at,
                Review(),
            )
        )

    @property
    def record(self) -> AlertRecord:
        """The current stored form."""
        return self._record

    @property
    def status(self) -> AlertStatus:
        """The bank side review status."""
        return self._record.status

    def start_review(self, reviewer: uuid.UUID, at: datetime, note: str | None = None) -> None:
        """NEW to REVIEWING (action START_REVIEW)."""
        _check_note(note)
        if self._record.status is not AlertStatus.NEW:
            raise AlertTransitionInvalidError("alert", self._record.status.value, AlertStatus.REVIEWING.value)
        review = Review(reviewer, at, note, None, None)
        self._record = replace(self._record, status=AlertStatus.REVIEWING, review=review)

    def submit_position(
        self, position: BankPosition, reviewer: uuid.UUID, at: datetime, note: str | None = None
    ) -> None:
        """REVIEWING to REVIEWING, recording the bank's position once (action SUBMIT_POSITION)."""
        _check_note(note)
        record = self._record
        if record.status is not AlertStatus.REVIEWING:
            raise AlertTransitionInvalidError("alert", record.status.value, AlertStatus.REVIEWING.value)
        recorded = record.review.bank_position
        if recorded is not None and recorded is not position:
            raise PositionAlreadyRecordedError
        submitted_at = record.review.position_submitted_at or at
        review = Review(
            reviewer, at, note if note is not None else record.review.note, position, submitted_at
        )
        self._record = replace(record, review=review)

    def apply_case_status(self, case_status: CaseStatus) -> bool:
        """A registry case update. Closes an open alert; returns False when nothing changed. A closed
        alert never takes another case status (SPEC 8.5: CLEARED and ESCALATED are terminal)."""
        record = self._record
        if case_status is record.case_status:
            return False
        if case_status is CaseStatus.OPEN or record.status not in _OPEN_STATUSES:
            raise IllegalTransitionError("alert_case", record.case_status.value, case_status.value)
        status = _CLOSING[case_status] if record.status in _OPEN_STATUSES else record.status
        self._record = replace(record, case_status=case_status, status=status)
        return True
