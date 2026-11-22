"""`RecordStartupConfiguration`: audit a deliberate weakening of a control at startup (SPEC 15.4).

When `HALT_ON_REGISTRY_INTEGRITY_FAILURE` is false the bank keeps sending to a registry
whose checkpoints failed verification. That is an operator's decision, so every process
start with the rule off writes `HALT_RULE_DISABLED` and a warning log line.
"""

from __future__ import annotations

from onepledge_bank_agent.application.events import LogEvent
from onepledge_bank_agent.application.ports import UnitOfWorkFactory
from onepledge_bank_agent.application.registry_audit import entry
from onepledge_bank_agent.application.runtime import Runtime
from onepledge_bank_agent.domain.enums import AuditAction, AuditObjectType

HALT_SETTING = "HALT_ON_REGISTRY_INTEGRITY_FAILURE"


class RecordStartupConfiguration:
    """Audits the halt rule being off."""

    def __init__(self, units: UnitOfWorkFactory, halt_enabled: bool, runtime: Runtime) -> None:
        self._units = units
        self._halt_enabled = halt_enabled
        self._log = runtime.log

    def run(self, process: str) -> bool:
        """Write `HALT_RULE_DISABLED` when the halt rule is off; True when an entry was written."""
        if self._halt_enabled:
            return False
        with self._units() as unit:
            unit.record(
                entry(
                    AuditAction.HALT_RULE_DISABLED,
                    AuditObjectType.CONFIG,
                    None,
                    {"setting": HALT_SETTING, "value": False, "process": process},
                )
            )
            unit.commit()
        self._log.warning(LogEvent.HALT_RULE_DISABLED, stage=process)
        return True
