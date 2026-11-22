"""SQLAlchemy models of the bank database (SPEC 12.1), one module per group of tables."""

from onepledge_bank_agent.infrastructure.db.models.base import Base
from onepledge_bank_agent.infrastructure.db.models.evidence import (
    AuditLogRow,
    IntegrityRunRow,
    QuarantinedMessageRow,
)
from onepledge_bank_agent.infrastructure.db.models.loans import LoanRow, LoanTokenRow, LocalCheckRow
from onepledge_bank_agent.infrastructure.db.models.reference import (
    FacilityRow,
    LenderRow,
    LoginAttemptRow,
    UserRow,
)
from onepledge_bank_agent.infrastructure.db.models.registry import (
    AlertRow,
    CertificateRow,
    CheckpointSeenRow,
    RegistryRequestItemRow,
    RegistryRequestRow,
)
from onepledge_bank_agent.infrastructure.db.models.tapes import (
    CleaningIssueRow,
    RawRowRow,
    SubmissionItemRow,
    TapeSubmissionRow,
)

__all__ = [
    "AlertRow",
    "AuditLogRow",
    "Base",
    "CertificateRow",
    "CheckpointSeenRow",
    "CleaningIssueRow",
    "FacilityRow",
    "IntegrityRunRow",
    "LenderRow",
    "LoanRow",
    "LoanTokenRow",
    "LocalCheckRow",
    "LoginAttemptRow",
    "QuarantinedMessageRow",
    "RawRowRow",
    "RegistryRequestItemRow",
    "RegistryRequestRow",
    "SubmissionItemRow",
    "TapeSubmissionRow",
    "UserRow",
]
