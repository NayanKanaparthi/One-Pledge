"""The in-memory stores of the registry double, grouped so they travel together (TEST DOUBLE)."""

from __future__ import annotations

from dataclasses import dataclass, field

from onepledge_contract_fakes.registry.cases import CaseBook
from onepledge_contract_fakes.registry.certificates import CertificateIssuer
from onepledge_contract_fakes.registry.checkpoints import CheckpointBook
from onepledge_contract_fakes.registry.ledger import Ledger
from onepledge_contract_fakes.registry.locks import LockTable
from onepledge_contract_fakes.registry.outbox import Outbox
from onepledge_contract_fakes.registry.signing import RegistrySigner


@dataclass(frozen=True)
class RegistryStores:
    """Ledger, locks, cases, certificates, checkpoints and outbox of one double."""

    signer: RegistrySigner
    ledger: Ledger = field(default_factory=Ledger)
    locks: LockTable = field(default_factory=LockTable)
    cases: CaseBook = field(default_factory=CaseBook)
    outbox: Outbox = field(default_factory=Outbox)
    checkpoints: CheckpointBook = field(init=False)
    certificates: CertificateIssuer = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "checkpoints", CheckpointBook(self.signer))
        object.__setattr__(self, "certificates", CertificateIssuer(self.signer))
