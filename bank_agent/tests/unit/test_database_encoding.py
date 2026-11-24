"""`db provision` creates the bank database in UTF8 from `template0` (SPEC_DECISIONS 133.1).

Under UTF8 the collation "C" orders by code point, the order Python uses, so the encoding is part
of what makes a digest independent of where a database was created; `template0` keeps whatever
`template1` holds out of it. The statement is checked as rendered; the integration tests check
the database it makes and the startup check that refuses any other encoding.
"""

from __future__ import annotations

import pytest

from onepledge_bank_agent.infrastructure.db.engines import DatabaseTarget
from onepledge_bank_agent.infrastructure.db.provisioning import create_database_statement
from onepledge_bank_agent.infrastructure.db.schema_manifest import expected
from onepledge_bank_agent.interface.cli.startup import CHECK_NAMES

pytestmark = pytest.mark.unit


def test_the_database_is_created_in_utf8_from_template0() -> None:
    target = DatabaseTarget("opdev_bank_harbor", "opdev_bank_harbor_owner", "p", "opdev_bank_harbor_app", "q")
    assert create_database_statement(target).as_string(None) == (
        'CREATE DATABASE "opdev_bank_harbor" OWNER "opdev_bank_harbor_owner" ENCODING \'UTF8\' '
        "TEMPLATE template0"
    )


def test_the_manifest_records_the_encoding() -> None:
    assert expected()["database encoding"] == "UTF8"


def test_the_encoding_is_checked_right_after_the_database_is_reached() -> None:
    assert CHECK_NAMES[:3] == ("settings", "database reachable", "database encoding")
