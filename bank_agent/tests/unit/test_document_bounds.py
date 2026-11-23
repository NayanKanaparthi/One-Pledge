"""Size bounds for stored JSON, by class (SPEC_DECISIONS 125, amending the `jsonb` row of 100).

Free-form documents, which no contract schema bounds, are at most 64 KiB in canonical form: an
audit entry's detail and a local check's evidence. An integrity run's report is at most 1 MiB
(its lists are capped at 200 by decision 108). A value one byte over is refused where it is made,
before any store sees it; one exactly at the bound is accepted. The three registry messages, whose
payload is quarantined above 64 KiB before its schema is checked, cannot reach that size in any
contract-valid form, so the 64 KiB check never refuses a valid message.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from bank_testkit.longest import Longest
from onepledge_bank_agent.application.evidence_ports import IntegrityRun
from onepledge_bank_agent.domain.audit import Actor, AuditDraft
from onepledge_bank_agent.domain.canonical_json import canonical_json
from onepledge_bank_agent.domain.enums import (
    AuditAction,
    AuditObjectType,
    CheckOutcome,
    CheckReason,
    CheckType,
    IntegrityRunStatus,
)
from onepledge_bank_agent.domain.errors import InvalidValueError
from onepledge_bank_agent.domain.storable import FREE_FORM_BYTES, REPORT_BYTES
from onepledge_bank_agent.domain.values import CheckResult

pytestmark = pytest.mark.unit


def of_size(size: int) -> dict[str, str]:
    """A JSON object whose canonical form is exactly `size` bytes."""
    padding = size - len(canonical_json({"x": ""}))
    document = {"x": "a" * padding}
    assert len(canonical_json(document)) == size
    return document


def draft(detail: dict[str, str]) -> AuditDraft:
    return AuditDraft(
        AuditAction.TAPE_UPLOADED,
        Actor.system(),
        AuditObjectType.TAPE_SUBMISSION,
        uuid.uuid4(),
        detail=detail,
    )


def check(evidence: dict[str, str]) -> CheckResult:
    return CheckResult(
        CheckType.LIENHOLDER_MATCH, CheckOutcome.PASS, CheckReason.LIENHOLDER_MATCHES, evidence
    )


def run(report: dict[str, str]) -> IntegrityRun:
    return IntegrityRun(uuid.uuid4(), datetime(2026, 11, 23, tzinfo=UTC), None, IntegrityRunStatus.OK, report)


@pytest.mark.parametrize(
    ("make", "field", "bound"),
    [(draft, "detail", FREE_FORM_BYTES), (check, "evidence", FREE_FORM_BYTES), (run, "report", REPORT_BYTES)],
    ids=["audit detail", "check evidence", "integrity report"],
)
def test_a_free_form_document_is_accepted_at_its_bound_and_refused_one_byte_over(
    make: object, field: str, bound: int
) -> None:
    assert callable(make)
    make(of_size(bound))
    with pytest.raises(InvalidValueError) as refused:
        make(of_size(bound + 1))
    assert (refused.value.field, refused.value.details[0].issue) == (field, "OUT_OF_RANGE")


def test_the_bounds_are_the_decisions() -> None:
    assert (FREE_FORM_BYTES, REPORT_BYTES) == (64 * 1024, 1024 * 1024)


@pytest.mark.parametrize("name", ["AlertMessage", "CaseUpdateMessage", "CheckpointMessage"])
def test_no_contract_valid_message_reaches_the_quarantine_bound(name: str) -> None:
    longest = Longest("registry.yaml")
    message = longest.of(name)
    assert list(longest.validator(name).iter_errors(message)) == []
    assert len(canonical_json(message)) < FREE_FORM_BYTES
