"""The guard manifest: every trigger the migrations install, as it is at head (SPEC_DECISIONS 90).

A trigger control is true only when, for every trigger it covers, the trigger exists on its
table, `tgenabled` is `O` or `A`, `tgtype` is the expected one (timing, level and the full
set of events: a trigger recreated for TRUNCATE only is not in force), it calls the expected
function in `public`, and the SHA-256 of that function's stored source (`pg_proc.prosrc`)
is the expected hash. So a guard replaced by a function that returns NULL, or by one that
lets every row through, turns its control false.

An integration test migrates a fresh database and asserts the installed triggers equal
this manifest, so it cannot drift from the migrations: a migration that changes a guard
changes the manifest in the same commit.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from sqlalchemy import Connection, text

ROW, BEFORE, INSERT, DELETE, UPDATE, TRUNCATE = 1, 2, 4, 8, 16, 32
"""The bits of `pg_trigger.tgtype` (PostgreSQL's `TRIGGER_TYPE_*`)."""
FORBID_MUTATION = BEFORE | DELETE | UPDATE | TRUNCATE
"""Statement level, before UPDATE, DELETE and TRUNCATE: the insert-only tables."""
ROW_BEFORE_INSERT = ROW | BEFORE | INSERT
ROW_BEFORE_UPDATE = ROW | BEFORE | UPDATE
ENABLED = frozenset({"O", "A"})
SCHEMA = "public"


@dataclass(frozen=True)
class Guard:
    """One installed trigger: its name, table, `tgtype`, function and the SHA-256 of the function's source."""

    trigger: str
    table: str
    tgtype: int
    function: str
    source_sha256: str


FORBID = ("onepledge_forbid_mutation", "a71754ac26b692a1e4f1a2b886691e5566d8222739add34782fcaddaf454f4f5")
INSERT_ONLY_TABLES = (
    "audit_log", "certificates", "checkpoints_seen", "cleaning_issues", "integrity_runs", "loan_tokens",
    "loans_raw", "local_checks", "registry_messages_quarantined", "submission_items",
)  # fmt: skip
GUARDS: tuple[Guard, ...] = (
    *(Guard(f"{table}_insert_only", table, FORBID_MUTATION, *FORBID) for table in INSERT_ONLY_TABLES),
    Guard("alerts_guard", "alerts", ROW_BEFORE_INSERT | UPDATE, "onepledge_alerts_guard",
          "c3563d5a045549e9661d828d4499bd6cb611a82616d8be109c95da52875ebf92"),
    Guard("audit_log_chain_guard", "audit_log", ROW_BEFORE_INSERT, "onepledge_audit_guard",
          "9511e72cfbae778738313a0a3b2bd660bd5a488be5af3f6402e9530fb9c9d808"),
    Guard("loans_guard", "loans", ROW_BEFORE_UPDATE, "onepledge_loans_guard",
          "d1c0b4746641dc56e049ca3f2ab265f46660f591ed5ef4084c7539f0a99c72e1"),
    Guard("loans_insert_guard", "loans", ROW_BEFORE_INSERT, "onepledge_loans_insert_guard",
          "e6c31d6c2093fa1ad06c1a0fdb56bf61c859c175fe730682fe4a6dd179b72dc1"),
    Guard("tape_submissions_guard", "tape_submissions", ROW_BEFORE_UPDATE, "onepledge_tape_guard",
          "ca2af4ae85f74f870b0dbfa88aba503c754e40d68f34b834760c4260f4fee649"),
    Guard("tape_submissions_insert_guard", "tape_submissions", ROW_BEFORE_INSERT,
          "onepledge_tape_insert_guard", "b3f1f2b515269457e4ec1b15f35738e86a04255d7d966a897fef530a71f07f03"),
)  # fmt: skip
MANIFEST: Mapping[tuple[str, str], Guard] = MappingProxyType({(g.table, g.trigger): g for g in GUARDS})
"""Each guard by (table, trigger name): a trigger name is unique only within its table."""

TRIGGERS = text(
    "SELECT t.tgname, c.relname, t.tgtype, t.tgenabled, n.nspname, p.proname, p.prosrc "
    "FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid JOIN pg_proc p ON p.oid = t.tgfoid "
    "JOIN pg_namespace n ON n.oid = p.pronamespace "
    "WHERE NOT t.tgisinternal AND c.relnamespace = 'public'::regnamespace"
)


@dataclass(frozen=True)
class InstalledTrigger:
    """A trigger as the catalogue holds it now."""

    guard: Guard
    enabled: str
    function_schema: str


def installed_triggers(connection: Connection) -> dict[tuple[str, str], InstalledTrigger]:
    """Every user trigger on a table of `public`, by (table, trigger name)."""
    found = {}
    for name, table, tgtype, enabled, schema, function, source in connection.execute(TRIGGERS):
        digest = hashlib.sha256(str(source).encode("utf-8")).hexdigest()
        guard = Guard(str(name), str(table), int(tgtype), str(function), digest)
        found[(guard.table, guard.trigger)] = InstalledTrigger(guard, str(enabled), str(schema))
    return found


def in_force(installed: Mapping[tuple[str, str], InstalledTrigger], guards: Iterable[Guard]) -> bool:
    """True when every guard is installed exactly as the manifest says and enabled."""
    for guard in guards:
        found = installed.get((guard.table, guard.trigger))
        if (
            found is None
            or (found.guard, found.function_schema) != (guard, SCHEMA)
            or found.enabled not in ENABLED
        ):
            return False
    return True


def guards_named(*triggers: str) -> tuple[Guard, ...]:
    """The manifest entries of these trigger names; a name the manifest lacks is a programming error."""
    found = tuple(g for g in GUARDS if g.trigger in triggers)
    if {g.trigger for g in found} != set(triggers):
        raise KeyError(sorted(set(triggers) - {g.trigger for g in found}))
    return found
