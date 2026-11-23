"""`SqlIntegrityRunRepository`: `integrity_runs` (insert-only)."""

from __future__ import annotations

import uuid

from sqlalchemy import insert, select
from sqlalchemy.orm import Session

from onepledge_bank_agent.application.evidence_ports import IntegrityRun
from onepledge_bank_agent.domain.enums import IntegrityRunStatus
from onepledge_bank_agent.infrastructure.db.models import IntegrityRunRow
from onepledge_bank_agent.infrastructure.db.readable import read_rows


def _run(row: IntegrityRunRow) -> IntegrityRun:
    return IntegrityRun(row.run_id, row.ran_at, row.triggered_by, IntegrityRunStatus(row.status), row.report)


class SqlIntegrityRunRepository:
    """A record of every deep verification."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, run: IntegrityRun) -> None:
        """Insert one run with its report."""
        self._session.execute(
            insert(IntegrityRunRow).values(
                run_id=run.run_id,
                ran_at=run.ran_at,
                triggered_by=run.triggered_by,
                status=run.status.value,
                report=dict(run.report),
            )
        )

    def latest(self) -> IntegrityRun | None:
        """The most recent run."""
        statement = (
            select(IntegrityRunRow).order_by(IntegrityRunRow.ran_at.desc(), IntegrityRunRow.run_id).limit(1)
        )
        found = read_rows(self._session, statement, IntegrityRunRow.run_id, _run, "integrity_runs")
        return found[0] if found else None

    def ids(self) -> set[uuid.UUID]:
        """The id of every stored run."""
        return set(self._session.execute(select(IntegrityRunRow.run_id)).scalars())
