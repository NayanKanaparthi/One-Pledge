"""Statistics, state dump and tamper modes of the registry test double (TEST CONTROLS, SPEC 7.3).

None of this is part of the contract. The tamper modes exist so the bank agent's
integrity checks can be proven to detect a rewritten history, an edited event, a
forged checkpoint signature and an edited certificate.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from onepledge_contract_fakes.canonical_json import JsonValue
from onepledge_contract_fakes.errors import ErrorDetail, InvariantViolationError, ValidationFailedError
from onepledge_contract_fakes.registry.locks import Lock
from onepledge_contract_fakes.registry.model import CaseStatus
from onepledge_contract_fakes.registry.publisher import Publisher
from onepledge_contract_fakes.registry.stores import RegistryStores
from onepledge_contract_fakes.registry.submissions import SubmissionRecord
from onepledge_contract_fakes.validation import Issue, JsonFields, is_canonical_uuid

# Ledger sequence numbers fit the 18 digit integer rule of every layer 1 service.
MAX_SEQ = 10**18 - 1


class TamperMode(StrEnum):
    """The four ways the double can break its own integrity on purpose, and the undo of the fourth."""

    REWRITE_HISTORY = "REWRITE_HISTORY"
    EDIT_IN_PLACE = "EDIT_IN_PLACE"
    BAD_CHECKPOINT_SIGNATURE = "BAD_CHECKPOINT_SIGNATURE"
    CERTIFICATE_BODY = "CERTIFICATE_BODY"
    CERTIFICATE_RESTORE = "CERTIFICATE_RESTORE"


@dataclass(frozen=True)
class TamperRequest:
    """A validated `/_test/tamper` body."""

    mode: TamperMode
    seq: int | None = None
    certificate_id: str | None = None


def parse_tamper_request(document: object) -> TamperRequest:
    """Validate a tamper body: `seq` for the two ledger modes, `certificate_id` for the certificate modes."""
    mode_text = document.get("mode") if isinstance(document, dict) else None
    optional = {
        TamperMode.REWRITE_HISTORY.value: ("seq",),
        TamperMode.EDIT_IN_PLACE.value: ("seq",),
        TamperMode.CERTIFICATE_BODY.value: ("certificate_id",),
        TamperMode.CERTIFICATE_RESTORE.value: ("certificate_id",),
    }.get(mode_text if isinstance(mode_text, str) else "", ())
    fields = JsonFields(document, required=("mode", *optional))
    mode = fields.choice("mode", [m.value for m in TamperMode])
    seq = fields.integer("seq", 1, MAX_SEQ) if "seq" in optional else None
    certificate_id = fields.string("certificate_id") if "certificate_id" in optional else None
    if certificate_id is not None and not is_canonical_uuid(certificate_id):
        fields.reject("certificate_id", Issue.PATTERN_MISMATCH)
    fields.finish()
    if mode is None:
        raise InvariantViolationError
    return TamperRequest(mode=TamperMode(mode), seq=seq, certificate_id=certificate_id)


def _lock_json(lock: Lock, active: bool) -> dict[str, JsonValue]:
    return {
        "lock_id": lock.lock_id,
        "member_id": lock.member_id,
        "asset_token": lock.asset_token,
        "field_tokens": dict(lock.field_tokens),
        "acquired_seq": lock.acquired_seq,
        "submission_id": lock.submission_id,
        "active": active,
    }


class RegistryInspector:
    """Read and tamper access to the double's internals for the test controls."""

    def __init__(self, member_ids: tuple[str, ...], stores: RegistryStores, publisher: Publisher) -> None:
        self._member_ids = member_ids
        self._ledger = stores.ledger
        self._locks = stores.locks
        self._cases = stores.cases
        self._certificates = stores.certificates
        self._checkpoints = stores.checkpoints
        self._publisher = publisher

    def stats(self, submissions: Sequence[SubmissionRecord]) -> dict[str, JsonValue]:
        """Counts in total and per member."""
        open_cases = [c for c in self._cases.all() if c.status == CaseStatus.OPEN]
        by_member: dict[str, JsonValue] = {}
        for member_id in self._member_ids:
            own = [s for s in submissions if s.member_id == member_id]
            by_member[member_id] = {
                "submissions": len(own),
                "items": sum(s.item_count for s in own),
                "active_locks": self._locks.count_for(member_id),
                "open_cases": sum(1 for c in open_cases if member_id in c.parties()),
            }
        total: dict[str, JsonValue] = {
            "submissions": len(submissions),
            "items": sum(s.item_count for s in submissions),
            "active_locks": len(self._locks.active()),
            "open_cases": len(open_cases),
            "events": self._ledger.head_seq,
            "checkpoints": len(self._checkpoints.all()),
        }
        return {"total": total, "by_member": by_member}

    def dump(self) -> dict[str, JsonValue]:
        """Members, locks with field codes, cases, events with payloads, certificates, checkpoints."""
        cases: list[JsonValue] = []
        for case in self._cases.all():
            cases.append(
                {
                    "case_id": case.case_id,
                    "lock_id": case.lock_id,
                    "holder_member_id": case.holder_member_id,
                    "claimant_member_id": case.claimant_member_id,
                    "holder_asset_token": case.holder_asset_token,
                    "claimant_asset_token": case.claimant_asset_token,
                    "match_kind": case.match_kind.value,
                    "match_basis": case.match_basis.value,
                    "kinds_matched": list(case.kinds_matched),
                    "status": case.status.value,
                    "positions": {member: position.value for member, position in case.positions.items()},
                    "opened_seq": case.opened_seq,
                    "resolved_seq": case.resolved_seq,
                }
            )
        locks: list[JsonValue] = [_lock_json(lock, True) for lock in self._locks.active()]
        locks.extend(_lock_json(lock, False) for lock in self._locks.released())
        return {
            "members": list(self._member_ids),
            "locks": locks,
            "cases": cases,
            "events": [event.full() for event in self._ledger.all()],
            "certificates": [stored.to_json() for stored in self._certificates.all()],
            "checkpoints": [checkpoint.to_json() for checkpoint in self._checkpoints.all()],
        }

    def tamper(self, request: TamperRequest) -> dict[str, JsonValue]:
        """Apply one tamper mode and describe what changed."""
        if request.mode == TamperMode.REWRITE_HISTORY and request.seq is not None:
            self._ledger.rewrite_history(request.seq)
            checkpoint = self._publisher.checkpoint()
            return {"mode": request.mode.value, "seq": request.seq, "checkpoint": checkpoint.to_json()}
        if request.mode == TamperMode.EDIT_IN_PLACE and request.seq is not None:
            self._ledger.edit_in_place(request.seq)
            return {"mode": request.mode.value, "seq": request.seq}
        if request.mode == TamperMode.BAD_CHECKPOINT_SIGNATURE:
            checkpoint = self._publisher.checkpoint(corrupt_signature=True)
            return {"mode": request.mode.value, "checkpoint": checkpoint.to_json()}
        if request.mode == TamperMode.CERTIFICATE_BODY and request.certificate_id is not None:
            self._certificates.tamper_locked_count(request.certificate_id)
            return {"mode": request.mode.value, "certificate_id": request.certificate_id}
        if request.mode == TamperMode.CERTIFICATE_RESTORE and request.certificate_id is not None:
            restored = self._certificates.restore(request.certificate_id)
            return {
                "mode": request.mode.value,
                "certificate_id": request.certificate_id,
                "restored": restored,
            }
        raise ValidationFailedError(details=(ErrorDetail("mode", Issue.INVALID_VALUE),))
