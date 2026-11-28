"""`ReportHealth`: who this holder is and which key version it evaluates with (`GET /healthz`)."""

from __future__ import annotations

from dataclasses import dataclass

from onepledge_key_holder.domain.identity import HolderId, KeyVersion


@dataclass(frozen=True)
class HealthReport:
    """The facts `/healthz` states about this process."""

    holder_id: HolderId
    active_key_version: KeyVersion


class ReportHealth:
    """Reports the identity and the active key version the process was configured with."""

    def __init__(self, holder_id: HolderId, active_key_version: KeyVersion) -> None:
        self._report = HealthReport(holder_id, active_key_version)

    def run(self) -> HealthReport:
        """The health facts of this process."""
        return self._report
