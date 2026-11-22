"""`ScriptedRegistry`: the `RegistryClient` port as a small registry in memory, scriptable per call.

It decides items the way SPEC 13.5 and the registry double do (a new asset code LOCKED,
one this bank holds LOCKED again, one another bank holds CONFLICT with a case, a release
RELEASED or REJECTED NOT_HELD), keeps one decision per idempotency key and answers a
repeat as a 200 replay, refuses a reused key with another body, certifies every decision
with a signed certificate, keeps a hash chained ledger of event headers, signs
checkpoints, and delivers outbox messages until they are acknowledged.

Scripting: `fail(method, error)` raises on the next call of a method (`after=True` for
`submit` stores the decision first, a lost answer); `rejects`, `unknown`, `held_elsewhere`
decide single asset codes; `tamper` and `restore` change and restore a certificate;
`mismatch` makes the next answer name another asset code; `refuse_key_version` answers
409 KEY_VERSION_MISMATCH.
"""

from __future__ import annotations

import copy
import hashlib
import uuid
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum

from bank_testkit.memory.signatures import REGISTRY_KEY_ID, sign
from onepledge_bank_agent.domain.canonical_json import ZERO_HASH, JsonValue, canonical_json, canonical_sha256
from onepledge_bank_agent.domain.certificate_rules import CERTIFICATE_WORDS
from onepledge_bank_agent.domain.checkpoint_rules import event_header_hash
from onepledge_bank_agent.domain.dependency_errors import (
    CaseAlreadyResolvedError,
    KeyVersionMismatchError,
    RegistryRejectedError,
)
from onepledge_bank_agent.domain.enums import (
    AlertRole,
    BankPosition,
    CaseStatus,
    FailureReason,
    MatchBasis,
    MatchKind,
    RegistryItemResult,
    TapeKind,
    TokenKind,
)
from onepledge_bank_agent.domain.errors import PipelineStageError, PositionAlreadyRecordedError
from onepledge_bank_agent.domain.registry_records import (
    AlertNotice,
    CaseNotice,
    CaseOutcome,
    EventHeader,
    EventPage,
    ItemOutcome,
    MessagePayload,
    OutboxMessage,
    OutboxPage,
    RegistryAnswer,
    RegistrySubmission,
    SignedCertificate,
    SignedCheckpoint,
)
from onepledge_bank_agent.domain.values import KeyVersion

AT = datetime(2026, 11, 20, 12, tzinfo=UTC)
AT_TEXT = "2026-11-20T12:00:00Z"


class Tamper(StrEnum):
    """How a stored certificate is broken, one SPEC 15.2 reason each."""

    BODY = "BODY"
    SIGNATURE = "SIGNATURE"
    KEY = "KEY"
    CONTENT = "CONTENT"


@dataclass
class _Script:
    error: PipelineStageError
    after: bool


def _submission_json(submission: RegistrySubmission) -> bytes:
    items: list[JsonValue] = [
        {
            "item_ref": str(item.item_ref),
            "asset_token": item.asset_token,
            "field_tokens": {k.value: v for k, v in sorted((item.field_tokens or {}).items())},
        }
        for item in submission.items
    ]
    return canonical_json(
        {"kind": submission.kind.value, "key_version": submission.key_version, "items": items}
    )


