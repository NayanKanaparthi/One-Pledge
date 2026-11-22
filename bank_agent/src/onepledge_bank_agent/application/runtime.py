"""`Runtime`: the clock, ids, faults, log and stop signal every pipeline use case receives together.

Shutdown (SPEC 13.1, SPEC_DECISIONS 51): every stage calls `after_unit()` once a unit is
committed (a check chunk, a tokenise chunk, a registry request with its stored response,
a certificate); the outbox sync checks the stop signal after each acknowledged message.
Once a stop is requested nothing further is committed: `StopRequestedError` unwinds the
stage, which is left where it is and resumes on the next start.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from onepledge_bank_agent.application.faults import FaultInjector
from onepledge_bank_agent.application.ports import Clock, IdGenerator, OperationalLog, StopSignal


class StopRequestedError(Exception):
    """A stop was requested; raised at a unit boundary so the stage commits nothing further."""


class NoStop:
    """A stop signal that is never set, for processes that are not asked to stop (one command)."""

    def is_set(self) -> bool:
        """Never set."""
        return False

    def wait(self, seconds: float) -> bool:
        """Never set, so it never waits for one: returns at once."""
        return False


@dataclass(frozen=True)
class Runtime:
    """Clock, id generator, fault injector, operational log and stop signal, injected as one object."""

    clock: Clock
    ids: IdGenerator
    faults: FaultInjector
    log: OperationalLog
    stop: StopSignal = field(default_factory=NoStop)

    def after_unit(self) -> None:
        """A unit was committed: raise `StopRequestedError` when a stop was requested."""
        if self.stop.is_set():
            raise StopRequestedError
