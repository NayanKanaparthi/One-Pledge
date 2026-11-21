"""Deciding one submission atomically, items in request order (TEST DOUBLE, SPEC 7.2).

Events per submission: LOCK_ACQUIRED per new lock; CONFLICT_DETECTED and
CASE_OPENED per new case; LOCK_RELEASED per release; CASE_RESOLVED per case closed
by that release; then one CERTIFICATE_ISSUED, whose seq is the `as_of_seq`; then a
checkpoint over the head and its CHECKPOINT_SIGNED event.
"""

from __future__ import annotations

from dataclasses import dataclass

from onepledge_contract_fakes.canonical_json import JsonValue
from onepledge_contract_fakes.clock import IdSource
from onepledge_contract_fakes.errors import ErrorDetail, InvariantViolationError, ValidationFailedError
from onepledge_contract_fakes.registry.certificates import CertificateFacts
from onepledge_contract_fakes.registry.locks import Lock
from onepledge_contract_fakes.registry.matching import AlreadyHeld, Free, Match, MatchingRules
from onepledge_contract_fakes.registry.model import (
    CaseStatus,
    EventType,
    ItemOutcome,
    ItemResult,
    Submission,
    SubmissionItem,
    SubmissionKind,
)
from onepledge_contract_fakes.registry.publisher import Publisher
from onepledge_contract_fakes.registry.stores import RegistryStores

NOT_HOLDER = "NOT_HOLDER"


def validate_submission(submission: Submission) -> None:
    """Semantic rules a schema cannot express: unique codes and refs, field codes by kind."""
    details: list[ErrorDetail] = []
    seen_assets: set[str] = set()
    seen_refs: set[str] = set()
    for index, item in enumerate(submission.items):
        if item.asset_token in seen_assets:
            details.append(ErrorDetail(f"items[{index}].asset_token", "DUPLICATE_ASSET_TOKEN"))
        if item.item_ref in seen_refs:
            details.append(ErrorDetail(f"items[{index}].item_ref", "DUPLICATE_ITEM_REF"))
        seen_assets.add(item.asset_token)
        seen_refs.add(item.item_ref)
        if submission.kind == SubmissionKind.RELEASE and item.field_tokens is not None:
            details.append(ErrorDetail(f"items[{index}].field_tokens", "NOT_ALLOWED_FOR_RELEASE"))
        if submission.kind != SubmissionKind.RELEASE and item.field_tokens is None:
            details.append(ErrorDetail(f"items[{index}].field_tokens", "MISSING"))
    if details:
        raise ValidationFailedError(details=tuple(details))


@dataclass(frozen=True)
class SubmissionRecord:
    """A decided submission as stored by the double."""

    submission_id: str
    member_id: str
    item_count: int
    result: dict[str, JsonValue]


