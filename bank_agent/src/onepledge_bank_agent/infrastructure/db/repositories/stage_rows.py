"""The rows deep verification digests, replays and re-derives, read as stored (SPEC_DECISIONS 107).

Each read returns rows of canonical cells (`domain/stage_digests.row`) in a fixed order with
fixed columns, so the digest a stage stored and the digest recomputed later are made the same
way. Values are taken as the database holds them, never through a domain type, so an edited
value is digested as edited.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping, Sequence
from types import MappingProxyType

from sqlalchemy import select, tuple_
from sqlalchemy.orm import Session

from onepledge_bank_agent.domain.enums import RequestOrigin
from onepledge_bank_agent.domain.replay import State
from onepledge_bank_agent.domain.stage_digests import DigestRow, StagePart, body_sha256, row
from onepledge_bank_agent.infrastructure.db.models import (
    AlertRow,
    CleaningIssueRow,
    FacilityRow,
    LenderRow,
    LoanRow,
    LoanTokenRow,
    LocalCheckRow,
    RawRowRow,
    RegistryRequestItemRow,
    RegistryRequestRow,
    SubmissionItemRow,
    TapeSubmissionRow,
    UserRow,
)


def items(session: Session, submission_id: uuid.UUID) -> list[DigestRow]:
    """`submission_items`: row number, disposition, loan id."""
    statement = (
        select(SubmissionItemRow.row_number, SubmissionItemRow.disposition, SubmissionItemRow.loan_id)
        .where(SubmissionItemRow.submission_id == submission_id)
        .order_by(SubmissionItemRow.row_number)
    )
    return [row(*found) for found in session.execute(statement)]


def issues(session: Session, submission_id: uuid.UUID) -> list[DigestRow]:
    """`cleaning_issues`: row number, field, problem, action."""
    issue = CleaningIssueRow
    statement = (
        select(RawRowRow.row_number, issue.field, issue.problem, issue.action)
        .join(RawRowRow, RawRowRow.raw_id == issue.raw_id)
        .where(RawRowRow.submission_id == submission_id)
        .order_by(RawRowRow.row_number, issue.field, issue.problem, issue.action)
    )
    return [row(*found) for found in session.execute(statement)]


def checks(session: Session, submission_id: uuid.UUID) -> list[DigestRow]:
    """`local_checks`: loan id, check type, result, reason code, evidence."""
    check = LocalCheckRow
    statement = (
        select(check.loan_id, check.check_type, check.result, check.reason_code, check.evidence)
        .where(check.submission_id == submission_id)
        .order_by(check.loan_id, check.check_type, check.result, check.reason_code)
    )
    return [row(*found) for found in session.execute(statement)]


def _tape_requests(session: Session, submission_id: uuid.UUID) -> list[uuid.UUID]:
    statement = (
        select(RegistryRequestRow.request_id)
        .where(
            RegistryRequestRow.submission_id == submission_id,
            RegistryRequestRow.origin == RequestOrigin.TAPE.value,
        )
        .order_by(RegistryRequestRow.batch_index)
    )
    return list(session.execute(statement).scalars())


def tokens(session: Session, submission_id: uuid.UUID) -> list[DigestRow]:
    """`loan_tokens` of the loans of the submission's requests, of the key versions of those requests."""
    request, item, token = RegistryRequestRow, RegistryRequestItemRow, LoanTokenRow
    pairs = (
        select(item.loan_id, request.key_version)
        .join(request, request.request_id == item.request_id)
        .where(request.submission_id == submission_id, request.origin == RequestOrigin.TAPE.value)
    )
    statement = (
        select(token.loan_id, token.token_kind, token.key_version, token.token)
        .where(tuple_(token.loan_id, token.key_version).in_(pairs))
        .order_by(token.loan_id, token.token_kind, token.key_version)
    )
    return [row(*found) for found in session.execute(statement)]


def requests(session: Session, request_ids: Sequence[uuid.UUID]) -> list[DigestRow]:
    """`registry_requests` in the order given, each with every answer column, the SHA-256 of its stored
    response body and its items with every result column."""
    found = []
    for request_id in request_ids:
        request = session.get(RegistryRequestRow, request_id)
        if request is None:
            found.append(row(str(request_id), None))
            continue
        item = RegistryRequestItemRow
        statement = select(item).where(item.request_id == request_id).order_by(item.position)
        held = [
            row(
                i.item_ref, i.position, i.loan_id, i.result, i.already_held, i.match_kind, i.match_basis,
                i.kinds_matched, i.registry_case_id, i.lock_id, i.reason,
            )
            for i in session.execute(statement).scalars()
        ]  # fmt: skip
        found.append(
            row(
                request.idempotency_key, request.key_version, request.item_count, request.response_status,
                request.responded_at, request.registry_submission_id, request.as_of_seq,
                request.certificate_id, body_sha256(request.response_body), held,
            )
        )  # fmt: skip
    return found


def stage_rows(session: Session, submission_id: uuid.UUID, part: StagePart) -> list[DigestRow]:
    """The rows of one stage digest."""
    if part is StagePart.REQUESTS:
        return requests(session, _tape_requests(session, submission_id))
    return PARTS[part](session, submission_id)


PARTS: Mapping[StagePart, Callable[[Session, uuid.UUID], list[DigestRow]]] = MappingProxyType(
    {StagePart.ITEMS: items, StagePart.ISSUES: issues, StagePart.CHECKS: checks, StagePart.TOKENS: tokens}
)


def reference(session: Session) -> dict[str, list[DigestRow]]:
    """`lenders` and `facilities`, every column a seed writes."""
    lender, facility = LenderRow, FacilityRow
    lenders = select(lender.lender_id, lender.legal_name, lender.lei).order_by(lender.lender_id)
    facilities = select(
        facility.facility_id, facility.lender_id, facility.code, facility.name, facility.credit_limit_cents,
        facility.advance_rate_bps, facility.status, facility.opened_on,
    ).order_by(facility.facility_id)  # fmt: skip
    return {
        "lenders": [row(*found) for found in session.execute(lenders)],
        "facilities": [row(*found) for found in session.execute(facilities)],
    }


def states(session: Session, table: str) -> dict[uuid.UUID, State]:
    """The columns the audit replay follows, by primary key, for loans, tapes, alerts and users."""
    if table == "loans":
        return {key: (state,) for key, state in session.execute(select(LoanRow.loan_id, LoanRow.state))}
    if table == "tape_submissions":
        tape = TapeSubmissionRow
        return {key: (status,) for key, status in session.execute(select(tape.submission_id, tape.status))}
    if table == "alerts":
        query = select(AlertRow.alert_id, AlertRow.status, AlertRow.case_status)
        return {key: (status, case) for key, status, case in session.execute(query)}
    users = select(UserRow.user_id, UserRow.role, UserRow.active)
    return {key: (role, "true" if active else "false") for key, role, active in session.execute(users)}


def loan_values(session: Session) -> dict[uuid.UUID, DigestRow]:
    """Each loan's vin, borrower, origination date, amount, balance and lender loan number."""
    loan = LoanRow
    statement = select(
        loan.loan_id, loan.vin, loan.borrower_ref, loan.origination_date, loan.original_amount_cents,
        loan.current_balance_cents, loan.lender_loan_number,
    )  # fmt: skip
    return {found[0]: row(*found[1:]) for found in session.execute(statement)}
