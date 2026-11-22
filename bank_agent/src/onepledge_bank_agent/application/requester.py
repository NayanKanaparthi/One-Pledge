"""`Requester`: who asked for an action, through which request, and from where (SPEC 17.1, 19).

A use case a person triggers records its audit entry with this actor and request id.
A command line invocation acts as the system actor with `via` set to `cli`, which the
entry's detail carries.
"""

from __future__ import annotations

from dataclasses import dataclass

from onepledge_bank_agent.domain.audit import Actor
from onepledge_bank_agent.domain.canonical_json import JsonValue

VIA_CLI = "cli"


@dataclass(frozen=True)
class Requester:
    """The actor of a requested action, the request id of the call and, for the command line, `via`."""

    actor: Actor
    request_id: str | None = None
    via: str | None = None

    @classmethod
    def command_line(cls) -> Requester:
        """The command line: the system actor, `via` cli."""
        return cls(Actor.system(), None, VIA_CLI)

    def detail(self) -> dict[str, JsonValue]:
        """`{"via": "cli"}` for the command line, empty otherwise."""
        return {"via": self.via} if self.via is not None else {}
