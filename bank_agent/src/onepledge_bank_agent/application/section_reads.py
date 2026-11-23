"""Reading the integrity report's sections each inside its own guard (SPEC_DECISIONS 93.8, 100).

The report and deep verification cannot fail as a whole because of stored data. Each group of
sections is read in its own transaction: a store without the expected shape leaves those
sections NOT_VERIFIED with the SQLSTATE (decision 93.8); a row that cannot be read back makes
them BROKEN and adds an `UNREADABLE` evidence problem naming the table and, where known, each
row (decision 100). Every other section is still produced.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from types import MappingProxyType

from onepledge_bank_agent.application.ports import UnitOfWork, UnitOfWorkFactory
from onepledge_bank_agent.domain.integrity import (
    EvidenceProblem,
    EvidenceProblemKind,
    EvidenceSection,
    SectionStatus,
    UnreadSection,
    evidence_section,
)
from onepledge_bank_agent.domain.store_errors import StoreMismatchError, StoreUnreadableError

SECTION_TABLES: Mapping[str, str] = MappingProxyType(
    {
        "registry_chain": "checkpoints_seen",
        "certificates": "certificates",
        "audit_log": "audit_log",
        "raw_data": "loans_raw",
        "registry_messages": "registry_messages_quarantined",
        "evidence": "integrity_runs",
    }
)
"""The table a section reads first, named when the store could not say which table it was."""


class SectionReads:
    """Runs reads for named sections and keeps what could not be read."""

    def __init__(self, units: UnitOfWorkFactory) -> None:
        self._units = units
        self.unread: list[UnreadSection] = []
        self.unreadable: list[EvidenceProblem] = []
        self._broken: set[str] = set()

    def read[T](self, sections: tuple[str, ...], query: Callable[[UnitOfWork], T]) -> T | None:
        """`query` in its own transaction, or None with the sections recorded as unread or unreadable."""
        try:
            with self._units() as unit:
                return query(unit)
        except StoreMismatchError as error:
            self.unread += [UnreadSection(section, error.sqlstate) for section in sections]
        except StoreUnreadableError as error:
            self._broken.update(sections)
            table = error.table or SECTION_TABLES.get(sections[0], sections[0])
            rows: Iterable[str | None] = error.row_ids or (None,)
            self.unreadable += [EvidenceProblem(table, row, EvidenceProblemKind.UNREADABLE) for row in rows]
        return None

    def missing(self, section: str) -> SectionStatus:
        """The status of a section whose read gave nothing: BROKEN when a row was unreadable."""
        return SectionStatus.BROKEN if section in self._broken else SectionStatus.NOT_VERIFIED

    def evidence(self, stored: Iterable[EvidenceProblem] | None) -> EvidenceSection:
        """The evidence section: the stored problems with every unreadable row added."""
        if stored is None and not self.unreadable:
            return evidence_section(None)
        return evidence_section([*(stored or ()), *self.unreadable])
