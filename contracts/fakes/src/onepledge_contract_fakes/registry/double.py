"""In-memory registry double (TEST DOUBLE of `contracts/registry.yaml`, SPEC 7.2).

All behaviour and state sit behind one re-entrant lock, so every submission is
decided atomically and two members racing for the same asset code always end with
exactly one lock. A member never sees another member's submissions, certificates,
cases or messages: asking for one gives 404, so existence is not revealed.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass

from onepledge_contract_fakes.canonical_json import JsonValue
from onepledge_contract_fakes.clock import Clock, IdSource
from onepledge_contract_fakes.daily_cap import DailyCap
from onepledge_contract_fakes.errors import (
    ForbiddenError,
    KeyVersionMismatchError,
    SubmissionNotFoundError,
)
from onepledge_contract_fakes.members import MemberDirectory, key_matches
from onepledge_contract_fakes.registry.config import RegistryConfig
from onepledge_contract_fakes.registry.idempotency import IdempotencyStore, StoredResponse
from onepledge_contract_fakes.registry.inspection import RegistryInspector, TamperRequest
from onepledge_contract_fakes.registry.model import CaseStatus, EventType, Position, Submission
from onepledge_contract_fakes.registry.publisher import Publisher
from onepledge_contract_fakes.registry.signing import RegistrySigner
from onepledge_contract_fakes.registry.stores import RegistryStores
from onepledge_contract_fakes.registry.submissions import (
    SubmissionProcessor,
    SubmissionRecord,
    validate_submission,
)


@dataclass(frozen=True)
class SubmitOutcome:
    """The HTTP status and exact bytes of a submission response."""

    status: int
    body: bytes
    replay: bool


class RegistryDouble:
    """The registry double: locks, rules, cases, certificates, ledger, checkpoints, outbox."""

    def __init__(self, config: RegistryConfig, clock: Clock, ids: IdSource) -> None:
        self._config = config
        self._lock = threading.RLock()
        self._members = MemberDirectory(config.members)
        self._signer = RegistrySigner(config.signing_seed, config.signing_key_id)
        stores = RegistryStores(self._signer)
        self._ledger = stores.ledger
        self._cases = stores.cases
        self._outbox = stores.outbox
        self._checkpoints = stores.checkpoints
        self._certificates = stores.certificates
        self._idempotency = IdempotencyStore()
        self._cap = DailyCap(config.daily_item_cap, clock)
        self._publisher = Publisher(stores, self._members.member_ids, clock, ids)
        self._processor = SubmissionProcessor(stores, self._publisher, ids)
        self._submissions: dict[str, SubmissionRecord] = {}
        self._inspector = RegistryInspector(self._members.member_ids, stores, self._publisher)
        for member_id in self._members.member_ids:
            self._publisher.event(EventType.MEMBER_JOINED, {"member_id": member_id})
        self._publisher.checkpoint()

    @property
    def public_key_b64(self) -> str:
        """The verification key banks are given out of band."""
        return self._signer.public_key_b64()

    @property
    def signing_key_id(self) -> str:
        """The id of the signing key."""
        return self._signer.key_id

    def authenticate(self, api_key: str | None) -> str:
        """The member id of a member key, or UnauthenticatedError."""
        return self._members.authenticate(api_key)

    def identify(self, api_key: str | None) -> str | None:
        """The member id of a member key, or None. Used only to label received bodies."""
        return self._members.identify(api_key)

    def reader_role(self, api_key: str | None) -> str:
        """`auditor` or `member` for the ledger read endpoints, or UnauthenticatedError."""
        if key_matches(api_key, self._config.auditor_key_sha256):
            return "auditor"
        self._members.authenticate(api_key)
        return "member"

    def submit(
        self, member_id: str, idempotency_key: str, submission: Submission, body_sha256: str
    ) -> SubmitOutcome:
        """Decide a submission, or replay the stored response of its idempotency key."""
        with self._lock:
            replay = self._idempotency.replay(member_id, idempotency_key, body_sha256)
            if replay is not None:
                return SubmitOutcome(200, replay, replay=True)
            validate_submission(submission)
            if submission.key_version != self._config.key_version:
                raise KeyVersionMismatchError
            self._cap.consume(member_id, len(submission.items))
            record = self._processor.process(member_id, submission)
            body = json.dumps(record.result, separators=(",", ":")).encode("utf-8")
            self._submissions[record.submission_id] = record
            self._idempotency.store(
                member_id, idempotency_key, StoredResponse(body_sha256, body, record.submission_id)
            )
            return SubmitOutcome(201, body, replay=False)

    def submission(self, member_id: str, submission_id: str) -> dict[str, JsonValue]:
        """A stored submission result of this member."""
        with self._lock:
            record = self._submissions.get(submission_id)
            if record is None or record.member_id != member_id:
                raise SubmissionNotFoundError
            return record.result

    def outbox(self, member_id: str, limit: int) -> dict[str, JsonValue]:
        """The `OutboxPage` of unacknowledged messages."""
        with self._lock:
            messages, has_more = self._outbox.pending(member_id, limit)
            return {"messages": [m.to_json() for m in messages], "has_more": has_more}

    def ack(self, member_id: str, message_id: str) -> None:
        """Acknowledge one message."""
        with self._lock:
            self._outbox.ack(member_id, message_id)

    def cases(
        self, member_id: str, status: CaseStatus | None, limit: int, offset: int
    ) -> dict[str, JsonValue]:
        """The `CasePage` of this member's cases."""
        with self._lock:
            own = [c for c in self._cases.of_member(member_id) if status is None or c.status == status]
            page = own[offset : offset + limit]
            return {
                "items": [c.view_for(member_id) for c in page],
                "total": len(own),
                "limit": limit,
                "offset": offset,
            }

    def record_position(self, member_id: str, case_id: str, position: Position) -> dict[str, JsonValue]:
        """Record a party's position and resolve the case when both agree."""
        with self._lock:
            case = self._cases.for_member(case_id, member_id)
            resolution = self._cases.record_position(case, member_id, position)
            if resolution is not None:
                seq = self._publisher.event(
                    EventType.CASE_RESOLVED, {"case_id": case.case_id, "status": resolution.value}
                )
                self._cases.resolve(case, resolution, seq)
                self._publisher.case_updates(case)
            return case.view_for(member_id)

    def certificate(self, member_id: str, certificate_id: str) -> dict[str, JsonValue]:
        """A signed certificate of this member."""
        with self._lock:
            return self._certificates.for_member(certificate_id, member_id).to_json()

    def ledger_events(self, after_seq: int, limit: int, full: bool, reader: str) -> dict[str, JsonValue]:
        """The `LedgerEventPage`. Payloads need the auditor key."""
        if full and reader != "auditor":
            raise ForbiddenError
        with self._lock:
            events = self._ledger.after(after_seq, limit)
            rendered: list[JsonValue] = [e.full() if full else e.header() for e in events]
            return {"events": rendered, "head_seq": self._ledger.head_seq}

    def latest_checkpoint(self) -> dict[str, JsonValue]:
        """The newest signed checkpoint."""
        with self._lock:
            return self._checkpoints.latest().to_json()

    def stats(self) -> dict[str, JsonValue]:
        """Totals and per member counts for `/_test/stats`."""
        with self._lock:
            return self._inspector.stats(tuple(self._submissions.values()))

    def dump(self) -> dict[str, JsonValue]:
        """Full state for `/_test/dump`: codes and registry identifiers only."""
        with self._lock:
            return self._inspector.dump()

    def tamper(self, request: TamperRequest) -> dict[str, JsonValue]:
        """Apply one tamper mode for the integrity tests."""
        with self._lock:
            return self._inspector.tamper(request)

    def checkpoint_now(self) -> dict[str, JsonValue]:
        """Sign and distribute a checkpoint now."""
        with self._lock:
            return self._publisher.checkpoint().to_json()
