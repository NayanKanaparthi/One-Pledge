"""The payments client reads the whole window and proves each page complete (SPEC_DECISIONS 52).

A stand-in payments service pages a list of payments by `limit` and `offset` exactly as
the contract says (newest first). Each breakage of decision 52 is one knob on the stand-in,
and each must be MALFORMED_RESPONSE (decision 49), never a silently short window.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta

import httpx
import pytest

from bank_testkit.memory.bank import MemoryBank, vins
from onepledge_bank_agent.domain import Vin
from onepledge_bank_agent.domain.enums import FailureCode, FailureReason, LoanState, TapeStatus
from onepledge_bank_agent.domain.errors import OutsideRecordsRejectedError
from onepledge_bank_agent.domain.outside_records import PaymentWindow
from onepledge_bank_agent.infrastructure.http.base import RetryingHttpCaller
from onepledge_bank_agent.infrastructure.http.outside_records import OutsideRecordsClient

pytestmark = pytest.mark.unit

VIN = Vin("1HGCM82633A004352")
OTHER_VIN = "5YJ3E1EA2KF317000"
WINDOW_FROM, WINDOW_TO = date(2026, 7, 17), date(2026, 10, 15)

type Page = dict[str, object]
type Breakage = Callable[[Page, int], Page]


class Quiet:
    def info(self, event: str, **fields: object) -> None:
        """Nothing."""

    def warning(self, event: str, **fields: object) -> None:
        """Nothing."""

    def failure(self, event: str, error: BaseException, **fields: object) -> None:
        """Nothing."""


def payment(paid_on: date, vin: str = VIN.value) -> dict[str, object]:
    return {
        "payment_id": str(uuid.uuid4()),
        "vin": vin,
        "paid_on": paid_on.isoformat(),
        "amount_cents": 500,
        "deposit_bank_code": "HARBOR",
    }


def newest_first(count: int) -> list[dict[str, object]]:
    """`count` payments inside the window, ordered by paid_on descending."""
    return [payment(WINDOW_TO - timedelta(days=(day * 90) // max(count, 1))) for day in range(count)]


@dataclass
class StandIn:
    """Pages `items` by limit and offset; `breakage` rewrites the page served at a given offset."""

    items: list[dict[str, object]]
    breakage: Breakage | None = None
    seen: list[dict[str, str]] = field(default_factory=list)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        self.seen.append(params)
        limit, offset = int(params["limit"]), int(params.get("offset", "0"))
        page: Page = {
            "items": self.items[offset : offset + limit],
            "total": len(self.items),
            "limit": limit,
            "offset": offset,
        }
        if self.breakage is not None:
            page = self.breakage(page, offset)
        return httpx.Response(200, json=page)


def read(service: StandIn) -> PaymentWindow:
    http = httpx.Client(base_url="http://outside.test", transport=httpx.MockTransport(service))
    caller = RetryingHttpCaller(http, "opk_" + "a" * 43, "outside_records", Quiet(), lambda _: None)
    return OutsideRecordsClient(caller).payments(VIN, WINDOW_FROM, WINDOW_TO)


@pytest.mark.parametrize("count", [0, 1, 199, 200, 201, 450, 10_000])
def test_the_client_pages_with_offset_until_it_holds_total(count: int) -> None:
    service = StandIn(newest_first(count))
    window = read(service)
    assert len(window.payments) == window.total_reported == count
    pages = max(1, -(-count // 200))
    assert [(p["limit"], p["offset"]) for p in service.seen] == [("200", str(200 * n)) for n in range(pages)]
    assert all(
        p["vin"] == VIN.value and p["from"] == "2026-07-17" and p["to"] == "2026-10-15" for p in service.seen
    )


def too_many(page: Page, offset: int) -> Page:
    items = page["items"]
    assert isinstance(items, list)
    return {**page, "items": [*items, *newest_first(201 - len(items))]} if offset == 0 else page


def empty_before_total(page: Page, offset: int) -> Page:
    return {**page, "items": []} if offset == 200 else page


def total_changes(page: Page, offset: int) -> Page:
    return {**page, "total": 449} if offset == 200 else page


def foreign_vin(page: Page, offset: int) -> Page:
    items = page["items"]
    assert isinstance(items, list)
    return {**page, "items": [*items[:-1], {**items[-1], "vin": OTHER_VIN}]} if offset == 200 else page


def before_window(page: Page, offset: int) -> Page:
    items = page["items"]
    assert isinstance(items, list)
    late = {**items[-1], "paid_on": (WINDOW_FROM - timedelta(days=1)).isoformat()}
    return {**page, "items": [*items[:-1], late]} if offset == 400 else page


def after_window(page: Page, offset: int) -> Page:
    items = page["items"]
    assert isinstance(items, list)
    early = {**items[0], "paid_on": (WINDOW_TO + timedelta(days=1)).isoformat()}
    return {**page, "items": [early, *items[1:]]} if offset == 0 else page


def oldest_first(page: Page, offset: int) -> Page:
    items = page["items"]
    assert isinstance(items, list)
    return {**page, "items": list(reversed(items))} if offset == 0 else page


def out_of_order_across_pages(page: Page, offset: int) -> Page:
    items = page["items"]
    assert isinstance(items, list)
    newer = {**items[0], "paid_on": WINDOW_TO.isoformat()}
    return {**page, "items": [newer, *items[1:]]} if offset == 200 else page


def over_the_ceiling(page: Page, offset: int) -> Page:
    return {**page, "total": 10_001}


def more_than_total(page: Page, offset: int) -> Page:
    return {**page, "total": 199} if offset == 0 else page


BREAKAGES: list[tuple[str, Breakage]] = [
    ("a page with more items than limit", too_many),
    ("an empty page while fewer than total are held", empty_before_total),
    ("total differs between pages", total_changes),
    ("an item about another vin", foreign_vin),
    ("paid_on before the window", before_window),
    ("paid_on after the window", after_window),
    ("items oldest first within a page", oldest_first),
    ("items out of order across pages", out_of_order_across_pages),
    ("total over 10,000", over_the_ceiling),
    ("a page holding more items than total", more_than_total),
]


@pytest.mark.parametrize("breakage", [b for _, b in BREAKAGES], ids=[name for name, _ in BREAKAGES])
def test_an_incomplete_or_inconsistent_window_is_malformed(breakage: Breakage) -> None:
    with pytest.raises(OutsideRecordsRejectedError) as raised:
        read(StandIn(newest_first(450), breakage))
    error = raised.value
    assert (error.failure_code, error.reason, error.http_status, error.transient) == (
        FailureCode.OUTSIDE_RECORDS_REJECTED,
        FailureReason.MALFORMED_RESPONSE,
        200,
        False,
    )


def test_a_moving_total_is_read_once_per_page_and_never_held_as_evidence() -> None:
    """SPEC_DECISIONS 68.9: the client stops at the page whose total moved; it does not loop."""
    service = StandIn(newest_first(450), total_changes)
    with pytest.raises(OutsideRecordsRejectedError):
        read(service)
    assert [p["offset"] for p in service.seen] == ["0", "200"]


def client_over(service: StandIn) -> OutsideRecordsClient:
    """The real payments client, over a stand-in service."""
    http = httpx.Client(base_url="http://outside.test", transport=httpx.MockTransport(service))
    return OutsideRecordsClient(
        RetryingHttpCaller(http, "opk_" + "a" * 43, "outside_records", Quiet(), lambda _: None)
    )


def test_a_moving_total_fails_the_tape_rejected_and_malformed_and_holds_no_loan() -> None:
    bank = MemoryBank()
    (vin,) = vins(1)
    payments = [payment(WINDOW_TO - timedelta(days=(day * 90) // 450), vin) for day in range(450)]
    service = StandIn(payments, total_changes)
    bank.payments = client_over(service)
    tape = bank.upload([vin])
    run = bank.run(tape)[-1]
    assert run.status is TapeStatus.FAILED and run.failure is not None
    assert (run.failure["failure_code"], run.failure["reason"]) == (
        "OUTSIDE_RECORDS_REJECTED",
        "MALFORMED_RESPONSE",
    )
    progress = bank.tape(tape).progress
    assert (progress.failed_stage, progress.resume_status) == (TapeStatus.CHECKED, TapeStatus.CLEANED)
    state = bank.world.store.state
    assert [loan.state for loan in state.loans.values()] == [LoanState.PENDING], "no loan is held"
    assert state.checks == {}, "no check result rests on a partial window"
    assert len(service.seen) == 2
    service.breakage = None
    bank.retry(tape)
    assert bank.run(tape)[-1].status is TapeStatus.COMPLETED
    (stored,) = bank.world.store.state.checks.values()
    assert "PAYMENT_IN_WINDOW" in {result.reason.value for result in stored}
