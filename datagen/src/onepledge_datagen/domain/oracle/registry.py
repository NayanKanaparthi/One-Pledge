"""Token inputs of SPEC 9.2 and the registry decision rules of `contracts/registry.yaml` (oracle).

The oracle compares canonical value strings instead of OPRF codes: two codes are
equal exactly when their inputs are equal, so the decisions are the same.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from onepledge_datagen.domain.oracle.model import Terms

KINDS: tuple[str, ...] = (
    "ASSET",
    "VIN",
    "BORROWER",
    "AMOUNT_EXACT",
    "AMOUNT_100",
    "AMOUNT_1000",
    "DATE_DAY",
    "DATE_MONTH",
)
EXACT_KINDS: tuple[str, ...] = ("BORROWER", "AMOUNT_EXACT", "DATE_DAY")
RULE_C_PAIRS: tuple[tuple[str, str], ...] = (
    ("AMOUNT_EXACT", "DATE_DAY"),
    ("AMOUNT_EXACT", "DATE_MONTH"),
    ("AMOUNT_100", "DATE_DAY"),
)


def token_inputs(lei: str, terms: Terms) -> dict[str, str]:
    """Canonical value string of every token kind (SPEC 9.2)."""
    borrower = f"{lei}|{terms.borrower_ref}"
    cents = terms.original_amount_cents
    day = terms.origination_date
    return {
        "ASSET": "AUTO|" + terms.vin,
        "VIN": terms.vin,
        "BORROWER": borrower,
        "AMOUNT_EXACT": f"{borrower}|{cents}",
        "AMOUNT_100": f"{borrower}|{cents // 10000}",
        "AMOUNT_1000": f"{borrower}|{cents // 100000}",
        "DATE_DAY": f"{borrower}|{day}",
        "DATE_MONTH": f"{borrower}|{day[:7]}",
    }


@dataclass(frozen=True)
class OracleLock:
    """An active lock: member, inputs, and its age order."""

    lock_id: int
    member: str
    inputs: dict[str, str]


@dataclass
class OracleCase:
    """A conflict case between a claimant's asset input and a lock."""

    case_id: int
    lock: OracleLock
    claimant: str
    claimant_asset: str
    match_kind: str
    match_basis: str
    kinds_matched: tuple[str, ...]
    open: bool = True


@dataclass(frozen=True)
class Decision:
    """The result of one lock request."""

    result: str
    match_kind: str | None = None
    match_basis: str | None = None
    kinds_matched: tuple[str, ...] = ()
    new_case: OracleCase | None = None


@dataclass(frozen=True)
class _Match:
    """A conflicting lock with the kind, basis and token kinds of the match."""

    lock: OracleLock
    kind: str
    basis: str
    kinds_matched: tuple[str, ...]


def _matched(inputs: dict[str, str], lock: OracleLock) -> tuple[str, ...]:
    """Token kinds whose inputs equal the lock's, in contract order."""
    return tuple(kind for kind in KINDS if inputs[kind] == lock.inputs[kind])


@dataclass
class RegistryModel:
    """Locks and cases, decided item by item in request order."""

    locks: dict[str, OracleLock] = field(default_factory=dict)
    cases: list[OracleCase] = field(default_factory=list)
    _next_id: int = 1

    def _new_id(self) -> int:
        """The next case number."""
        value = self._next_id
        self._next_id += 1
        return value

    def lock(self, member: str, inputs: dict[str, str]) -> Decision:
        """Rules A, B and C for one BACK_BOOK or PLEDGE item."""
        holder = self.locks.get(inputs["ASSET"])
        if holder is not None:
            if holder.member == member:
                return Decision("LOCKED")
            matched = _matched(inputs, holder)
            kind = "EXACT" if all(k in matched for k in EXACT_KINDS) else "LIKELY"
            return self._conflict(member, inputs, _Match(holder, kind, "ASSET", matched))
        best: tuple[OracleLock, tuple[str, ...]] | None = None
        for lock in sorted(self.locks.values(), key=lambda item: item.lock_id):
            if lock.member == member or lock.inputs["BORROWER"] != inputs["BORROWER"]:
                continue
            matched = _matched(inputs, lock)
            if any(a in matched and b in matched for a, b in RULE_C_PAIRS) and (
                best is None or len(matched) > len(best[1])
            ):
                best = (lock, matched)
        if best is not None:
            return self._conflict(member, inputs, _Match(best[0], "LIKELY", "FIELDS", best[1]))
        self.locks[inputs["ASSET"]] = OracleLock(self._new_id(), member, inputs)
        return Decision("LOCKED")

    def _conflict(self, member: str, inputs: dict[str, str], match: _Match) -> Decision:
        """CONFLICT with this item's own match; a new case unless the claimant already has an OPEN one."""
        for case in self.cases:
            if (
                case.open
                and case.lock.lock_id == match.lock.lock_id
                and case.claimant == member
                and case.claimant_asset == inputs["ASSET"]
            ):
                # The OPEN case is reused (no new case, no alert); the item reports its own match.
                return Decision("CONFLICT", match.kind, match.basis, match.kinds_matched)
        case = OracleCase(
            self._new_id(), match.lock, member, inputs["ASSET"], match.kind, match.basis, match.kinds_matched
        )
        self.cases.append(case)
        return Decision("CONFLICT", match.kind, match.basis, match.kinds_matched, new_case=case)

    def release(self, member: str, asset_input: str) -> str:
        """RELEASED when the member holds the lock (open cases close as late release), else REJECTED."""
        lock = self.locks.get(asset_input)
        if lock is None or lock.member != member:
            return "REJECTED"
        del self.locks[asset_input]
        for case in self.cases:
            if case.open and case.lock.lock_id == lock.lock_id:
                case.open = False
        return "RELEASED"
