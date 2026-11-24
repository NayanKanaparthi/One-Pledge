"""Every text-typed column is `COLLATE "C"` (SPEC_DECISIONS 133.2).

The catalogue of a database migrated to head is read: every column of the bank's tables whose
type is collatable (`text`, `char(n)`, `varchar`, `text[]`, `alembic_version.version_num`
included) must carry the collation `"C"`, so comparisons, unique keys, check constraints with
patterns and orderings on it are by code point whatever the database default is. A column added
later without it fails here.
"""

from __future__ import annotations

import pytest

from bank_testkit.database import BankDatabase
from onepledge_bank_agent.domain.integrity import ControlViolation, DatabaseControl
from onepledge_bank_agent.infrastructure.db.controls import SqlDatabaseControlInspector
from onepledge_bank_agent.infrastructure.db.engines import make_engine
from onepledge_bank_agent.infrastructure.db.migrations import head_revision

pytestmark = pytest.mark.integration

COLLATIONS = """
SELECT c.relname || '.' || a.attname, format_type(a.atttypid, a.atttypmod), l.collname
FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid JOIN pg_collation l ON l.oid = a.attcollation
WHERE c.relnamespace = 'public'::regnamespace AND c.relkind IN ('r', 'p') AND a.attnum > 0
    AND NOT a.attisdropped AND a.attcollation <> 0
"""


def text_columns(database: BankDatabase) -> dict[str, tuple[str, str]]:
    with database.connect("admin") as connection:
        rows = connection.execute(COLLATIONS).fetchall()
    return {str(name): (str(kind), str(collation)) for name, kind, collation in rows}


def test_every_text_typed_column_is_collate_c(bank_db: BankDatabase) -> None:
    found = text_columns(bank_db)
    assert len(found) > 80 and "alembic_version.version_num" in found
    assert {kind.split("(")[0] for kind, _ in found.values()} >= {"text", "character", "text[]"}
    assert sorted(name for name, (_, collation) in found.items() if collation != "C") == []


def test_a_column_moved_to_a_case_insensitive_collation_is_named_by_the_control(
    bank_db: BankDatabase,
) -> None:
    """SPEC_DECISIONS 133 and 134 (AUD-03): a non-deterministic ICU collation, as a superuser can put on a
    column, would make strings that differ in case equal; the manifest control names the column."""
    with bank_db.connect("admin") as connection:
        connection.execute(
            "CREATE COLLATION optest_ci (provider = icu, locale = 'und-u-ks-level2', deterministic = false)"
        )
        connection.execute("ALTER TABLE users ALTER COLUMN display_name TYPE text COLLATE optest_ci")
    assert text_columns(bank_db)["users.display_name"] == ("text", "optest_ci")
    engine = make_engine(bank_db.app_url, inspecting=True)
    try:
        inspector = SqlDatabaseControlInspector(engine, head_revision())
        holds = inspector.controls()[DatabaseControl.SCHEMA_MATCHES_MANIFEST]
        violations = inspector.violations()
    finally:
        engine.dispose()
    manifest = DatabaseControl.SCHEMA_MATCHES_MANIFEST
    assert not holds
    assert ControlViolation(manifest, "SCHEMA_OBJECT_CHANGED", "column users.display_name") in violations