class SubmissionProcessor:
    """Applies the decision rules to one submission and records everything it causes."""

    def __init__(self, stores: RegistryStores, publisher: Publisher, ids: IdSource) -> None:
        self._ledger = stores.ledger
        self._locks = stores.locks
        self._cases = stores.cases
        self._certificates = stores.certificates
        self._publisher = publisher
        self._ids = ids
        self._rules = MatchingRules(stores.locks, stores.cases.is_exempt)

    def process(self, member_id: str, submission: Submission) -> SubmissionRecord:
        """Decide every item, issue the certificate and sign a checkpoint."""
        received_at = self._publisher.now()
        submission_id = self._ids.new_id()
        results = [self._decide(member_id, submission_id, submission.kind, item) for item in submission.items]
        certificate_id = self._ids.new_id()
        facts = CertificateFacts(
            certificate_id=certificate_id,
            member_id=member_id,
            submission_id=submission_id,
            kind=submission.kind.value,
            key_version=submission.key_version,
            as_of_seq=self._ledger.head_seq + 1,
            issued_at=self._publisher.now(),
        )
        stored = self._certificates.issue(facts, results)
        as_of_seq = self._publisher.event(
            EventType.CERTIFICATE_ISSUED,
            {
                "certificate_id": certificate_id,
                "member_id": member_id,
                "submission_id": submission_id,
                "body_hash": stored.body_hash,
            },
        )
        self._publisher.checkpoint()
        result: dict[str, JsonValue] = {
            "submission_id": submission_id,
            "member_id": member_id,
            "kind": submission.kind.value,
            "key_version": submission.key_version,
            "status": "COMPLETED",
            "received_at": received_at,
            "as_of_seq": as_of_seq,
            "certificate_id": certificate_id,
            "results": [r.to_json() for r in results],
        }
        return SubmissionRecord(submission_id, member_id, len(results), result)

    def _decide(
        self, member_id: str, submission_id: str, kind: SubmissionKind, item: SubmissionItem
    ) -> ItemResult:
        if kind == SubmissionKind.RELEASE:
            return self._release(member_id, submission_id, item)
        if item.field_tokens is None:
            raise InvariantViolationError
        decision = self._rules.decide(member_id, item.asset_token, item.field_tokens)
        if isinstance(decision, AlreadyHeld):
            return ItemResult(
                item.item_ref, item.asset_token, ItemOutcome.LOCKED, decision.lock.lock_id, True
            )
        if isinstance(decision, Free):
            return self._lock(member_id, submission_id, item)
        return self._conflict(member_id, submission_id, item, decision)

    def _lock(self, member_id: str, submission_id: str, item: SubmissionItem) -> ItemResult:
        field_tokens = dict(item.field_tokens or {})
        lock_id = self._ids.new_id()
        seq = self._publisher.event(
            EventType.LOCK_ACQUIRED,
            {
                "lock_id": lock_id,
                "member_id": member_id,
                "asset_token": item.asset_token,
                "field_tokens": dict(field_tokens),
                "submission_id": submission_id,
            },
        )
        self._locks.acquire(Lock(lock_id, member_id, item.asset_token, field_tokens, seq, submission_id))
        return ItemResult(item.item_ref, item.asset_token, ItemOutcome.LOCKED, lock_id, False)

    def _conflict(self, member_id: str, submission_id: str, item: SubmissionItem, match: Match) -> ItemResult:
        """A CONFLICT result carrying this item's own match against the lock (registry.yaml).

        A repeated conflict of the same claimant with the same lock reuses the OPEN case
        (no new event, no new alert); the stored case keeps the match it was opened with.
        """
        case = self._cases.open_case_for(member_id, item.asset_token, match.lock.lock_id)
        if case is None:
            self._publisher.event(
                EventType.CONFLICT_DETECTED,
                {
                    "submission_id": submission_id,
                    "claimant_member_id": member_id,
                    "claimant_asset_token": item.asset_token,
                    "lock_id": match.lock.lock_id,
                    "match_kind": match.kind.value,
                    "match_basis": match.basis.value,
                    "kinds_matched": list(match.kinds_matched),
                },
            )
            case_id = self._ids.new_id()
            opened_seq = self._publisher.event(
                EventType.CASE_OPENED,
                {
                    "case_id": case_id,
                    "lock_id": match.lock.lock_id,
                    "holder_member_id": match.lock.member_id,
                    "claimant_member_id": member_id,
                    "claimant_asset_token": item.asset_token,
                    "match_kind": match.kind.value,
                    "match_basis": match.basis.value,
                    "kinds_matched": list(match.kinds_matched),
                },
            )
            case = self._cases.open(case_id, member_id, item.asset_token, match, opened_seq)
            self._publisher.alerts(case)
        return ItemResult(
            item_ref=item.item_ref,
            asset_token=item.asset_token,
            result=ItemOutcome.CONFLICT,
            match_kind=match.kind,
            match_basis=match.basis,
            kinds_matched=match.kinds_matched,
            case_id=case.case_id,
        )

    def _release(self, member_id: str, submission_id: str, item: SubmissionItem) -> ItemResult:
        lock = self._locks.holder(item.asset_token)
        if lock is None or lock.member_id != member_id:
            return ItemResult(item.item_ref, item.asset_token, ItemOutcome.REJECTED, reason=NOT_HOLDER)
        self._publisher.event(
            EventType.LOCK_RELEASED,
            {
                "lock_id": lock.lock_id,
                "member_id": member_id,
                "asset_token": lock.asset_token,
                "submission_id": submission_id,
            },
        )
        self._locks.release(lock)
        late_release = CaseStatus.CLEARED_LATE_RELEASE
        for case in self._cases.open_on_lock(lock.lock_id):
            resolved_seq = self._publisher.event(
                EventType.CASE_RESOLVED, {"case_id": case.case_id, "status": late_release.value}
            )
            self._cases.resolve(case, late_release, resolved_seq)
            self._publisher.case_updates(case)
        return ItemResult(item.item_ref, item.asset_token, ItemOutcome.RELEASED, lock.lock_id, False)
