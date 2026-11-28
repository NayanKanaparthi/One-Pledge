"""The Alembic layout: `alembic.ini` finds the packaged migrations, whose head is the WP2 grants revision."""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from onepledge_key_holder.infrastructure.db.migrations import installed_head

pytestmark = pytest.mark.unit

PACKAGE = Path(__file__).resolve().parents[1]


def test_alembic_ini_points_at_the_migrations_beside_src() -> None:
    script = ScriptDirectory.from_config(Config(str(PACKAGE / "alembic.ini")))
    assert Path(script.dir).resolve() == (PACKAGE / "migrations").resolve()
    assert (Path(script.dir) / "env.py").is_file()
    assert script.get_heads() == ["0002_grants"]


def test_the_revisions_are_one_line_from_the_schema_to_the_grants() -> None:
    script = ScriptDirectory.from_config(Config(str(PACKAGE / "alembic.ini")))
    assert [revision.revision for revision in script.walk_revisions()] == ["0002_grants", "0001_schema"]
    assert installed_head() == "0002_grants"
