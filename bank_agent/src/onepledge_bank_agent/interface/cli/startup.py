"""Runtime commands start through a fixed sequence of named checks (SPEC_DECISIONS 104).

`settings` (the runtime settings, read before the composition is built), `database reachable`,
`session parameters`, `schema at head` and `role inspection`; `integrity report` checks only
that the tables exist instead of the last two, so it can show a schema behind the code or a
role that is not least-privilege as false controls. Each check has its own failure sentence. A
statement the database refuses inside a check is caught at the check's boundary
(`probe.startup_check`) and reported as that check's failure with its SQLSTATE, so an ordinary
condition of the environment never reaches the last resort handler. (The bind probe is the
serving command's, which the bank agent gets with its API.)
"""

from __future__ import annotations

from onepledge_bank_agent.infrastructure.db.probe import startup_check
from onepledge_bank_agent.interface.composition import RuntimeComposition

SETTINGS, REACHABLE, SESSION, SCHEMA, ROLE = (
    "settings", "database reachable", "session parameters", "schema at head", "role inspection",
)  # fmt: skip
CHECK_NAMES = (SETTINGS, REACHABLE, SESSION, SCHEMA, ROLE)


def start_runtime(composition: RuntimeComposition, *, guarded: bool = True) -> None:
    """The database checks in order; without `guarded` only the tables are required (integrity report)."""
    probe = composition.probe()
    startup_check(REACHABLE, probe.ensure_reachable)
    startup_check(SESSION, probe.ensure_session_parameters)
    if not guarded:
        startup_check(SCHEMA, composition.schema().require_tables)
        return
    startup_check(SCHEMA, composition.schema().require_head)
    startup_check(ROLE, probe.ensure_least_privilege)
