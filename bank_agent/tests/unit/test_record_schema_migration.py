"""`RecordSchemaMigration` (SPEC_DECISIONS 30): one `SCHEMA_MIGRATED` entry when the revision changed."""

from __future__ import annotations

import pytest

from bank_testkit.world import World
from onepledge_bank_agent.application.record_schema_migration import RecordSchemaMigration
from onepledge_bank_agent.domain.audit import Actor
from onepledge_bank_agent.domain.enums import AuditAction, AuditObjectType, AuditOutcome

pytestmark = pytest.mark.unit


def test_an_upgrade_of_a_new_database_records_from_none_to_the_head() -> None:
    world = World()
    assert RecordSchemaMigration(world.units, "cli").run(None, "0003_grants")
    (entry,) = world.audit(AuditAction.SCHEMA_MIGRATED)
    assert (entry.actor, entry.object_type, entry.object_id, entry.outcome) == (
        Actor.system(),
        AuditObjectType.SCHEMA,
        None,
        AuditOutcome.SUCCESS,
    )
    assert dict(entry.detail) == {"via": "cli", "from_revision": None, "to_revision": "0003_grants"}


def test_an_upgrade_between_revisions_names_both_and_a_no_op_records_nothing() -> None:
    world = World()
    record = RecordSchemaMigration(world.units, "cli")
    assert record.run("0002_guards", "0003_grants")
    assert not record.run("0003_grants", "0003_grants")
    (entry,) = world.audit()
    assert entry.detail["from_revision"] == "0002_guards"
