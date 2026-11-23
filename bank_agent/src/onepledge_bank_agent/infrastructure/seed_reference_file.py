"""Reads one bank's lenders and facilities from datagen's `reference.json` (SPEC 5.3, 19 `seed-reference`).

The file is hostile input (SPEC_DECISIONS 74): at most 5 MiB, parsed by the bounded parser
(nesting at most 32 levels, no key twice in one object, no `NaN`), holding no text the bank
cannot store (U+0000, a surrogate code point), then validated with strict types (a number is
a JSON integer, never a fraction or a string; an id is a UUID in its canonical spelling,
SPEC_DECISIONS 75). Every problem is one sentence that starts with the file's path: "PATH: no
such file.", "PATH: larger than 5 MiB.", "PATH: nests too deeply.", "PATH: not valid JSON.",
"PATH: holds text that cannot be stored." or "PATH: not a reference data file."
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from types import MappingProxyType

from pydantic import BaseModel, ConfigDict, ValidationError

from onepledge_bank_agent.application.ports import ReferenceData
from onepledge_bank_agent.domain.canonical_json import sha256_hex
from onepledge_bank_agent.domain.entities import Facility, Lender
from onepledge_bank_agent.domain.enums import FacilityStatus
from onepledge_bank_agent.domain.errors import InvalidValueError, ReferenceDataError
from onepledge_bank_agent.domain.values import Lei
from onepledge_bank_agent.infrastructure import bounded_json
from onepledge_bank_agent.infrastructure.encodings import holds_unstorable_text
from onepledge_bank_agent.infrastructure.http.answers import CanonicalUuid

REFERENCE_FILE = "reference.json"
PROBLEMS = MappingProxyType(
    {
        bounded_json.JsonProblem.TOO_DEEP: "nests too deeply",
        bounded_json.JsonProblem.NOT_JSON: "not valid JSON",
    }
)


class _Entry(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True, strict=True)


class _LenderEntry(_Entry):
    lender_id: CanonicalUuid
    lender_code: str
    legal_name: str
    lei: str


class _FacilityEntry(_Entry):
    facility_id: CanonicalUuid
    facility_code: str
    bank_code: str
    lender_code: str
    name: str
    credit_limit_cents: int
    advance_rate_bps: int
    opened_on: date


class _ReferenceFile(_Entry):
    lenders: list[_LenderEntry]
    facilities: list[_FacilityEntry]


class ReferenceFileSource:
    """The `ReferenceSource` port on a data directory written by `onepledge-datagen`."""

    def load(self, data_dir: Path, bank_code: str) -> ReferenceData:
        """The facilities of `bank_code` and the lenders they lend to."""
        path = data_dir / REFERENCE_FILE
        content = self._content(path)
        try:
            document = bounded_json.loads(content)
        except bounded_json.JsonRefusedError as refused:
            problem = PROBLEMS[refused.problem]
            raise ReferenceDataError(f"{path}: {problem}.") from None
        if holds_unstorable_text(document):
            raise ReferenceDataError(f"{path}: holds text that cannot be stored.")
        try:
            parsed = _ReferenceFile.model_validate_json(content)
        except ValidationError:
            raise ReferenceDataError(f"{path}: not a reference data file.") from None
        facilities = [entry for entry in parsed.facilities if entry.bank_code == bank_code]
        if not facilities:
            raise ReferenceDataError(f"{REFERENCE_FILE} has no facility for the bank code {bank_code}.")
        lenders = {entry.lender_code: entry for entry in parsed.lenders}
        if any(entry.lender_code not in lenders for entry in facilities):
            raise ReferenceDataError(f"{REFERENCE_FILE} names a lender that it does not define.")
        try:
            used = sorted({entry.lender_code for entry in facilities})
            return ReferenceData(
                lenders=tuple(self._lender(lenders[code]) for code in used),
                facilities=tuple(self._facility(entry, lenders[entry.lender_code]) for entry in facilities),
                source_sha256=sha256_hex(content),
            )
        except InvalidValueError as error:
            raise ReferenceDataError(f"{REFERENCE_FILE} has an invalid {error.field}.") from None

    @staticmethod
    def _content(path: Path) -> bytes:
        """The file's bytes, at most 5 MiB, or one sentence naming the file."""
        try:
            with path.open("rb") as file:
                content = file.read(bounded_json.MAX_BYTES + 1)
        except FileNotFoundError:
            raise ReferenceDataError(f"{path}: no such file.") from None
        except OSError as error:
            reason = (error.strerror or "it cannot be read").lower()
            raise ReferenceDataError(f"{path}: {reason}.") from None
        if len(content) > bounded_json.MAX_BYTES:
            raise ReferenceDataError(f"{path}: larger than 5 MiB.")
        return content

    @staticmethod
    def _lender(entry: _LenderEntry) -> Lender:
        return Lender(entry.lender_id, entry.legal_name, Lei(entry.lei))

    @staticmethod
    def _facility(entry: _FacilityEntry, lender: _LenderEntry) -> Facility:
        return Facility(
            facility_id=entry.facility_id,
            lender_id=lender.lender_id,
            code=entry.facility_code,
            name=entry.name,
            credit_limit_cents=entry.credit_limit_cents,
            advance_rate_bps=entry.advance_rate_bps,
            status=FacilityStatus.ACTIVE,
            opened_on=entry.opened_on,
        )
