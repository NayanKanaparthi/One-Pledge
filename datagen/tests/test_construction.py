"""Each planted case is built the way SPEC 5.7 describes, checked from the files with independent code."""

from __future__ import annotations

import csv
import io
import json
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.integration

VALUES = {**{str(d): d for d in range(10)}, **dict(zip("ABCDEFGH", range(1, 9), strict=True))}
VALUES.update({"J": 1, "K": 2, "L": 3, "M": 4, "N": 5, "P": 7, "R": 9})
VALUES.update(dict(zip("STUVWXYZ", range(2, 10), strict=True)))
WEIGHTS = (8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2)


def valid(vin: str) -> bool:
    if len(vin) != 17 or any(c not in VALUES for c in vin):
        return False
    remainder = sum(VALUES[c] * w for c, w in zip(vin, WEIGHTS, strict=True)) % 11
    return vin[8] == ("X" if remainder == 10 else str(remainder))


class DataSet:
    """Independent reader of a data directory."""

    def __init__(self, directory: Path) -> None:
        self.key: dict[str, Any] = json.loads((directory / "answer_key/answer_key.json").read_text())
        manifest = json.loads((directory / "manifest.json").read_text())
        self.as_of = {s["step_id"]: date.fromisoformat(s["as_of_date"]) for s in manifest["steps"]}
        self.rows: dict[tuple[str, int], list[str]] = {}
        for step in manifest["steps"]:
            text = (directory / step["file"]).read_text(encoding="utf-8-sig")
            for number, row in enumerate([r for r in csv.reader(io.StringIO(text, newline="")) if r][1:], 1):
                self.rows[(step["step_id"], number)] = row
        self.titles = {r[0] for r in self._csv(directory / "outside_records/titles.csv")}
        self.payments: dict[str, list[date]] = defaultdict(list)
        for r in self._csv(directory / "outside_records/payments.csv"):
            self.payments[r[1]].append(date.fromisoformat(r[2]))
        self.contracts: dict[str, list[tuple[int, date]]] = defaultdict(list)
        for r in self._csv(directory / "outside_records/contracts.csv"):
            self.contracts[r[1]].append((int(r[3]), date.fromisoformat(r[4])))

    @staticmethod
    def _csv(path: Path) -> list[list[str]]:
        return list(csv.reader(io.StringIO(path.read_text(), newline="")))[1:]

    def cases(self, case_type: str) -> list[dict[str, list[str]]]:
        found = []
        for case in self.key["cases"]:
            if case["case_type"] == case_type:
                found.append(
                    {
                        f"{r['role']}{i}": self.rows[(r["step_id"], r["row_number"])]
                        for i, r in enumerate(case["rows"])
                    }
                )
        return found

    def step_rows(self, case_type: str) -> list[tuple[str, list[str]]]:
        return [
            (r["step_id"], self.rows[(r["step_id"], r["row_number"])])
            for c in self.key["cases"]
            if c["case_type"] == case_type
            for r in c["rows"]
        ]


def cents(cell: str) -> int:
    whole, _, fraction = cell.replace("$", "").replace(",", "").partition(".")
    return int(whole) * 100 + int(fraction or 0)


def day(cell: str) -> date:
    return datetime.strptime(cell, "%m/%d/%Y").date()


@pytest.fixture(scope="module")
def data(full_dir: Path) -> DataSet:
    return DataSet(full_dir)


def test_exact_copies_are_identical_rows(data: DataSet) -> None:
    for case in data.cases("EXACT_COPY"):
        assert case["ORIGINAL0"] == case["COPY1"]


def test_amount_changed_copies(data: DataSet) -> None:
    for case in data.cases("COPY_AMOUNT_CHANGED"):
        original, copy = case["ORIGINAL0"], case["COPY1"]
        a, b = cents(original[4]), cents(copy[4])
        assert 50_000 <= abs(a - b) <= 300_000 and a // 10_000 != b // 10_000
        assert sorted(amount for amount, _ in data.contracts[copy[1]]) == sorted([a, b])
    for case in data.cases("COPY_AMOUNT_CHANGED_NO_CONTRACT"):
        assert [amount for amount, _ in data.contracts[case["COPY1"][1]]] == [cents(case["ORIGINAL0"][4])]


