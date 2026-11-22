"""In-memory ports and scripted dependency fakes for unit tests of the use cases (SPEC_DECISIONS 63).

`stores`: the unit of work and every repository, with rollback and the audit chain rule.
`clocks`: the clock and the identifier generator. `outside_records`: the title, payments
and contracts clients. `registry`: the registry client. `key_holders`: both key holders
and the blinder. `signatures`: the signature verifier. `locks`: the advisory locks.
`bank`: all of them wired the way the composition root wires the real ones.

Nothing under `src/` imports this package and no product code path selects it.
"""

from bank_testkit.memory.clocks import CountingIds, ManualClock
from bank_testkit.memory.stores import (
    MemoryControls,
    MemoryState,
    MemoryStore,
    MemoryUnitOfWork,
    MemoryUnits,
)

__all__ = [
    "CountingIds",
    "ManualClock",
    "MemoryControls",
    "MemoryState",
    "MemoryStore",
    "MemoryUnitOfWork",
    "MemoryUnits",
]
