"""`AccessPolicy`: every cell of the SPEC 18.2 matrix, written out here row by row."""

from __future__ import annotations

import pytest

from onepledge_bank_agent.domain.access_policy import AccessPolicy, Operation, Route
from onepledge_bank_agent.domain.enums import Role, TapeKind
from onepledge_bank_agent.domain.errors import ForbiddenError

pytestmark = pytest.mark.unit

AN, CO, AD = Role.ANALYST, Role.CREDIT_OFFICER, Role.ADMIN
EVERY = {AN, CO, AD}
MATRIX: dict[Operation, set[Role]] = {
    Operation.LOGIN: EVERY, Operation.LIVENESS: EVERY, Operation.READINESS: EVERY,
    Operation.GET_CURRENT_USER: EVERY,
    Operation.LIST_FACILITIES: {AN, CO},
    Operation.UPLOAD_TAPE: {AN, CO},
    Operation.UPLOAD_RELEASE_TAPE: {CO},
    Operation.LIST_TAPES: {AN, CO}, Operation.GET_TAPE: {AN, CO}, Operation.LIST_TAPE_ITEMS: {AN, CO},
    Operation.RETRY_TAPE: {CO},
    Operation.CREATE_RELEASE: {CO},
    Operation.LIST_LOANS: {AN, CO}, Operation.SEARCH_LOANS: {AN, CO}, Operation.GET_LOAN: {AN, CO},
    Operation.GET_LOAN_LINEAGE: {AN, CO},
    Operation.RESUBMIT_LOAN: {CO},
    Operation.LIST_ALERTS: {AN, CO}, Operation.GET_ALERT: {AN, CO},
    Operation.START_ALERT_REVIEW: {AN, CO},
    Operation.SUBMIT_ALERT_POSITION: {CO},
    Operation.LIST_CERTIFICATES: {AN, CO}, Operation.GET_CERTIFICATE: {AN, CO},
    Operation.GET_INTEGRITY: EVERY,
    Operation.RUN_DEEP_VERIFICATION: {CO, AD}, Operation.SYNC_REGISTRY: {CO, AD},
    Operation.LIST_AUDIT_LOG: {CO, AD},
    Operation.LIST_USERS: {AD}, Operation.CREATE_USER: {AD}, Operation.UPDATE_USER: {AD},
}  # fmt: skip
POLICY = AccessPolicy()


def test_the_matrix_names_every_operation() -> None:
    assert set(MATRIX) == set(Operation)


@pytest.mark.parametrize("operation", list(Operation))
@pytest.mark.parametrize("role", list(Role))
def test_every_cell(operation: Operation, role: Role) -> None:
    allowed = role in MATRIX[operation]
    assert POLICY.allows(role, operation) is allowed
    if allowed:
        POLICY.require(role, operation)
    else:
        with pytest.raises(ForbiddenError) as raised:
            POLICY.require(role, operation)
        assert raised.value.code == "FORBIDDEN"


def test_admin_reads_no_loan_tape_alert_or_certificate_data() -> None:
    data = [
        op
        for op in Operation
        if op.value.startswith(
            (
                "LIST_TAPE",
                "GET_TAPE",
                "LIST_LOANS",
                "GET_LOAN",
                "SEARCH",
                "LIST_ALERTS",
                "GET_ALERT",
                "LIST_CERT",
                "GET_CERT",
            )
        )
    ]
    assert data and not any(POLICY.allows(AD, op) for op in data)


def test_public_operations_and_upload_by_kind() -> None:
    assert {op for op in Operation if POLICY.is_public(op)} == {
        Operation.LOGIN,
        Operation.LIVENESS,
        Operation.READINESS,
    }
    assert POLICY.upload_operation(TapeKind.RELEASE) is Operation.UPLOAD_RELEASE_TAPE
    assert POLICY.upload_operation(TapeKind.PLEDGE) is Operation.UPLOAD_TAPE
    assert POLICY.upload_operation(TapeKind.BACK_BOOK) is Operation.UPLOAD_TAPE
    assert POLICY.route(Operation.UPLOAD_RELEASE_TAPE) == Route("POST", "/v1/tapes")
    assert POLICY.roles(Operation.SUBMIT_ALERT_POSITION) == frozenset({CO})


def test_a_route_serving_two_rows_allows_the_union_of_their_roles() -> None:
    roles = POLICY.route_roles()
    assert roles[Route("POST", "/v1/tapes")] == {AN, CO}
    assert roles[Route("POST", "/v1/alerts/{alert_id}/review")] == {AN, CO}
    assert Route("POST", "/v1/auth/login") not in roles
