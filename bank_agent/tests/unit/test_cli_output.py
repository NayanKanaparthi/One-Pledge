"""The command line output helper (SPEC 19): aligned tables, pairs, JSON, one sentence errors."""

from __future__ import annotations

import io
import json

import pytest

from onepledge_bank_agent.interface.cli.output import Output, short_hash

pytestmark = pytest.mark.unit


def streams() -> tuple[Output, io.StringIO, io.StringIO]:
    out, err = io.StringIO(), io.StringIO()
    return Output(out, err), out, err


def test_a_table_has_title_headers_aligned_rows_a_count_and_a_closing_line() -> None:
    output, out, err = streams()
    rows = [("lenders", "2"), ("facilities", "1,204")]
    output.table("Reference data of HARBOR", ("table", "rows"), rows, "tables seeded", "Done.")
    assert out.getvalue().splitlines() == [
        "Reference data of HARBOR",
        "  table       rows",
        "  lenders     2",
        "  facilities  1,204",
        "2 tables seeded",
        "Done.",
    ]
    assert err.getvalue() == ""


def test_no_line_ends_in_spaces_and_counts_use_thousands_separators() -> None:
    output, out, _ = streams()
    output.table("T", ("a", "b"), [("x", "")] * 1234, "rows", "End.")
    lines = out.getvalue().splitlines()
    assert all(line == line.rstrip() for line in lines)
    assert lines[-2] == "1,234 rows"


def test_pairs_align_their_names() -> None:
    output, out, _ = streams()
    output.pairs("Schema", {"database": "bank", "at latest": "yes"})
    assert out.getvalue() == "Schema\n  database   bank\n  at latest  yes\n"


def test_json_is_indented_and_ascii_and_errors_go_to_stderr() -> None:
    output, out, err = streams()
    output.json({"name": "café", "n": 1})
    output.error("PostgreSQL refused the connection at DATABASE_URL; run db provision first.")
    assert json.loads(out.getvalue()) == {"name": "café", "n": 1}
    assert "\\u00e9" in out.getvalue() and out.getvalue().startswith("{\n  ")
    assert err.getvalue().count("\n") == 1


def test_a_hash_is_shown_as_its_first_twelve_characters() -> None:
    assert short_hash("0123456789abcdef" * 4) == "0123456789ab..."