@dataclass
class ScriptedRegistry:
    """A registry for one member bank."""

    member_id: uuid.UUID
    key_id: str = REGISTRY_KEY_ID
    rejects: dict[str, str] = field(default_factory=dict)
    unknown: set[str] = field(default_factory=set)
    held_elsewhere: set[str] = field(default_factory=set)
    mismatch: bool = False
    refuse_key_version: bool = False
    resolved: set[uuid.UUID] = field(default_factory=set)
    case_outcomes: dict[uuid.UUID, CaseStatus] = field(default_factory=dict)
    locks: dict[str, uuid.UUID] = field(default_factory=dict)
    cases: dict[uuid.UUID, str] = field(default_factory=dict)
    positions: dict[uuid.UUID, BankPosition] = field(default_factory=dict)
    calls: Counter[str] = field(default_factory=Counter)
    decisions: int = 0
    _scripts: dict[str, list[_Script | None]] = field(default_factory=dict)
    _answers: dict[str, tuple[bytes, RegistryAnswer]] = field(default_factory=dict)
    _certificates: dict[uuid.UUID, SignedCertificate] = field(default_factory=dict)
    _originals: dict[uuid.UUID, SignedCertificate] = field(default_factory=dict)
    _events: list[EventHeader] = field(default_factory=list)
    _outbox: list[OutboxMessage] = field(default_factory=list)
    _checkpoint_seq: int = 0
    _ids: int = 0

    # -- scripting -----------------------------------------------------------------------------

    def fail(
        self, method: str, error: PipelineStageError, *, after: bool = False, times: int = 1, skip: int = 0
    ) -> None:
        """Raise `error` on `times` calls of `method` after `skip` good ones (`after`: store first)."""
        queue = self._scripts.setdefault(method, [])
        queue.extend(None for _ in range(skip))
        queue.extend(_Script(error, after) for _ in range(times))

    def _next(self, method: str) -> _Script | None:
        self.calls[method] += 1
        queue = self._scripts.get(method, [])
        return queue.pop(0) if queue else None

    def _before(self, method: str) -> _Script | None:
        script = self._next(method)
        if script is not None and not script.after:
            raise script.error
        return script

    def new_id(self) -> uuid.UUID:
        """A deterministic id of this registry."""
        self._ids += 1
        return uuid.UUID(int=(0xA << 124) + self._ids, version=4)

    # -- submissions and certificates --------------------------------------------------------

    def submit(
        self, idempotency_key: str, submission: RegistrySubmission, before_each: Callable[[], None]
    ) -> RegistryAnswer:
        """Decide once per key; a repeat with the same body is a 200 replay."""
        before_each()
        script = self._before("submit")
        if self.refuse_key_version:
            raise KeyVersionMismatchError(409)
        body = _submission_json(submission)
        if idempotency_key in self._answers:
            stored_body, answer = self._answers[idempotency_key]
            if stored_body != body:
                raise RegistryRejectedError(409, FailureReason.REQUEST_REFUSED)
            answer = replace(answer, status=200, replay=True)
        else:
            answer = self._decide(submission)
            self._answers[idempotency_key] = (body, answer)
        if script is not None:
            raise script.error
        if self.mismatch:
            self.mismatch = False
            first = answer.results[0]
            return replace(answer, results=(replace(first, asset_token="f" * 64), *answer.results[1:]))
        return answer

    def _decide(self, submission: RegistrySubmission) -> RegistryAnswer:
        results = tuple(
            self._result(submission.kind, item.item_ref, item.asset_token) for item in submission.items
        )
        self.decisions += 1
        self.append_event("SUBMISSION_DECIDED")
        certificate_id, submission_id = self.new_id(), self.new_id()
        as_of_seq = len(self._events)
        answer = RegistryAnswer(
            status=201, replay=False, submission_id=submission_id, member_id=self.member_id,
            kind=submission.kind, key_version=KeyVersion.parse(submission.key_version), as_of_seq=as_of_seq,
            certificate_id=certificate_id, results=results,
            body={"submission_id": str(submission_id), "certificate_id": str(certificate_id)},
        )  # fmt: skip
        self._certify(answer)
        return answer

    def _result(self, kind: TapeKind, item_ref: uuid.UUID, asset: str) -> ItemOutcome:
        plain = ItemOutcome(item_ref, asset, "", None, False, None, None, (), None, None)
        if kind is TapeKind.RELEASE:
            if asset in self.locks:
                return replace(plain, result=RegistryItemResult.RELEASED.value, lock_id=self.locks.pop(asset))
            return replace(plain, result=RegistryItemResult.REJECTED.value, reason="NOT_HELD")
        if asset in self.rejects:
            return replace(plain, result=RegistryItemResult.REJECTED.value, reason=self.rejects[asset])
        if asset in self.unknown:
            return replace(plain, result="WITHDRAWN")
        if asset in self.held_elsewhere:
            case_id = self.new_id()
            self.cases[case_id] = asset
            return replace(
                plain,
                result=RegistryItemResult.CONFLICT.value,
                case_id=case_id,
                match_kind=MatchKind.EXACT,
                match_basis=MatchBasis.ASSET,
                kinds_matched=(TokenKind.ASSET,),
            )
        held = asset in self.locks
        lock_id = self.locks.setdefault(asset, self.new_id())
        return replace(plain, result=RegistryItemResult.LOCKED.value, lock_id=lock_id, already_held=held)

    def _certify(self, answer: RegistryAnswer) -> None:
        words = [CERTIFICATE_WORDS.get(_known(outcome.result), "REJECTED") for outcome in answer.results]
        counts = Counter(words)
        body: dict[str, JsonValue] = {
            "certificate_id": str(answer.certificate_id),
            "member_id": str(self.member_id),
            "submission_id": str(answer.submission_id),
            "kind": answer.kind.value,
            "key_version": str(answer.key_version),
            "as_of_seq": answer.as_of_seq,
            "issued_at": AT_TEXT,
            "summary": {
                "checked": len(words),
                "locked": counts["LOCKED"],
                "held": counts["HELD"],
                "released": counts["RELEASED"],
                "rejected": counts["REJECTED"],
            },
            "items": [
                {"asset_token": o.asset_token, "status": w}
                for o, w in zip(answer.results, words, strict=True)
            ],
        }
        signed = self._signed(body, self.key_id)
        self._certificates[answer.certificate_id] = signed
        self._originals[answer.certificate_id] = copy.deepcopy(signed)

    def _signed(self, body: Mapping[str, JsonValue], key_id: str) -> SignedCertificate:
        message = canonical_json(dict(body))
        return SignedCertificate(
            dict(body), hashlib.sha256(message).hexdigest(), sign(key_id, message), key_id, AT
        )

    def certificate(self, certificate_id: uuid.UUID) -> SignedCertificate:
        """The certificate as stored now (tampered or not)."""
        self._before("certificate")
        found = self._certificates.get(certificate_id)
        if found is None:
            raise RegistryRejectedError(404, FailureReason.REQUEST_REFUSED)
        return copy.deepcopy(found)

    def certificate_ids(self) -> list[uuid.UUID]:
        """Every certificate issued, in order."""
        return list(self._certificates)

    def tamper(self, certificate_id: uuid.UUID, how: Tamper) -> None:
        """Break one stored certificate so it fails with one reason of SPEC 15.2."""
        signed = self._certificates[certificate_id]
        body = copy.deepcopy(dict(signed.body))
        if how is Tamper.BODY:
            summary = body["summary"]
            assert isinstance(summary, dict)
            summary["locked"] = int(summary["locked"]) + 1
            self._certificates[certificate_id] = replace(signed, body=body)
        elif how is Tamper.SIGNATURE:
            flipped = ("A" if signed.signature[0] != "A" else "B") + signed.signature[1:]
            self._certificates[certificate_id] = replace(signed, signature=flipped)
        elif how is Tamper.KEY:
            self._certificates[certificate_id] = self._signed(body, "registry-unknown-9")
        else:
            body["as_of_seq"] = int(str(body["as_of_seq"])) + 1
            self._certificates[certificate_id] = self._signed(body, self.key_id)

    def restore(self, certificate_id: uuid.UUID) -> None:
        """Put the certificate back exactly as first signed."""
        self._certificates[certificate_id] = copy.deepcopy(self._originals[certificate_id])

    # -- ledger, checkpoints and outbox ---------------------------------------------------------

    def append_event(self, event_type: str) -> EventHeader:
        """One more event on the hash chained ledger."""
        seq = len(self._events) + 1
        prev_hash = self._events[-1].event_hash if self._events else ZERO_HASH
        payload = canonical_sha256({"seq": seq, "type": event_type})
        header = EventHeader(seq, event_type, payload, prev_hash, ZERO_HASH, AT_TEXT, 1)
        header = replace(header, event_hash=event_header_hash(header))
        self._events.append(header)
        return header

    def rewrite_event(self, seq: int) -> None:
        """A history rewrite: event `seq` gets another payload, its hash recomputed, the chain not."""
        event = self._events[seq - 1]
        changed = replace(event, payload_hash="d" * 64)
        self._events[seq - 1] = replace(changed, event_hash=event_header_hash(changed))

    def event_headers(self, after_seq: int, limit: int) -> EventPage:
        """Headers after a seq, up to `limit`, with the head seq."""
        self._before("event_headers")
        events = tuple(event for event in self._events if event.seq > after_seq)[:limit]
        return EventPage(events, len(self._events))

    def checkpoint(
        self, *, key_id: str | None = None, bad_signature: bool = False, **body_changes: JsonValue
    ) -> SignedCheckpoint:
        """Sign the head (or a forged body) and deliver it as a CHECKPOINT message."""
        self._checkpoint_seq += 1
        head = self._events[-1] if self._events else None
        body: dict[str, JsonValue] = {
            "schema_version": 1,
            "checkpoint_seq": self._checkpoint_seq,
            "last_event_seq": head.seq if head else 0,
            "head_hash": head.event_hash if head else ZERO_HASH,
            "signed_at": AT_TEXT,
            **body_changes,
        }
        signing_key = key_id or self.key_id
        signature = sign(signing_key, canonical_json(body))
        if bad_signature:
            signature = ("A" if signature[0] != "A" else "B") + signature[1:]
        seq, last, head_hash = body["checkpoint_seq"], body["last_event_seq"], body["head_hash"]
        assert isinstance(seq, int) and isinstance(last, int) and isinstance(head_hash, str)
        signed = SignedCheckpoint(body, signature, signing_key, seq, last, head_hash, AT)
        self.deliver("CHECKPOINT", signed)
        return signed

    def deliver(self, message_type: str, payload: MessagePayload) -> OutboxMessage:
        """Queue one outbox message."""
        position = max((message.position for message in self._outbox), default=0) + 1
        message = OutboxMessage(self.new_id(), position, message_type, payload)
        self._outbox.append(message)
        return message

    def alert(self, asset_token: str, role: AlertRole = AlertRole.HOLDER) -> uuid.UUID:
        """Deliver an ALERT about one of this bank's asset codes; returns the case id."""
        case_id = self.new_id()
        self.cases[case_id] = asset_token
        notice = AlertNotice(
            case_id,
            role,
            asset_token,
            MatchKind.EXACT,
            MatchBasis.ASSET,
            (TokenKind.ASSET,),
            len(self._events) + 1,
        )
        self.deliver("ALERT", notice)
        return case_id

    def case_update(self, case_id: uuid.UUID, status: CaseStatus) -> None:
        """Deliver a CASE_UPDATE for a case."""
        self.deliver("CASE_UPDATE", CaseNotice(case_id, self.cases[case_id], status, len(self._events)))

    def outbox(self, limit: int) -> OutboxPage:
        """The unacknowledged messages in position order."""
        self._before("outbox")
        return OutboxPage(tuple(self._outbox[:limit]), len(self._outbox) > limit)

    def acknowledge(self, message_id: uuid.UUID) -> None:
        """Remove one message (also when it was acknowledged before)."""
        self._before("acknowledge")
        self._outbox = [message for message in self._outbox if message.message_id != message_id]

    def pending(self) -> list[OutboxMessage]:
        """The messages not yet acknowledged."""
        return list(self._outbox)

    # -- cases ----------------------------------------------------------------------------------

    def record_position(self, case_id: uuid.UUID, position: BankPosition) -> CaseOutcome:
        """Record this bank's position; a resolved case or a changed position is refused."""
        self._before("record_position")
        if case_id in self.resolved:
            raise CaseAlreadyResolvedError(409)
        if self.positions.get(case_id, position) is not position:
            raise PositionAlreadyRecordedError
        self.positions[case_id] = position
        return CaseOutcome(case_id, self.case_outcomes.get(case_id, CaseStatus.OPEN), position)


def _known(result: str) -> RegistryItemResult:
    try:
        return RegistryItemResult(result)
    except ValueError:
        return RegistryItemResult.REJECTED
