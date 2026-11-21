"""State behind the `/_test/` endpoints of both doubles (TEST DOUBLES, SPEC 7.3).

These controls exist only when `FAKE_TEST_CONTROLS=true`. They are not part of
any contract: they let tests and the layer 1 scorecard see what a double
received and make it fail on purpose.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from enum import StrEnum

from onepledge_contract_fakes.errors import InvariantViolationError
from onepledge_contract_fakes.validation import Issue, JsonFields

INJECTABLE_STATUSES = frozenset({429, 500, 502, 503, 504})
MAX_INJECTED_FAILURES = 1000
SUBMISSIONS_ROUTE = "/v1/submissions"


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


def parse_failure_spec(
    document: object, contract_routes: frozenset[str], allow_after_commit: bool
) -> FailureSpec:
    """Validate a `/_test/fail-next` body against the double's contract route templates.

    `path`, when given, must be one of the double's route templates; with AFTER_COMMIT it
    may only be `/v1/submissions`. Every problem is INVALID_VALUE, INVALID_TYPE,
    OUT_OF_RANGE, REQUIRED or UNKNOWN_FIELD, like any other request of a layer 1 service.
    """
    fields = JsonFields(document, required=("count", "status", "when"), optional=("path",))
    count = fields.integer("count", 1, MAX_INJECTED_FAILURES)
    status = fields.integer("status", 100, 599)
    if status is not None and status not in INJECTABLE_STATUSES:
        fields.reject("status", Issue.INVALID_VALUE)
    when = fields.choice("when", [w.value for w in FailureWhen])
    if when == FailureWhen.AFTER_COMMIT and not allow_after_commit:
        fields.reject("when", Issue.INVALID_VALUE)
    path = fields.choice("path", contract_routes)
    if path is not None and when == FailureWhen.AFTER_COMMIT and path != SUBMISSIONS_ROUTE:
        fields.reject("path", Issue.INVALID_VALUE)
    fields.finish()
    if count is None or status is None or when is None:
        raise InvariantViolationError
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