def test_date_changed_copies(data: DataSet) -> None:
    for case in data.cases("COPY_DATE_CHANGED"):
        moved = day(case["COPY1"][3])
        assert 7 <= abs((moved - day(case["ORIGINAL0"][3])).days) <= 60 and moved <= data.as_of["S09"]
        assert any(abs((signed - moved).days) <= 3 for _, signed in data.contracts[case["COPY1"][1]])


def _one_change(first: str, second: str) -> int:
    return sum(a != b for a, b in zip(first, second, strict=True))


def test_altered_vin_copies(data: DataSet) -> None:
    for case in data.cases("COPY_VIN_ALTERED_CHECK_DIGIT_INVALID"):
        assert _one_change(case["ORIGINAL0"][1], case["COPY1"][1]) == 1 and not valid(case["COPY1"][1])
    for case_type in ("COPY_VIN_ALTERED_NO_TITLE", "COPY_VIN_ALTERED_TITLE_PENDING"):
        for case in data.cases(case_type):
            original, copy = case["ORIGINAL0"][1], case["COPY1"][1]
            index = next(i for i, (a, b) in enumerate(zip(original, copy, strict=True)) if a != b)
            assert (
                _one_change(original, copy) == 1
                and index != 8
                and VALUES[original[index]] == VALUES[copy[index]]
            )
            assert (
                valid(copy) and copy not in data.titles and copy not in data.payments and data.contracts[copy]
            )
    for case in data.cases("COPY_VIN_ALTERED_TITLE_PENDING"):
        assert (data.as_of["S09"] - day(case["COPY1"][3])).days <= 30


def test_invented_and_blind_spot_loans(data: DataSet) -> None:
    for case_type, low, high in (
        ("INVENTED_NO_TITLE", 120, 400),
        ("BLIND_SPOT_INVENTED_IN_PENDING_WINDOW", 5, 20),
    ):
        for step, row in data.step_rows(case_type):
            assert low <= (data.as_of[step] - day(row[3])).days <= high
            assert row[1] not in data.titles and row[1] not in data.payments and data.contracts[row[1]]


def test_local_failure_loans(data: DataSet) -> None:
    for step, row in data.step_rows("NO_PAYMENTS_90D"):
        assert (data.as_of[step] - day(row[3])).days >= 240 and row[1] in data.titles
        assert 100 <= (data.as_of[step] - max(data.payments[row[1]])).days <= 200
    for step, row in data.step_rows("TITLE_PENDING_HONEST"):
        assert 3 <= (data.as_of[step] - day(row[3])).days <= 15 and row[1] not in data.titles


def test_two_cars_pairs(data: DataSet) -> None:
    cases = data.cases("TWO_CARS_ONE_BORROWER")
    near = 0
    for case in cases:
        a, b = case["CAR_A0"], case["CAR_B1"]
        assert a[2] == b[2] and a[1] != b[1]
        same_month = day(a[3]).replace(day=1) == day(b[3]).replace(day=1)
        same_band = cents(a[4]) // 100_000 == cents(b[4]) // 100_000
        if same_month and same_band:
            near += 1
            assert day(a[3]) != day(b[3]) and cents(a[4]) // 10_000 != cents(b[4]) // 10_000
        else:
            assert not same_month and not same_band
    assert near == (len(cases) + 1) // 2


def test_release_and_refinance_rows(data: DataSet) -> None:
    for case_type in ("MOVED_AFTER_RELEASE", "REFINANCED"):
        for case in data.cases(case_type):
            assert case["RELEASE1"][1] == case["ORIGINAL0"][1] and case["RELEASE1"][0] == case["ORIGINAL0"][0]
    for case in data.cases("MOVED_AFTER_RELEASE"):
        assert case["REPLEDGE2"] == case["ORIGINAL0"]
    for case in data.cases("REFINANCED"):
        new, old = case["NEW_LOAN2"], case["ORIGINAL0"]
        assert new[1] == old[1] and new[2] == old[2] and new[0] != old[0]
        assert date(2026, 11, 1) <= day(new[3]) <= date(2026, 11, 10) and len(data.contracts[new[1]]) == 2


def test_race_rows_are_identical(data: DataSet) -> None:
    for case in data.cases("RACE_PAIR"):
        first, second = case.values()
        assert first == second
