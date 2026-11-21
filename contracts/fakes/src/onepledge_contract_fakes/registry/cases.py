"""Conflict cases of the registry double (TEST DOUBLE).

A case links a claimant's asset code to the lock it conflicted with. Each party
records one position; equal positions resolve the case. A holder's release
resolves every open case on that lock as CLEARED_LATE_RELEASE. A (claimant asset
code, lock) pair cleared as a data error is exempt from Rule C afterwards.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from onepledge_contract_fakes.canonical_json import JsonValue
from onepledge_contract_fakes.errors import (
    CaseAlreadyResolvedError,
    CaseNotFoundError,
    PositionAlreadyRecordedError,
)
from onepledge_contract_fakes.registry.locks import Lock
from onepledge_contract_fakes.registry.matching import Match
from onepledge_contract_fakes.registry.model import CaseStatus, MatchBasis, MatchKind, Position, Role

RESOLUTION_BY_POSITION: Mapping[Position, CaseStatus] = MappingProxyType(
    {
        Position.DATA_ERROR: CaseStatus.CLEARED_DATA_ERROR,
        Position.CONFIRMED_DOUBLE_PLEDGE: CaseStatus.CONFIRMED,
    }
)


@dataclass
class Case:
    """One conflict case and both parties' positions."""

    case_id: str
    lock_id: str
    holder_member_id: str
    holder_asset_token: str
    claimant_member_id: str
    claimant_asset_token: str
    match_kind: MatchKind
    match_basis: MatchBasis
    kinds_matched: tuple[str, ...]
    opened_seq: int
    status: CaseStatus = CaseStatus.OPEN
    resolved_seq: int | None = None
    positions: dict[str, Position] = field(default_factory=dict)

    def role_of(self, member_id: str) -> Role:
        """The member's side of the case."""
        return Role.HOLDER if member_id == self.holder_member_id else Role.CLAIMANT

    def asset_token_of(self, member_id: str) -> str:
        """The member's own asset code for its own loan."""
        return self.holder_asset_token if member_id == self.holder_member_id else self.claimant_asset_token

    def parties(self) -> tuple[str, str]:
        """Holder first, then claimant."""
        return (self.holder_member_id, self.claimant_member_id)

    def view_for(self, member_id: str) -> dict[str, JsonValue]:
        """The `CaseView` wire shape for one party. It never names the other party."""
        other = self.claimant_member_id if member_id == self.holder_member_id else self.holder_member_id
        own = self.positions.get(member_id)
        return {
            "case_id": self.case_id,
            "role": self.role_of(member_id).value,
            "asset_token": self.asset_token_of(member_id),
            "match_kind": self.match_kind.value,
            "match_basis": self.match_basis.value,
            "kinds_matched": list(self.kinds_matched),
            "status": self.status.value,
            "your_position": own.value if own else None,
            "awaiting_counterparty": self.status == CaseStatus.OPEN and other not in self.positions,
            "opened_seq": self.opened_seq,
            "resolved_seq": self.resolved_seq,
        }


class CaseBook:
    """Every case, with lookups by party, by lock and by claimant pair."""

    def __init__(self) -> None:
        self._cases: dict[str, Case] = {}
        self._exempt: set[tuple[str, str]] = set()

    def open_case_for(self, claimant_asset_token: str, lock_id: str) -> Case | None:
        """The OPEN case of this claimant asset code against this lock, if any."""
        for case in self._cases.values():
            if (
                case.status == CaseStatus.OPEN
                and case.lock_id == lock_id
                and case.claimant_asset_token == claimant_asset_token
            ):
                return case
        return None

    def open(self, case_id: str, claimant: str, claimant_asset: str, match: Match, opened_seq: int) -> Case:
        """Open a new case for a conflict."""
        lock: Lock = match.lock
        case = Case(
            case_id=case_id,
            lock_id=lock.lock_id,
            holder_member_id=lock.member_id,
            holder_asset_token=lock.asset_token,
            claimant_member_id=claimant,
            claimant_asset_token=claimant_asset,
            match_kind=match.kind,
            match_basis=match.basis,
            kinds_matched=match.kinds_matched,
            opened_seq=opened_seq,
        )
        self._cases[case_id] = case
        return case

    def for_member(self, case_id: str, member_id: str) -> Case:
        """A case the member is a party to, or CaseNotFoundError (never revealing existence)."""
        case = self._cases.get(case_id)
        if case is None or member_id not in case.parties():
            raise CaseNotFoundError
        return case

    def of_member(self, member_id: str) -> tuple[Case, ...]:
        """The member's cases ordered by opened_seq, then case id."""
        own = [c for c in self._cases.values() if member_id in c.parties()]
        return tuple(sorted(own, key=lambda c: (c.opened_seq, c.case_id)))

    def open_on_lock(self, lock_id: str) -> tuple[Case, ...]:
        """OPEN cases against one lock, oldest first."""
        cases = [c for c in self._cases.values() if c.lock_id == lock_id and c.status == CaseStatus.OPEN]
        return tuple(sorted(cases, key=lambda c: c.opened_seq))

    def record_position(self, case: Case, member_id: str, position: Position) -> CaseStatus | None:
        """Record a party's position. Returns the new status when this resolves the case."""
        current = case.positions.get(member_id)
        if case.status != CaseStatus.OPEN:
            if current == position:
                return None
            raise CaseAlreadyResolvedError
        if current is not None and current != position:
            raise PositionAlreadyRecordedError
        case.positions[member_id] = position
        holder_position = case.positions.get(case.holder_member_id)
        claimant_position = case.positions.get(case.claimant_member_id)
        if holder_position is not None and holder_position == claimant_position:
            return RESOLUTION_BY_POSITION[position]
        return None

    def resolve(self, case: Case, status: CaseStatus, resolved_seq: int) -> None:
        """Close a case; a data error clearance exempts the pair from Rule C."""
        case.status = status
        case.resolved_seq = resolved_seq
        if status == CaseStatus.CLEARED_DATA_ERROR:
            self._exempt.add((case.claimant_asset_token, case.lock_id))

    def is_exempt(self, claimant_asset_token: str, lock_id: str) -> bool:
        """Whether a pair was cleared as a data error."""
        return (claimant_asset_token, lock_id) in self._exempt

    def all(self) -> tuple[Case, ...]:
        """Every case ordered by opened_seq."""
        return tuple(sorted(self._cases.values(), key=lambda c: (c.opened_seq, c.case_id)))
