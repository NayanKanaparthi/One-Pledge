"""Typed view of a written data set, parsed and validated from its files (used by `verify`).

The generator checks its own output through this same view, so `generate` and
`verify` apply exactly the same invariants to exactly the same bytes.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from onepledge_datagen.domain.entities import ContractRecord, PaymentRecord, TitleRecord
from onepledge_datagen.domain.errors import DataSetUnreadableError
from onepledge_datagen.domain.text_values import parse_iso_date


@dataclass(frozen=True)
class ManifestStep:
    """One step of manifest.json."""

    step_id: str
    bank_code: str
    facility_code: str
    kind: str
    as_of: date
    file: str
    concurrent_group: str | None
    data_rows: int


@dataclass(frozen=True)
class FacilityView:
    """A facility of reference.json with its lender's name and LEI."""

    facility_code: str
    bank_code: str
    lender_code: str
    lender_legal_name: str
    lender_lei: str


@dataclass(frozen=True)
class KeyExpectation:
    """The `expected` object of an answer key row."""

    disposition: str
    cleaning_problems: tuple[str, ...]
    outcome: str
    failed_checks: tuple[str, ...]
    failed_check_reasons: Mapping[str, str]
    match_kind: str | None
    match_basis: str | None
    include: tuple[str, ...]
    exclude: tuple[str, ...]
    alert: tuple[str, str] | None


@dataclass(frozen=True)
class KeyRow:
    """One row of a planted case."""

    case_id: str
    case_type: str
    category: str
    role: str
    step_id: str
    bank_code: str
    row_number: int
    expected: KeyExpectation
    race_group: str | None


@dataclass(frozen=True)
class KeyMessiness:
    """One data quality row."""

    kind: str
    step_id: str
    row_number: int
    expected_action: str
    expected_problem: str


@dataclass(frozen=True)
class DataSetView:
    """Everything a data directory holds, parsed."""

    profile: str
    seed: int
    steps: tuple[ManifestStep, ...]
    facilities: Mapping[str, FacilityView]
    case_rows: tuple[KeyRow, ...]
    messiness: tuple[KeyMessiness, ...]
    inherent_fixes: Mapping[str, frozenset[str]]
    tapes: Mapping[str, bytes]
    titles: tuple[TitleRecord, ...]
    payments: tuple[PaymentRecord, ...]
    contracts: tuple[ContractRecord, ...]
    label_scanned_text: Mapping[str, str]


def _field(document: object, name: str, where: str) -> object:
    if not isinstance(document, dict) or name not in document:
        raise DataSetUnreadableError(f"{where} has no field {name}.")
    return document[name]


def _text(document: object, name: str, where: str) -> str:
    value = _field(document, name, where)
    if not isinstance(value, str):
        raise DataSetUnreadableError(f"{where}: field {name} is not text.")
    return value


def _number(document: object, name: str, where: str) -> int:
    value = _field(document, name, where)
    if not isinstance(value, int) or isinstance(value, bool):
        raise DataSetUnreadableError(f"{where}: field {name} is not an integer.")
    return value


def _day(document: object, name: str, where: str) -> date:
    value = parse_iso_date(_text(document, name, where))
    if value is None:
        raise DataSetUnreadableError(f"{where}: field {name} is not a YYYY-MM-DD date.")
    return value


def _optional_text(document: object, name: str, where: str) -> str | None:
    value = _field(document, name, where)
    if value is not None and not isinstance(value, str):
        raise DataSetUnreadableError(f"{where}: field {name} is not text or null.")
    return value


def _texts(document: object, name: str, where: str) -> tuple[str, ...]:
    value = _field(document, name, where)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise DataSetUnreadableError(f"{where}: field {name} is not a list of text.")
    return tuple(value)


def _list(document: object, name: str, where: str) -> Sequence[object]:
    value = _field(document, name, where)
    if not isinstance(value, list):
        raise DataSetUnreadableError(f"{where}: field {name} is not a list.")
    return value


