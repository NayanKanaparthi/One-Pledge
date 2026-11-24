"""The models declare `COLLATE "C"` on every text-typed column, as migration 0016 sets it
(SPEC_DECISIONS 133.2), so the models and the migrated schema stay equal."""

from __future__ import annotations

import pytest
from sqlalchemy import ARRAY, String

from onepledge_bank_agent.infrastructure.db.models import Base

pytestmark = pytest.mark.unit


def test_every_text_typed_column_of_the_models_is_collate_c() -> None:
    found: dict[str, str | None] = {}
    for table in Base.metadata.tables.values():
        for column in table.columns:
            kind = column.type.item_type if isinstance(column.type, ARRAY) else column.type
            if isinstance(kind, String):  # Text and CHAR are String types
                found[f"{table.name}.{column.name}"] = kind.collation
    assert len(found) > 70
    assert sorted(name for name, collation in found.items() if collation != "C") == []
