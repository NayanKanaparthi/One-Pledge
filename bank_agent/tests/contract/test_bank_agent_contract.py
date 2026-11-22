"""The domain against `contracts/bank_agent.yaml`: roles per route, error codes and every enumeration.

SPEC 16.3 requires the `x-roles` of the YAML and `AccessPolicy` to be identical; SPEC 4
fixes one message per error code; SPEC 8 and the YAML must list the same values.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Any

import pytest
import yaml

from onepledge_bank_agent.domain import enums
from onepledge_bank_agent.domain.access_policy import AccessPolicy, Route
from onepledge_bank_agent.domain.enums import Role
from onepledge_bank_agent.domain.errors import MESSAGES

pytestmark = pytest.mark.contract

PUBLIC = "PUBLIC"
NAMED_SCHEMAS: dict[str, type[StrEnum]] = {
    "Role": enums.Role,
    "TapeKind": enums.TapeKind,
    "TapeStatus": enums.TapeStatus,
    "TapeSource": enums.TapeSource,
    "FailureCode": enums.FailureCode,
    "LoanState": enums.LoanState,
    "RowDisposition": enums.RowDisposition,
    "CheckType": enums.CheckType,
    "CheckOutcome": enums.CheckOutcome,
    "TokenKind": enums.TokenKind,
    "MatchKind": enums.MatchKind,
    "MatchBasis": enums.MatchBasis,
    "AlertStatus": enums.AlertStatus,
    "CaseStatus": enums.CaseStatus,
    "RegistryItemResult": enums.RegistryItemResult,
}
PROPERTIES: dict[tuple[str, ...], type[StrEnum]] = {
    ("Facility", "status"): enums.FacilityStatus,
    ("CleaningIssue", "action"): enums.CleaningAction,
    ("Alert", "role"): enums.AlertRole,
    ("Alert", "explanation_source"): enums.ExplanationSource,
    ("Alert", "bank_position"): enums.BankPosition,
    ("AlertReviewRequest", "position"): enums.BankPosition,
    ("AuditEntry", "actor_type"): enums.ActorType,
    ("AuditEntry", "outcome"): enums.AuditOutcome,
    ("LineageRegistryRequest", "origin"): enums.RequestOrigin,
    ("CheckpointFailure", "failure_reason"): enums.CheckpointFailure,
    ("CertificateSection", "failures", "items", "failure_reason"): enums.CertificateFailure,
    ("IntegrityReport", "status"): enums.IntegrityRunStatus,
}


@pytest.fixture(scope="module")
def document(contracts_dir: Path) -> dict[str, Any]:
    loaded: dict[str, Any] = yaml.safe_load((contracts_dir / "bank_agent.yaml").read_text(encoding="utf-8"))
    return loaded


def yaml_roles(document: dict[str, Any]) -> dict[Route, frozenset[Role]]:
    """`x-roles` of every operation that is not public."""
    found: dict[Route, frozenset[Role]] = {}
    for template, item in document["paths"].items():
        for method, operation in item.items():
            roles = operation["x-roles"]
            if roles != [PUBLIC]:
                route = Route(method.upper(), template, operation["operationId"])
                found[route] = frozenset(Role(role) for role in roles)
    return found


def public_routes(document: dict[str, Any]) -> set[Route]:
    return {
        Route(method.upper(), template, operation["operationId"])
        for template, item in document["paths"].items()
        for method, operation in item.items()
        if operation["x-roles"] == [PUBLIC]
    }


def test_access_policy_equals_the_x_roles_of_every_operation(document: dict[str, Any]) -> None:
    assert dict(AccessPolicy().route_roles()) == yaml_roles(document)


def test_every_operation_has_x_roles_and_public_ones_are_the_three_of_the_spec(
    document: dict[str, Any],
) -> None:
    for item in document["paths"].values():
        for operation in item.values():
            roles = operation["x-roles"]
            assert roles and (roles == [PUBLIC] or PUBLIC not in roles)
    assert public_routes(document) == {
        Route("POST", "/v1/auth/login", "login"),
        Route("GET", "/healthz", "liveness"),
        Route("GET", "/readyz", "readiness"),
    }


def test_the_message_table_has_exactly_the_error_codes_of_the_contract(document: dict[str, Any]) -> None:
    error = document["components"]["schemas"]["ErrorResponse"]["properties"]["error"]
    assert set(MESSAGES) == set(error["properties"]["code"]["enum"])


@pytest.mark.parametrize("name", sorted(NAMED_SCHEMAS))
def test_each_named_enum_schema_equals_the_domain_enum(document: dict[str, Any], name: str) -> None:
    schema = document["components"]["schemas"][name]
    assert schema["enum"] == [member.value for member in NAMED_SCHEMAS[name]]


@pytest.mark.parametrize("path", sorted(PROPERTIES), ids=".".join)
def test_each_inline_enum_equals_the_domain_enum(document: dict[str, Any], path: tuple[str, ...]) -> None:
    node: Any = document["components"]["schemas"][path[0]]
    for part in path[1:]:
        node = node["items"] if part == "items" else node["properties"][part]
    values = [value for value in node["enum"] if value is not None]
    assert sorted(values) == sorted(member.value for member in PROPERTIES[path])


def test_every_enum_of_the_contract_is_compared(document: dict[str, Any]) -> None:
    """A new enum in the YAML must be added to one of the tables above or be listed here with a reason."""
    literals = {
        ("LoginResponse", "token_type"), ("Health", "status"), ("Health", "service"),
        ("AlertReviewRequest", "action"),  # request actions of the review endpoint, not stored
        ("ErrorResponse", "error", "code"),  # compared with MESSAGES above
        ("IntegrityStatus",),  # the API view adds NOT_VERIFIED to the stored OK and BROKEN
    }  # fmt: skip
    covered = {(name,) for name in NAMED_SCHEMAS} | set(PROPERTIES) | literals
    found: set[tuple[str, ...]] = set()

    def walk(node: object, path: tuple[str, ...]) -> None:
        if isinstance(node, dict):
            if "enum" in node:
                found.add(path)
            for key, child in node.items():
                walk(child, path if key == "properties" else (*path, key))
        elif isinstance(node, list):
            for child in node:
                walk(child, path)

    for name, schema in document["components"]["schemas"].items():
        walk(schema, (name,))
    unknown = {path for path in found if path not in covered}
    assert unknown == set()
