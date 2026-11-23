"""Replaying the audit log against the current state of every row it describes (SPEC_DECISIONS 107 c).

For every loan, the `LOAN_STATE_CHANGED` entries in order must form a chain from nothing to its
current state through the transitions of SPEC 8.4. For every tape, `TAPE_UPLOADED`,
`TAPE_STAGE_ADVANCED`, `TAPE_FAILED` and `TAPE_RETRY_REQUESTED` must lead to its status. For every
alert, `ALERT_RECEIVED`, `ALERT_REVIEW_STARTED`, `ALERT_POSITION_SUBMITTED` and
`ALERT_CASE_UPDATED` must lead to its status and case status. For every user, `USER_CREATED` and
`USER_UPDATED` must lead to its role and whether it is active. A row with no entries, entries
for a row that does not exist, an illegal step or a final state that differs is
`STATE_MISMATCH` on the row's table.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from types import MappingProxyType

from onepledge_bank_agent.domain.audit import AuditEntry
from onepledge_bank_agent.domain.canonical_json import JsonValue
from onepledge_bank_agent.domain.enums import AuditAction, LoanState, TapeStatus
from onepledge_bank_agent.domain.integrity import EvidenceProblem, EvidenceProblemKind
from onepledge_bank_agent.domain.loan import LOAN_TRANSITIONS

type State = tuple[str | None, ...]
"""A row's replayed state: one value per column the replay follows."""
type Step = Callable[[State | None, AuditEntry], State]

NEXT_STAGE: Mapping[TapeStatus, TapeStatus] = MappingProxyType(
    {
        TapeStatus.RECEIVED: TapeStatus.CLEANED,
        TapeStatus.CLEANED: TapeStatus.CHECKED,
        TapeStatus.CHECKED: TapeStatus.TOKENIZED,
        TapeStatus.TOKENIZED: TapeStatus.SUBMITTED,
        TapeStatus.SUBMITTED: TapeStatus.COMPLETED,
    }
)
LOAN_ACTIONS = frozenset({AuditAction.LOAN_STATE_CHANGED.value})
TAPE_ACTIONS = frozenset(
    action.value
    for action in (
        AuditAction.TAPE_UPLOADED,
        AuditAction.RELEASE_REQUESTED,
        AuditAction.TAPE_STAGE_ADVANCED,
        AuditAction.TAPE_FAILED,
        AuditAction.TAPE_RETRY_REQUESTED,
    )
)
ALERT_ACTIONS = frozenset(
    action.value
    for action in (
        AuditAction.ALERT_RECEIVED,
        AuditAction.ALERT_REVIEW_STARTED,
        AuditAction.ALERT_POSITION_SUBMITTED,
        AuditAction.ALERT_CASE_UPDATED,
    )
)
USER_ACTIONS = frozenset({AuditAction.USER_CREATED.value, AuditAction.USER_UPDATED.value})


class IllegalStepError(Exception):
    """An entry that cannot follow the state replayed so far."""


def _text(detail: Mapping[str, JsonValue], key: str) -> str | None:
    value = detail.get(key)
    return str(value) if value is not None else None


def loan_step(state: State | None, entry: AuditEntry) -> State:
    """One LOAN_STATE_CHANGED entry: `from` must be the current state and the move legal."""
    before = _text(entry.detail, "from")
    after = _text(entry.detail, "to")
    current = state[0] if state is not None else None
    if before != current or after is None:
        raise IllegalStepError
    if (LoanState(before) if before is not None else None, LoanState(after)) not in LOAN_TRANSITIONS:
        raise IllegalStepError
    return (after,)


def tape_step(state: State | None, entry: AuditEntry) -> State:
    """One tape entry: created RECEIVED, one stage forward, into FAILED, back to the resume status."""
    current = TapeStatus(state[0]) if state is not None and state[0] is not None else None
    action = entry.action
    if action in (AuditAction.TAPE_UPLOADED.value, AuditAction.RELEASE_REQUESTED.value):
        if current is not None:
            raise IllegalStepError
        return (TapeStatus.RECEIVED.value,)
    if current is None:
        raise IllegalStepError
    if action == AuditAction.TAPE_STAGE_ADVANCED.value:
        if _text(entry.detail, "to") != NEXT_STAGE.get(current, current).value or current not in NEXT_STAGE:
            raise IllegalStepError
        return (NEXT_STAGE[current].value,)
    if action == AuditAction.TAPE_FAILED.value:
        if current in (TapeStatus.COMPLETED, TapeStatus.FAILED):
            raise IllegalStepError
        return (TapeStatus.FAILED.value,)
    resume = _text(entry.detail, "resume_status")
    if current is not TapeStatus.FAILED or resume is None:
        raise IllegalStepError
    return (resume,)


def alert_step(state: State | None, entry: AuditEntry) -> State:
    """One alert entry: each carries the status and case status it left the alert in."""
    received = entry.action == AuditAction.ALERT_RECEIVED.value
    if received is not (state is None):
        raise IllegalStepError
    status = _text(entry.detail, "to" if entry.action == AuditAction.ALERT_CASE_UPDATED.value else "status")
    case_status = _text(entry.detail, "case_status")
    if status is None or case_status is None:
        raise IllegalStepError
    return (status, case_status)


def user_step(state: State | None, entry: AuditEntry) -> State:
    """One user entry: each carries the role and whether the user is active."""
    if (entry.action == AuditAction.USER_CREATED.value) is not (state is None):
        raise IllegalStepError
    role, active = _text(entry.detail, "role"), entry.detail.get("active")
    if role is None or not isinstance(active, bool):
        raise IllegalStepError
    return (role, str(active).lower())


def replay(
    table: str,
    actions: frozenset[str],
    step: Step,
    entries: Iterable[AuditEntry],
    current: Mapping[uuid.UUID, State],
) -> list[EvidenceProblem]:
    """STATE_MISMATCH for every row of `current` whose entries do not lead to its state, and for every
    object the entries name that has no row."""
    by_object: dict[uuid.UUID, list[AuditEntry]] = defaultdict(list)
    for entry in entries:
        if entry.action in actions and entry.object_id is not None:
            by_object[entry.object_id].append(entry)
    problems = []
    for object_id in sorted(set(by_object) | set(current), key=str):
        ordered = sorted(by_object.get(object_id, []), key=lambda e: e.seq)
        if not _leads_to(step, ordered, current.get(object_id)):
            problems.append(EvidenceProblem(table, str(object_id), EvidenceProblemKind.STATE_MISMATCH))
    return problems


def _leads_to(step: Step, entries: Sequence[AuditEntry], expected: State | None) -> bool:
    if not entries or expected is None:
        return False
    state: State | None = None
    try:
        for entry in entries:
            state = step(state, entry)
    except (IllegalStepError, ValueError):
        return False
    return state == expected
