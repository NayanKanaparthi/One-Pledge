"""Reads one bank's lenders and facilities from datagen's `reference.json` (SPEC 5.3, 19 `seed-reference`)."""

from __future__ import annotations

import uuid
from datetime import date
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError

from onepledge_bank_agent.application.ports import ReferenceData
from onepledge_bank_agent.domain.entities import Facility, Lender
from onepledge_bank_agent.domain.enums import FacilityStatus
from onepledge_bank_agent.domain.errors import InvalidValueError, ReferenceDataError
from onepledge_bank_agent.domain.values import Lei

REFERENCE_FILE = "reference.json"


class _Entry(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class _LenderEntry(_Entry):
    lender_id: uuid.UUID
    lender_code: str
    legal_name: str
    lei: str


class _FacilityEntry(_Entry):
    facility_id: uuid.UUID
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
        try:
            parsed = _ReferenceFile.model_validate_json(path.read_bytes())
        except FileNotFoundError:
            raise ReferenceDataError(f"{REFERENCE_FILE} was not found in the data directory.") from None
        except (OSError, ValidationError):
            raise ReferenceDataError(f"{REFERENCE_FILE} is not a valid reference data file.") from None
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
            )
        except InvalidValueError as error:
            raise ReferenceDataError(f"{REFERENCE_FILE} has an invalid {error.field}.") from None

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
