"""`in_force` (SPEC_DECISIONS 90): a guard holds only when every installed fact equals the manifest."""

from __future__ import annotations

from dataclasses import replace

import pytest

from onepledge_bank_agent.infrastructure.db.guard_manifest import (
    FORBID_MUTATION,
    GUARDS,
    MANIFEST,
    TRUNCATE,
    Guard,
    InstalledTrigger,
    guards_named,
    in_force,
)

pytestmark = pytest.mark.unit

GUARD = MANIFEST[("loans", "loans_guard")]


def installed(
    guard: Guard, enabled: str = "O", schema: str = "public"
) -> dict[tuple[str, str], InstalledTrigger]:
    return {(GUARD.table, GUARD.trigger): InstalledTrigger(guard, enabled, schema)}


def test_the_guard_as_shipped_and_enabled_on_origin_or_always_is_in_force() -> None:
    assert in_force(installed(GUARD), [GUARD])
    assert in_force(installed(GUARD, "A"), [GUARD])


@pytest.mark.parametrize(
    "changed",
    [
        replace(GUARD, tgtype=TRUNCATE),
        replace(GUARD, function="onepledge_other"),
        replace(GUARD, source_sha256="0" * 64),
        replace(GUARD, table="alerts"),
        replace(GUARD, trigger="loans_guard_2"),
    ],
    ids=["tgtype", "function", "source", "table", "name"],
)
def test_any_fact_that_differs_is_not_in_force(changed: Guard) -> None:
    assert not in_force(installed(changed), [GUARD])


@pytest.mark.parametrize("enabled", ["D", "R"])
def test_a_disabled_or_replica_only_trigger_is_not_in_force(enabled: str) -> None:
    assert not in_force(installed(GUARD, enabled), [GUARD])


def test_a_function_of_another_schema_or_a_missing_trigger_is_not_in_force() -> None:
    assert not in_force(installed(GUARD, schema="shadow"), [GUARD])
    assert not in_force({}, [GUARD])


def test_every_insert_only_trigger_fires_before_update_delete_and_truncate_per_statement() -> None:
    insert_only = [g for g in GUARDS if g.trigger.endswith("_insert_only")]
    assert len(insert_only) == 10
    assert {g.tgtype for g in insert_only} == {FORBID_MUTATION}
    assert FORBID_MUTATION == 58


def test_names_the_manifest_lacks_are_refused() -> None:
    assert guards_named("loans_guard") == (GUARD,)
    with pytest.raises(KeyError):
        guards_named("loans_guard", "no_such_trigger")
