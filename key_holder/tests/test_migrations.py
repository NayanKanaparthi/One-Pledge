"""The Alembic layout: `alembic.ini` finds the packaged migrations, which hold no revision before WP2."""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

pytestmark = pytest.mark.unit

PACKAGE = Path(__file__).resolve().parents[1]


def test_alembic_ini_points_at_the_migrations_beside_src() -> None:
    script = ScriptDirectory.from_config(Config(str(PACKAGE / "alembic.ini")))
    assert Path(script.dir).resolve() == (PACKAGE / "migrations").resolve()
    assert (Path(script.dir) / "env.py").is_file()
    assert script.get_heads() == []
