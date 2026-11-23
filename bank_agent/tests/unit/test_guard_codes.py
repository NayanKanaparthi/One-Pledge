"""The nine guard codes are one list: the README table, `StoreRefusedError` and the migrations
(SPEC_DECISIONS 128).

`bank_agent/README.md` lists every SQLSTATE the bank's own triggers raise in one table under
"Guard codes"; the table's codes are exactly `store_errors.GUARD_CODES`, and exactly the codes
the migrations raise with `ERRCODE`. A guard added in a migration without its row, or a row for
a code no guard raises, fails here.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from onepledge_bank_agent.domain.store_errors import GUARD_CODES

pytestmark = pytest.mark.unit

PACKAGE = Path(__file__).resolve().parents[2]
HEADING = "## Guard codes"
ROW = re.compile(r"^\| `(OP\d{3})` \| (.+) \| (.+) \|$")


def readme_rows() -> dict[str, tuple[str, str]]:
    lines = (PACKAGE / "README.md").read_text(encoding="utf-8").splitlines()
    assert HEADING in lines, "the README has a Guard codes section"
    start = lines.index(HEADING)
    rows: dict[str, tuple[str, str]] = {}
    for line in lines[start + 1 :]:
        if line.startswith("## "):
            break
        found = ROW.match(line)
        if found:
            assert found.group(1) not in rows, found.group(1)
            rows[found.group(1)] = (found.group(2), found.group(3))
    return rows


def raised_by_migrations() -> set[str]:
    sources = (PACKAGE / "migrations" / "versions").glob("*.py")
    return {code for path in sources for code in re.findall(r"ERRCODE = '(OP\d{3})'", path.read_text())}


def test_the_readme_table_lists_exactly_the_nine_guard_codes_in_order() -> None:
    rows = readme_rows()
    assert list(rows) == [f"OP00{n}" for n in range(1, 10)]
    assert set(rows) == GUARD_CODES == raised_by_migrations()


def test_each_row_names_what_it_refuses_and_the_migration_that_raises_it() -> None:
    migrations = {path.name for path in (PACKAGE / "migrations" / "versions").glob("*.py")}
    for code, (refuses, where) in readme_rows().items():
        assert refuses.strip(), code
        named = set(re.findall(r"`(\d{4}_\w+\.py)`", where))
        assert named and named <= migrations, (code, where)
        assert any(
            f"ERRCODE = '{code}'" in (PACKAGE / "migrations/versions" / name).read_text() for name in named
        )
