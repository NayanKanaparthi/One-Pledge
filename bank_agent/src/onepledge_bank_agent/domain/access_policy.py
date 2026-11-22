"""`AccessPolicy`: the one place that decides who may do what (SPEC 16.3, 18.2).

Each row of the SPEC 18.2 matrix is one `Operation`, bound to its method and route
template. Where one route serves two rows (an upload by kind, a review by action),
the roles of the route are the union of its operations, which is what
`contracts/bank_agent.yaml` lists as `x-roles`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from onepledge_bank_agent.domain.enums import Role, TapeKind
from onepledge_bank_agent.domain.errors import ForbiddenError


class Operation(StrEnum):
    """One row of the permission matrix."""

    LOGIN = "LOGIN"
    LIVENESS = "LIVENESS"
    READINESS = "READINESS"
    GET_CURRENT_USER = "GET_CURRENT_USER"
    LIST_FACILITIES = "LIST_FACILITIES"
    UPLOAD_TAPE = "UPLOAD_TAPE"
    UPLOAD_RELEASE_TAPE = "UPLOAD_RELEASE_TAPE"
    LIST_TAPES = "LIST_TAPES"
    GET_TAPE = "GET_TAPE"
    LIST_TAPE_ITEMS = "LIST_TAPE_ITEMS"
    RETRY_TAPE = "RETRY_TAPE"
    CREATE_RELEASE = "CREATE_RELEASE"
    LIST_LOANS = "LIST_LOANS"
    SEARCH_LOANS = "SEARCH_LOANS"
    GET_LOAN = "GET_LOAN"
    GET_LOAN_LINEAGE = "GET_LOAN_LINEAGE"
    RESUBMIT_LOAN = "RESUBMIT_LOAN"
    LIST_ALERTS = "LIST_ALERTS"
    GET_ALERT = "GET_ALERT"
    START_ALERT_REVIEW = "START_ALERT_REVIEW"
    SUBMIT_ALERT_POSITION = "SUBMIT_ALERT_POSITION"
    LIST_CERTIFICATES = "LIST_CERTIFICATES"
    GET_CERTIFICATE = "GET_CERTIFICATE"
    GET_INTEGRITY = "GET_INTEGRITY"
    RUN_DEEP_VERIFICATION = "RUN_DEEP_VERIFICATION"
    SYNC_REGISTRY = "SYNC_REGISTRY"
    LIST_AUDIT_LOG = "LIST_AUDIT_LOG"
    LIST_USERS = "LIST_USERS"
    CREATE_USER = "CREATE_USER"
    UPDATE_USER = "UPDATE_USER"


@dataclass(frozen=True)
class Route:
    """An HTTP method, a route template and its `operationId` in `bank_agent.yaml`."""

    method: str
    template: str
    operation_id: str


_A, _C, _D = Role.ANALYST, Role.CREDIT_OFFICER, Role.ADMIN
_PUBLIC: frozenset[Role] = frozenset(Role)
_READERS = frozenset({_A, _C})
_OFFICER = frozenset({_C})
_EVERYONE = frozenset({_A, _C, _D})
_OVERSIGHT = frozenset({_C, _D})
_ADMIN = frozenset({_D})

_MATRIX: Mapping[Operation, tuple[Route, frozenset[Role]]] = MappingProxyType(
    {
        Operation.LOGIN: (Route("POST", "/v1/auth/login", "login"), _PUBLIC),
        Operation.LIVENESS: (Route("GET", "/healthz", "liveness"), _PUBLIC),
        Operation.READINESS: (Route("GET", "/readyz", "readiness"), _PUBLIC),
        Operation.GET_CURRENT_USER: (Route("GET", "/v1/auth/me", "getCurrentUser"), _EVERYONE),
        Operation.LIST_FACILITIES: (Route("GET", "/v1/facilities", "listFacilities"), _READERS),
        Operation.UPLOAD_TAPE: (Route("POST", "/v1/tapes", "uploadTape"), _READERS),
        Operation.UPLOAD_RELEASE_TAPE: (Route("POST", "/v1/tapes", "uploadTape"), _OFFICER),
        Operation.LIST_TAPES: (Route("GET", "/v1/tapes", "listTapes"), _READERS),
        Operation.GET_TAPE: (Route("GET", "/v1/tapes/{submission_id}", "getTape"), _READERS),
        Operation.LIST_TAPE_ITEMS: (
            Route("GET", "/v1/tapes/{submission_id}/items", "listTapeItems"),
            _READERS,
        ),
        Operation.RETRY_TAPE: (Route("POST", "/v1/tapes/{submission_id}/retry", "retryTape"), _OFFICER),
        Operation.CREATE_RELEASE: (Route("POST", "/v1/releases", "createRelease"), _OFFICER),
        Operation.LIST_LOANS: (Route("GET", "/v1/loans", "listLoans"), _READERS),
        Operation.SEARCH_LOANS: (Route("POST", "/v1/loans/search", "searchLoans"), _READERS),
        Operation.GET_LOAN: (Route("GET", "/v1/loans/{loan_id}", "getLoan"), _READERS),
        Operation.GET_LOAN_LINEAGE: (Route("GET", "/v1/loans/{loan_id}/lineage", "getLoanLineage"), _READERS),
        Operation.RESUBMIT_LOAN: (Route("POST", "/v1/loans/{loan_id}/resubmit", "resubmitLoan"), _OFFICER),
        Operation.LIST_ALERTS: (Route("GET", "/v1/alerts", "listAlerts"), _READERS),
        Operation.GET_ALERT: (Route("GET", "/v1/alerts/{alert_id}", "getAlert"), _READERS),
        Operation.START_ALERT_REVIEW: (
            Route("POST", "/v1/alerts/{alert_id}/review", "reviewAlert"),
            _READERS,
        ),
        Operation.SUBMIT_ALERT_POSITION: (
            Route("POST", "/v1/alerts/{alert_id}/review", "reviewAlert"),
            _OFFICER,
        ),
        Operation.LIST_CERTIFICATES: (Route("GET", "/v1/certificates", "listCertificates"), _READERS),
        Operation.GET_CERTIFICATE: (
            Route("GET", "/v1/certificates/{certificate_id}", "getCertificate"),
            _READERS,
        ),
        Operation.GET_INTEGRITY: (Route("GET", "/v1/integrity", "getIntegrity"), _EVERYONE),
        Operation.RUN_DEEP_VERIFICATION: (
            Route("POST", "/v1/integrity/verify", "runDeepVerification"),
            _OVERSIGHT,
        ),
        Operation.SYNC_REGISTRY: (Route("POST", "/v1/registry/sync", "syncRegistry"), _OVERSIGHT),
        Operation.LIST_AUDIT_LOG: (Route("GET", "/v1/audit-log", "listAuditLog"), _OVERSIGHT),
        Operation.LIST_USERS: (Route("GET", "/v1/users", "listUsers"), _ADMIN),
        Operation.CREATE_USER: (Route("POST", "/v1/users", "createUser"), _ADMIN),
        Operation.UPDATE_USER: (Route("PATCH", "/v1/users/{user_id}", "updateUser"), _ADMIN),
    }
)
_PUBLIC_OPERATIONS = frozenset({Operation.LOGIN, Operation.LIVENESS, Operation.READINESS})


class AccessPolicy:
    """The permission matrix of SPEC 18.2."""

    def is_public(self, operation: Operation) -> bool:
        """Login and health need no token."""
        return operation in _PUBLIC_OPERATIONS

    def allows(self, role: Role, operation: Operation) -> bool:
        """True when the role may perform the operation."""
        return role in _MATRIX[operation][1]

    def require(self, role: Role, operation: Operation) -> None:
        """Raise `ForbiddenError` unless the role may perform the operation."""
        if not self.allows(role, operation):
            raise ForbiddenError

    def route(self, operation: Operation) -> Route:
        """The method and route template an operation is served on."""
        return _MATRIX[operation][0]

    def roles(self, operation: Operation) -> frozenset[Role]:
        """The roles allowed to perform an operation (every role for a public one)."""
        return _MATRIX[operation][1]

    def route_roles(self) -> Mapping[Route, frozenset[Role]]:
        """Roles per route: the union over the operations served on it (the YAML `x-roles`)."""
        merged: dict[Route, frozenset[Role]] = {}
        for operation, (route, roles) in _MATRIX.items():
            if operation not in _PUBLIC_OPERATIONS:
                merged[route] = merged.get(route, frozenset()) | roles
        return MappingProxyType(merged)

    def upload_operation(self, kind: TapeKind) -> Operation:
        """A release tape needs a credit officer; back book and pledge tapes do not."""
        return Operation.UPLOAD_RELEASE_TAPE if kind is TapeKind.RELEASE else Operation.UPLOAD_TAPE
