"""Decision rules A, B and C, cases, release and outbox fan-out of the registry double."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any

import pytest

from onepledge_contract_fakes.canonical_json import JsonValue, canonical_sha256
from onepledge_contract_fakes.errors import (
    CaseAlreadyResolvedError,
    CaseNotFoundError,
    CertificateNotFoundError,
    OutboxMessageNotFoundError,
    PositionAlreadyRecordedError,
    SubmissionNotFoundError,
)
from onepledge_contract_fakes.registry.double import RegistryDouble
from onepledge_contract_fakes.registry.model import (
    FIELD_KINDS,
    TOKEN_KINDS,
    CaseStatus,
    Position,
    Submission,
    SubmissionItem,
    SubmissionKind,
)
from onepledge_contract_fakes.testing import (
    CountingIds,
    FixedClock,
    new_api_key,
    new_members,
    registry_config,
)

pytestmark = pytest.mark.unit

ALL_KINDS = list(TOKEN_KINDS)


@dataclass(frozen=True)
class Loan:
    """Loan terms from which deterministic stand-in codes are derived (equal inputs, equal codes)."""

    vin: str
    borrower: str = "C1000001"
    cents: int = 2_345_017
    day: str = "2026-08-14"

    def values(self) -> dict[str, str]:
        b = "LEI|" + self.borrower
        return {
            "ASSET": "AUTO|" + self.vin,
            "VIN": self.vin,
            "BORROWER": b,
            "AMOUNT_EXACT": f"{b}|{self.cents}",
            "AMOUNT_100": f"{b}|{self.cents // 10000}",
            "AMOUNT_1000": f"{b}|{self.cents // 100000}",
            "DATE_DAY": f"{b}|{self.day}",
            "DATE_MONTH": f"{b}|{self.day[:7]}",
        }

    def code(self, kind: str) -> str:
        return hashlib.sha256(f"{kind}\x1f{self.values()[kind]}".encode()).hexdigest()

    def item(self) -> SubmissionItem:
        return SubmissionItem(str(uuid.uuid4()), self.code("ASSET"), {k: self.code(k) for k in FIELD_KINDS})

    def release_item(self) -> SubmissionItem:
        return SubmissionItem(str(uuid.uuid4()), self.code("ASSET"), None)


class World:
    """A registry double with three members."""

    def __init__(self) -> None:
        self.members, self.keys = new_members(3)
        self.ids = [m.member_id for m in self.members]
        self.registry = RegistryDouble(
            registry_config(self.members, new_api_key()), FixedClock(), CountingIds()
        )

    def submit(self, member: int, kind: SubmissionKind, *items: SubmissionItem) -> dict[str, Any]:
        submission = Submission(kind, "1.1", tuple(items))
        body: JsonValue = {"kind": kind.value, "refs": [i.item_ref for i in items]}
        outcome = self.registry.submit(
            self.ids[member], str(uuid.uuid4()), submission, canonical_sha256(body)
        )
        return plain(json.loads(outcome.body))

    def pledge(self, member: int, *loans: Loan) -> list[dict[str, Any]]:
        return list(self.submit(member, SubmissionKind.PLEDGE, *(loan.item() for loan in loans))["results"])

    def release(self, member: int, *loans: Loan) -> list[dict[str, Any]]:
        items = (loan.release_item() for loan in loans)
        return list(self.submit(member, SubmissionKind.RELEASE, *items)["results"])

    def messages(self, member: int, kind: str) -> list[dict[str, Any]]:
        page = plain(self.registry.outbox(self.ids[member], 200))
        return [m for m in page["messages"] if m["type"] == kind]

    def events(self) -> list[str]:
        page = plain(self.registry.ledger_events(0, 1000, True, "auditor"))
        return [e["event_type"] for e in page["events"]]


def plain(value: object) -> dict[str, Any]:
    """A JSON round trip, so tests can index nested results freely."""
    result: dict[str, Any] = json.loads(json.dumps(value))
    return result


@pytest.fixture
def world() -> World:
    return World()


def test_a_free_asset_is_locked_and_rule_a_reports_already_held(world: World) -> None:
    loan = Loan("VIN-A")
    first = world.pledge(0, loan)[0]
    assert first["result"] == "LOCKED" and first["already_held"] is False and first["lock_id"]
    again = world.pledge(0, loan)[0]
    assert (
        again["result"] == "LOCKED" and again["already_held"] is True and again["lock_id"] == first["lock_id"]
    )
    assert plain(world.registry.stats())["total"]["active_locks"] == 1


def test_rule_b_exact_copy(world: World) -> None:
    loan = Loan("VIN-B")
    world.pledge(0, loan)
    result = world.pledge(1, loan)[0]
    assert result["result"] == "CONFLICT" and result["lock_id"] is None
    assert (result["match_kind"], result["match_basis"]) == ("EXACT", "ASSET")
    assert result["kinds_matched"] == ALL_KINDS


def test_rule_b_amount_changed_is_likely_never_exact(world: World) -> None:
    original = Loan("VIN-C", cents=2_345_017)
    world.pledge(0, original)
    copy = Loan("VIN-C", cents=2_345_017 + 150_000)
    result = world.pledge(1, copy)[0]
    assert (result["match_kind"], result["match_basis"]) == ("LIKELY", "ASSET")
    matched = result["kinds_matched"]
    assert isinstance(matched, list)
    assert {"ASSET", "VIN", "BORROWER", "DATE_DAY", "DATE_MONTH"} <= set(matched)
    assert "AMOUNT_EXACT" not in matched and "AMOUNT_100" not in matched


def test_rule_b_date_changed_is_likely(world: World) -> None:
    world.pledge(0, Loan("VIN-D", day="2026-08-14"))
    result = world.pledge(1, Loan("VIN-D", day="2026-08-30"))[0]
    assert result["match_kind"] == "LIKELY"
    assert result["kinds_matched"] == [
        "ASSET",
        "VIN",
        "BORROWER",
        "AMOUNT_EXACT",
        "AMOUNT_100",
        "AMOUNT_1000",
        "DATE_MONTH",
    ]


@pytest.mark.parametrize(
    ("copy", "expected_kinds"),
    [
        (Loan("VIN-X2"), ["BORROWER", "AMOUNT_EXACT", "AMOUNT_100", "AMOUNT_1000", "DATE_DAY", "DATE_MONTH"]),
        (
            Loan("VIN-X2", day="2026-08-20"),
            ["BORROWER", "AMOUNT_EXACT", "AMOUNT_100", "AMOUNT_1000", "DATE_MONTH"],
        ),
        (
            Loan("VIN-X2", cents=2_345_090),
            ["BORROWER", "AMOUNT_100", "AMOUNT_1000", "DATE_DAY", "DATE_MONTH"],
        ),
    ],
)
def test_rule_c_each_field_pair_triggers(world: World, copy: Loan, expected_kinds: list[str]) -> None:
    world.pledge(0, Loan("VIN-X1"))
    result = world.pledge(1, copy)[0]
    assert (result["result"], result["match_kind"], result["match_basis"]) == ("CONFLICT", "LIKELY", "FIELDS")
    assert result["kinds_matched"] == expected_kinds


@pytest.mark.parametrize(
    "other",
    [
        Loan("VIN-Y2", cents=2_345_017 + 70_000, day="2026-08-20"),
        Loan("VIN-Y2", cents=2_345_017 + 70_000),
        Loan("VIN-Y2", borrower="C1000002"),
    ],
)
def test_rule_c_does_not_trigger_without_a_pair(world: World, other: Loan) -> None:
    world.pledge(0, Loan("VIN-Y1"))
    assert world.pledge(1, other)[0]["result"] == "LOCKED"


def test_rule_c_ignores_the_callers_own_locks(world: World) -> None:
    world.pledge(0, Loan("VIN-Z1"))
    assert world.pledge(0, Loan("VIN-Z2"))[0]["result"] == "LOCKED"


def test_rule_c_prefers_most_equal_kinds_then_the_oldest_lock(world: World) -> None:
    locks = world.pledge(0, Loan("VIN-M1", day="2026-08-20"), Loan("VIN-M2"), Loan("VIN-M3"))
    assert [r["result"] for r in locks] == ["LOCKED", "LOCKED", "LOCKED"]
    result = world.pledge(1, Loan("VIN-M4"))[0]
    assert result["match_basis"] == "FIELDS"
    case_id = result["case_id"]
    holder_alerts = [m for m in world.messages(0, "ALERT") if m["payload"]["case_id"] == case_id]
    assert len(holder_alerts) == 1
    assert holder_alerts[0]["payload"]["asset_token"] == Loan("VIN-M2").code("ASSET")


def test_new_case_alerts_both_parties_with_their_own_codes(world: World) -> None:
    original, copy = Loan("VIN-E"), Loan("VIN-E")
    world.pledge(0, original)
    result = world.pledge(1, copy)[0]
    holder = world.messages(0, "ALERT")
    claimant = world.messages(1, "ALERT")
    assert len(holder) == 1 and len(claimant) == 1
    assert holder[0]["payload"]["role"] == "HOLDER" and claimant[0]["payload"]["role"] == "CLAIMANT"
    assert holder[0]["payload"]["case_id"] == claimant[0]["payload"]["case_id"] == result["case_id"]
    for message in holder + claimant:
        assert world.ids[0] not in json.dumps(message["payload"]) or message in holder
        assert world.ids[1] not in json.dumps(message["payload"])
    assert world.messages(2, "ALERT") == []


def test_a_repeated_conflict_reuses_the_open_case_without_new_events_or_alerts(world: World) -> None:
    loan = Loan("VIN-F")
    world.pledge(0, loan)
    first = world.pledge(1, loan)[0]
    events_before = world.events()
    second = world.pledge(1, loan)[0]
    assert second["case_id"] == first["case_id"]
    added = world.events()[len(events_before) :]
    assert "CASE_OPENED" not in added and "CONFLICT_DETECTED" not in added
    assert len(world.messages(1, "ALERT")) == 1


def test_a_third_member_claiming_the_same_car_gets_its_own_case(world: World) -> None:
    loan = Loan("VIN-F3")
    world.pledge(0, loan)
    second = world.pledge(1, loan)[0]
    third = world.pledge(2, Loan("VIN-F3", cents=2_400_000))[0]
    assert third["case_id"] != second["case_id"]
    assert (third["match_kind"], second["match_kind"]) == ("LIKELY", "EXACT")
    assert len(world.messages(2, "ALERT")) == 1
    assert len(world.messages(0, "ALERT")) == 2


def test_events_of_one_submission_in_order_and_as_of_seq(world: World) -> None:
    world.pledge(0, Loan("VIN-G1"))
    before = len(world.events())
    result = world.submit(
        1, SubmissionKind.PLEDGE, Loan("VIN-G1").item(), Loan("VIN-G2", borrower="C2").item()
    )
    added = world.events()[before:]
    assert added == [
        "CONFLICT_DETECTED",
        "CASE_OPENED",
        "LOCK_ACQUIRED",
        "CERTIFICATE_ISSUED",
        "CHECKPOINT_SIGNED",
    ]
    assert result["as_of_seq"] == before + 4
    checkpoint = plain(world.registry.latest_checkpoint())["body"]
    assert checkpoint["last_event_seq"] == before + 4


def test_start_appends_member_joined_and_signs_checkpoint_one() -> None:
    world = World()
    assert world.events() == ["MEMBER_JOINED"] * 3 + ["CHECKPOINT_SIGNED"]
    body = plain(world.registry.latest_checkpoint())["body"]
    assert body["checkpoint_seq"] == 1 and body["last_event_seq"] == 3
    for member in range(3):
        assert len(world.messages(member, "CHECKPOINT")) == 1


def test_release_frees_the_asset_and_clears_open_cases_as_late_release(world: World) -> None:
    loan = Loan("VIN-H")
    world.pledge(0, loan)
    conflict = world.pledge(1, loan)[0]
    released = world.release(0, loan)[0]
    assert released["result"] == "RELEASED" and released["lock_id"]
    for member in (0, 1):
        updates = world.messages(member, "CASE_UPDATE")
        assert [u["payload"]["status"] for u in updates] == ["CLEARED_LATE_RELEASE"]
        assert updates[0]["payload"]["case_id"] == conflict["case_id"]
    assert world.pledge(2, loan)[0]["result"] == "LOCKED"
    assert world.messages(2, "ALERT") == []


def test_release_by_a_non_holder_is_rejected(world: World) -> None:
    loan = Loan("VIN-I")
    world.pledge(0, loan)
    result = world.release(1, loan)[0]
    assert result["result"] == "REJECTED" and result["reason"] == "NOT_HOLDER" and result["lock_id"] is None
    assert world.release(1, Loan("VIN-NEVER"))[0]["reason"] == "NOT_HOLDER"


def test_positions_resolve_only_when_both_agree(world: World) -> None:
    loan = Loan("VIN-J")
    world.pledge(0, loan)
    case_id = str(world.pledge(1, loan)[0]["case_id"])
    view = world.registry.record_position(world.ids[1], case_id, Position.DATA_ERROR)
    assert view["status"] == "OPEN" and view["awaiting_counterparty"] is True and view["role"] == "CLAIMANT"
    assert world.registry.record_position(world.ids[1], case_id, Position.DATA_ERROR)["status"] == "OPEN"
    with pytest.raises(PositionAlreadyRecordedError):
        world.registry.record_position(world.ids[1], case_id, Position.CONFIRMED_DOUBLE_PLEDGE)
    final = world.registry.record_position(world.ids[0], case_id, Position.DATA_ERROR)
    assert final["status"] == "CLEARED_DATA_ERROR" and final["role"] == "HOLDER" and final["resolved_seq"]
    assert (
        world.registry.record_position(world.ids[0], case_id, Position.DATA_ERROR)["status"]
        == "CLEARED_DATA_ERROR"
    )
    with pytest.raises(CaseNotFoundError):
        world.registry.record_position(world.ids[2], case_id, Position.DATA_ERROR)


def test_confirmed_double_pledge_and_late_positions(world: World) -> None:
    loan = Loan("VIN-K")
    world.pledge(0, loan)
    case_id = str(world.pledge(1, loan)[0]["case_id"])
    world.registry.record_position(world.ids[0], case_id, Position.CONFIRMED_DOUBLE_PLEDGE)
    view = world.registry.record_position(world.ids[1], case_id, Position.CONFIRMED_DOUBLE_PLEDGE)
    assert view["status"] == CaseStatus.CONFIRMED.value
    assert [u["payload"]["status"] for u in world.messages(0, "CASE_UPDATE")] == ["CONFIRMED"]
    other = Loan("VIN-K2")
    world.pledge(0, other)
    second = str(world.pledge(1, other)[0]["case_id"])
    world.release(0, other)
    with pytest.raises(CaseAlreadyResolvedError):
        world.registry.record_position(world.ids[1], second, Position.DATA_ERROR)


def test_a_field_case_cleared_as_data_error_lets_the_claimant_lock_on_resubmission(world: World) -> None:
    world.pledge(0, Loan("VIN-L1"))
    copy = Loan("VIN-L2")
    case_id = str(world.pledge(1, copy)[0]["case_id"])
    world.registry.record_position(world.ids[0], case_id, Position.DATA_ERROR)
    world.registry.record_position(world.ids[1], case_id, Position.DATA_ERROR)
    assert world.pledge(1, copy)[0]["result"] == "LOCKED"


def test_members_never_see_each_others_resources(world: World) -> None:
    loan = Loan("VIN-N")
    result = world.submit(0, SubmissionKind.PLEDGE, loan.item())
    submission_id, certificate_id = str(result["submission_id"]), str(result["certificate_id"])
    assert world.registry.submission(world.ids[0], submission_id)["submission_id"] == submission_id
    with pytest.raises(SubmissionNotFoundError):
        world.registry.submission(world.ids[1], submission_id)
    with pytest.raises(CertificateNotFoundError):
        world.registry.certificate(world.ids[1], certificate_id)
    message_id = str(world.messages(0, "CHECKPOINT")[0]["message_id"])
    with pytest.raises(OutboxMessageNotFoundError):
        world.registry.ack(world.ids[1], message_id)
    world.pledge(1, loan)
    cases = world.registry.cases(world.ids[1], None, 50, 0)
    assert world.ids[0] not in json.dumps(cases)


def test_outbox_positions_increase_and_ack_removes_a_message(world: World) -> None:
    world.pledge(0, Loan("VIN-O1"))
    world.pledge(0, Loan("VIN-O2"))
    page = plain(world.registry.outbox(world.ids[0], 2))
    positions = [m["position"] for m in page["messages"]]
    assert positions == [1, 2] and page["has_more"] is True
    first = str(page["messages"][0]["message_id"])
    world.registry.ack(world.ids[0], first)
    world.registry.ack(world.ids[0], first)
    after = plain(world.registry.outbox(world.ids[0], 200))
    assert [m["position"] for m in after["messages"]] == [2, 3]


def test_cases_page_filters_by_status(world: World) -> None:
    loan = Loan("VIN-P")
    world.pledge(0, loan)
    world.pledge(1, loan)
    assert world.registry.cases(world.ids[0], CaseStatus.OPEN, 50, 0)["total"] == 1
    assert world.registry.cases(world.ids[0], CaseStatus.CONFIRMED, 50, 0)["total"] == 0
