"""`Runtime`: the time, identifier, fault and log collaborators every pipeline use case receives together."""

from __future__ import annotations

from dataclasses import dataclass

from onepledge_bank_agent.application.faults import FaultInjector
from onepledge_bank_agent.application.ports import Clock, IdGenerator, OperationalLog


@dataclass(frozen=True)
class Runtime:
    """Clock, id generator, fault injector and operational log, injected as one parameter object."""

    clock: Clock
    ids: IdGenerator
    faults: FaultInjector
    log: OperationalLog
