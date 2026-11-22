"""`SqlFacilityRepository`: `lenders` and `facilities`."""

from __future__ import annotations

import uuid

from sqlalchemy import Boolean, ColumnElement, Result, func, literal_column, or_, select
from sqlalchemy.dialects.postgresql import Insert, insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from onepledge_bank_agent.application.ports import Upserted
from onepledge_bank_agent.domain.entities import Facility, FacilityWithLender, Lender
from onepledge_bank_agent.domain.errors import ReferenceDataError
from onepledge_bank_agent.infrastructure.db.models import FacilityRow, LenderRow
from onepledge_bank_agent.infrastructure.db.repositories.mapping import facility_from_rows


class SqlFacilityRepository:
    """Reference data: read by every stage, written only by reference seeding as the owner role."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, facility_id: uuid.UUID) -> FacilityWithLender | None:
        """A facility with its lender."""
        statement = (
            select(FacilityRow, LenderRow)
            .join(LenderRow, LenderRow.lender_id == FacilityRow.lender_id)
            .where(FacilityRow.facility_id == facility_id)
        )
        row = self._session.execute(statement).first()
        if row is None:
            return None
        facility_row, lender_row = row
        return facility_from_rows(facility_row, lender_row)

    def upsert_lender(self, lender: Lender) -> Upserted:
        """Insert, or update name and LEI of, the lender with this id."""
        statement = insert(LenderRow).values(
            lender_id=lender.lender_id,
            legal_name=lender.legal_name,
            lei=lender.lei.value,
            created_at=func.now(),
        )
        updated = ("legal_name", "lei")
        return self._execute(
            statement.on_conflict_do_update(
                index_elements=[LenderRow.lender_id],
                set_={name: statement.excluded[name] for name in updated},
                where=_changed(LenderRow, statement, updated),
            )
        )

    def upsert_facility(self, facility: Facility) -> Upserted:
        """Insert, or update every field but the status of, the facility with this id."""
        statement = insert(FacilityRow).values(
            facility_id=facility.facility_id,
            lender_id=facility.lender_id,
            code=facility.code,
            name=facility.name,
            credit_limit_cents=facility.credit_limit_cents,
            advance_rate_bps=facility.advance_rate_bps,
            status=facility.status.value,
            opened_on=facility.opened_on,
        )
        updated = ("lender_id", "code", "name", "credit_limit_cents", "advance_rate_bps", "opened_on")
        return self._execute(
            statement.on_conflict_do_update(
                index_elements=[FacilityRow.facility_id],
                set_={name: statement.excluded[name] for name in updated},
                where=_changed(FacilityRow, statement, updated),
            )
        )

    def _execute(self, statement: Insert) -> Upserted:
        """Run the upsert; no returned row means the stored row already held these values."""
        try:
            returned: Result[tuple[bool]] = self._session.execute(
                statement.returning(literal_column("(xmax = 0)", Boolean))
            )
            inserted = returned.scalar_one_or_none()
        except IntegrityError:
            raise ReferenceDataError(
                "The reference data conflicts with a lender or facility already stored under another id."
            ) from None
        if inserted is None:
            return Upserted.UNCHANGED
        return Upserted.INSERTED if inserted else Upserted.UPDATED


def _changed(
    table: type[LenderRow] | type[FacilityRow], statement: Insert, names: tuple[str, ...]
) -> ColumnElement[bool]:
    """True when any of the columns would change, so an identical upsert touches no row."""
    return or_(*(getattr(table, name).is_distinct_from(statement.excluded[name]) for name in names))