def parse_steps(manifest: object) -> tuple[ManifestStep, ...]:
    """The steps of manifest.json."""
    steps = []
    for entry in _list(manifest, "steps", "manifest.json"):
        where = "manifest.json step"
        steps.append(
            ManifestStep(
                step_id=_text(entry, "step_id", where),
                bank_code=_text(entry, "bank_code", where),
                facility_code=_text(entry, "facility_code", where),
                kind=_text(entry, "kind", where),
                as_of=_day(entry, "as_of_date", where),
                file=_text(entry, "file", where),
                concurrent_group=_optional_text(entry, "concurrent_group", where),
                data_rows=_number(entry, "data_rows", where),
            )
        )
    return tuple(steps)


def parse_facilities(reference: object) -> dict[str, FacilityView]:
    """Facilities of reference.json joined to their lenders."""
    lenders = {
        _text(entry, "lender_code", "reference.json lender"): entry
        for entry in _list(reference, "lenders", "reference.json")
    }
    facilities = {}
    for entry in _list(reference, "facilities", "reference.json"):
        where = "reference.json facility"
        lender = lenders.get(_text(entry, "lender_code", where))
        if lender is None:
            raise DataSetUnreadableError("reference.json names a facility whose lender is missing.")
        code = _text(entry, "facility_code", where)
        facilities[code] = FacilityView(
            facility_code=code,
            bank_code=_text(entry, "bank_code", where),
            lender_code=_text(entry, "lender_code", where),
            lender_legal_name=_text(lender, "legal_name", "reference.json lender"),
            lender_lei=_text(lender, "lei", "reference.json lender"),
        )
    return facilities


def _expectation(document: object) -> KeyExpectation:
    where = "answer key expectation"
    alert = _field(document, "alert", where)
    alert_pair = None if alert is None else (_text(alert, "role", where), _text(alert, "match_kind", where))
    reasons = _field(document, "failed_check_reasons", where)
    if not isinstance(reasons, dict) or not all(isinstance(v, str) for v in reasons.values()):
        raise DataSetUnreadableError("answer key expectation has malformed failed_check_reasons.")
    return KeyExpectation(
        disposition=_text(document, "disposition", where),
        cleaning_problems=_texts(document, "cleaning_problems", where),
        outcome=_text(document, "outcome", where),
        failed_checks=_texts(document, "failed_checks", where),
        failed_check_reasons=dict(reasons),
        match_kind=_optional_text(document, "match_kind", where),
        match_basis=_optional_text(document, "match_basis", where),
        include=_texts(document, "kinds_matched_include", where),
        exclude=_texts(document, "kinds_matched_exclude", where),
        alert=alert_pair,
    )


def parse_answer_key(document: object) -> tuple[tuple[KeyRow, ...], tuple[KeyMessiness, ...]]:
    """Case rows and data quality rows of the answer key."""
    rows = []
    for case in _list(document, "cases", "answer key"):
        where = "answer key case"
        for row in _list(case, "rows", where):
            rows.append(
                KeyRow(
                    case_id=_text(case, "case_id", where),
                    case_type=_text(case, "case_type", where),
                    category=_text(case, "category", where),
                    role=_text(row, "role", where),
                    step_id=_text(row, "step_id", where),
                    bank_code=_text(row, "bank_code", where),
                    row_number=_number(row, "row_number", where),
                    expected=_expectation(_field(row, "expected", where)),
                    race_group=_optional_text(case, "race_group", where),
                )
            )
    messiness = tuple(
        KeyMessiness(
            kind=_text(entry, "kind", "answer key messiness"),
            step_id=_text(entry, "step_id", "answer key messiness"),
            row_number=_number(entry, "row_number", "answer key messiness"),
            expected_action=_text(entry, "expected_action", "answer key messiness"),
            expected_problem=_text(entry, "expected_problem", "answer key messiness"),
        )
        for entry in _list(document, "messiness", "answer key")
    )
    return tuple(rows), messiness


def parse_inherent_fixes(document: object) -> dict[str, frozenset[str]]:
    """The answer key's `inherent_fixes`: facility code to the FIXED problems its format always causes."""
    found = _field(document, "inherent_fixes", "answer key")
    if not isinstance(found, dict) or not all(
        isinstance(code, str) and isinstance(problems, list) and all(isinstance(p, str) for p in problems)
        for code, problems in found.items()
    ):
        raise DataSetUnreadableError("answer key has a malformed inherent_fixes map.")
    return {code: frozenset(problems) for code, problems in found.items()}
