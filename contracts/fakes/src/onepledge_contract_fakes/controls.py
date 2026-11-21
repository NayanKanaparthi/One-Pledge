"""State behind the `/_test/` endpoints of both doubles (TEST DOUBLES, SPEC 7.3).

These controls exist only when `FAKE_TEST_CONTROLS=true`. They are not part of
any contract: they let tests and the layer 1 scorecard see what a double
received and make it fail on purpose.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from enum import StrEnum

from onepledge_contract_fakes.errors import ErrorDetail, ValidationFailedError

INJECTABLE_STATUSES = frozenset({429, 500, 502, 503, 504})
MAX_INJECTED_FAILURES = 1000
SUBMISSIONS_ROUTE = "/v1/submissions"
_REQUIRED_FIELDS = frozenset({"count", "status", "when"})
_OPTIONAL_FIELDS = frozenset({"path"})


class FailureWhen(StrEnum):
    """When an injected failure takes effect."""

    BEFORE_PROCESSING = "BEFORE_PROCESSING"
    AFTER_COMMIT = "AFTER_COMMIT"


@dataclass(frozen=True)
class ReceivedRequest:
    """One request body received on a contract endpoint, as raw text."""

    path: str
    member_id: str | None
    body: str


@dataclass(frozen=True)
class FailureSpec:
    """A request to fail the next `count` contract calls with `status`.

    With `path` (a contract route template such as `/v1/outbox`), only calls to that
    route consume the failures; other calls are served normally (SPEC_DECISIONS 6).
    """

    count: int
    status: int
    when: FailureWhen
    path: str | None = None


def _route_filter(document: dict[str, object], contract_routes: frozenset[str], when: str) -> str | None:
    path = document.get("path")
    if path is None:
        return None
    if not isinstance(path, str) or path not in contract_routes:
        raise ValidationFailedError(details=(ErrorDetail("path", "UNKNOWN_ROUTE"),))
    if when == FailureWhen.AFTER_COMMIT and path != SUBMISSIONS_ROUTE:
        raise ValidationFailedError(details=(ErrorDetail("path", "AFTER_COMMIT_ONLY_ON_SUBMISSIONS"),))
    return path


def parse_failure_spec(
    document: object, contract_routes: frozenset[str], allow_after_commit: bool
) -> FailureSpec:
    """Validate a `/_test/fail-next` body against the double's contract route templates."""
    if (
        not isinstance(document, dict)
        or not set(document) >= _REQUIRED_FIELDS
        or not set(document) <= _REQUIRED_FIELDS | _OPTIONAL_FIELDS
    ):
        raise ValidationFailedError(details=(ErrorDetail("body", "MUST_HAVE_COUNT_STATUS_WHEN"),))
    count, status, when = document["count"], document["status"], document["when"]
    if not isinstance(count, int) or isinstance(count, bool) or not 1 <= count <= MAX_INJECTED_FAILURES:
        raise ValidationFailedError(details=(ErrorDetail("count", "OUT_OF_RANGE"),))
    if not isinstance(status, int) or status not in INJECTABLE_STATUSES:
        raise ValidationFailedError(details=(ErrorDetail("status", "NOT_AN_INJECTABLE_STATUS"),))
    if when not in {w.value for w in FailureWhen}:
        raise ValidationFailedError(details=(ErrorDetail("when", "INVALID_VALUE"),))
    if when == FailureWhen.AFTER_COMMIT and not allow_after_commit:
        raise ValidationFailedError(details=(ErrorDetail("when", "AFTER_COMMIT_NOT_SUPPORTED"),))
    path = _route_filter(document, contract_routes, when)
    return FailureSpec(count=count, status=status, when=FailureWhen(when), path=path)


class ControlState:
    """Received bodies and the pending injected failures of one double."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._received: list[ReceivedRequest] = []
        self._pending: FailureSpec | None = None
        self._remaining = 0

    def record(self, path: str, member_id: str | None, body: bytes) -> None:
        """Keep a received body as text, in arrival order."""
        text = body.decode("utf-8", errors="replace")
        with self._lock:
            self._received.append(ReceivedRequest(path=path, member_id=member_id, body=text))

    def received(self) -> tuple[ReceivedRequest, ...]:
        """Every body received so far, oldest first."""
        with self._lock:
            return tuple(self._received)

    def arm(self, spec: FailureSpec) -> None:
        """Replace any pending failure with a new one."""
        with self._lock:
            self._pending = spec
            self._remaining = spec.count

    def take(self, when: FailureWhen, route: str) -> int | None:
        """Consume one pending failure that applies to this call and return its status.

        `route` is the contract route template of the call. BEFORE_PROCESSING applies
        to every contract call, or only to `path` when the failure names one.
        AFTER_COMMIT applies only to registry submissions, which are processed and
        stored before the failure is returned.
        """
        with self._lock:
            spec = self._pending
            if spec is None or spec.when != when:
                return None
            if when == FailureWhen.AFTER_COMMIT and route != SUBMISSIONS_ROUTE:
                return None
            if spec.path is not None and spec.path != route:
                return None
            self._remaining -= 1
            if self._remaining <= 0:
                self._pending = None
            return spec.status
