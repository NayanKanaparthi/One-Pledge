"""Command line details of `onepledge-bank` (SPEC_DECISIONS 78; review 2, quality findings 7 and 12).

A usage error with `--json` prints one USAGE document on stdout and argparse's usage text
on stderr, exit 2, and no option can be abbreviated; `--debug` says what it prints; lines
are at most 100 columns, a table's last column and a pair's value wrapping under
themselves, and `--wide` lifts the limit; fixed sentences end with a full stop.
"""

from __future__ import annotations

import io
import json
import uuid

import pytest

from onepledge_bank_agent.application.advance_submission import StageOutcome, StageRun
from onepledge_bank_agent.application.audit_entries import FailureDetail
from onepledge_bank_agent.application.run_pipeline_worker import WorkerReport
from onepledge_bank_agent.domain.enums import TapeStatus
from onepledge_bank_agent.domain.errors import MESSAGES
from onepledge_bank_agent.infrastructure.db import failures
from onepledge_bank_agent.infrastructure.db.role_check import RoleViolation, Violation
from onepledge_bank_agent.infrastructure.db.role_guard import refusal_sentence
from onepledge_bank_agent.interface.cli.commands import TASK_FAILED, worker_report
from onepledge_bank_agent.interface.cli.main import UNEXPECTED, main
from onepledge_bank_agent.interface.cli.output import USAGE_MESSAGE, WIDTH, Output, fit

pytestmark = pytest.mark.unit

USAGE = {
    "ok": False,
    "error": {"code": "USAGE", "message": "The command line is not valid. See the usage text."},
}


@pytest.mark.parametrize(
    "argv",
    [["db", "--json"], ["seed-reference", "--json"], ["--json", "nonsense"], ["worker", "--json"],
     ["integrity", "verify", "--json", "--twice"]],
    ids=["db without action", "seed without dir", "unknown command", "worker json, no once", "extra flag"],
)  # fmt: skip
def test_a_usage_error_with_json_is_one_usage_document_on_stdout(
    argv: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    out = io.StringIO()
    with pytest.raises(SystemExit) as raised:
        main(argv, Output(out, io.StringIO()))
    assert raised.value.code == 2
    assert json.loads(out.getvalue()) == USAGE
    assert "usage:" in capsys.readouterr().err


def test_a_usage_error_without_json_prints_nothing_on_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    out = io.StringIO()
    with pytest.raises(SystemExit) as raised:
        main(["db"], Output(out, io.StringIO()))
    assert (raised.value.code, out.getvalue()) == (2, "")
    assert "usage:" in capsys.readouterr().err


@pytest.mark.parametrize(
    "command",
    [["db", "migrate"], ["seed-reference"], ["worker"], ["registry", "sync"], ["integrity", "report"]],
)
def test_debug_and_wide_help_say_what_they_do(command: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as raised:
        main([*command, "--help"])
    assert raised.value.code == 0
    text = " ".join(capsys.readouterr().out.split())
    assert "--debug print the exception type and call frames" in text
    assert "--wide do not wrap human readable output at 100 columns" in text


def test_a_long_line_wraps_at_100_columns_keeping_its_indent_and_never_cuts_a_word() -> None:
    identifier = "f" * 36
    line = "  " + " ".join(["word"] * 25 + [identifier] + ["word"] * 5)
    wrapped = fit(line).split("\n")
    assert len(wrapped) > 1 and all(len(part) <= WIDTH for part in wrapped)
    assert all(part.startswith("    ") for part in wrapped[1:])
    assert any(identifier in part for part in wrapped), "an identifier is never cut"
    assert fit(line, wide=True) == line


def test_the_output_wraps_its_lines_and_sentences_unless_wide() -> None:
    long = "x " * 70
    narrow_out, narrow_err = io.StringIO(), io.StringIO()
    narrow = Output(narrow_out, narrow_err)
    narrow.line(long)
    narrow.error(long)
    assert all(len(line) <= WIDTH for line in (narrow_out.getvalue() + narrow_err.getvalue()).splitlines())
    wide_out = io.StringIO()
    Output(wide_out, io.StringIO(), wide=True).line(long)
    assert wide_out.getvalue() == long.rstrip() + "\n"


@pytest.mark.parametrize("argv", [["worker", "--once", "--js"], ["db", "status", "--wid"]])
def test_an_abbreviated_option_is_a_usage_error_not_the_option(
    argv: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    """No abbreviation: `--js` is not `--json`, so it can never ask for a JSON result by accident."""
    out = io.StringIO()
    with pytest.raises(SystemExit) as raised:
        main(argv, Output(out, io.StringIO()))
    assert (raised.value.code, out.getvalue()) == (2, "")
    assert "unrecognized arguments" in capsys.readouterr().err


def test_the_last_column_of_a_long_row_wraps_under_itself_and_the_other_columns_stay_aligned() -> None:
    out = io.StringIO()
    facts = " ".join(["fact"] * 30)
    rows = [("registry_chain", "OK", "short"), ("certificates", "BROKEN", facts)]
    Output(out, io.StringIO()).table("Sections", ("section", "status", "facts"), rows, ("s", "ss"), "")
    lines = out.getvalue().splitlines()
    assert all(len(line) <= WIDTH for line in lines)
    column = lines[1].index("facts")
    assert lines[2][column:] == "short" and lines[3][:column].split() == ["certificates", "BROKEN"]
    continuation = lines[4 : lines.index("2 ss")]
    assert continuation and all(line[:column].strip() == "" and line[column] != " " for line in continuation)
    assert " ".join(part[column:] for part in [lines[3], *continuation]) == facts


def test_a_pair_value_wraps_under_the_value_column_and_wide_keeps_one_line() -> None:
    value = " ".join(["word"] * 40)
    narrow, wide = io.StringIO(), io.StringIO()
    Output(narrow, io.StringIO()).pairs("Title", {"name": value})
    Output(wide, io.StringIO(), wide=True).pairs("Title", {"name": value})
    lines = narrow.getvalue().splitlines()
    assert len(lines) > 2 and all(len(line) <= WIDTH for line in lines)
    assert all(line.startswith(" " * len("  name  ")) for line in lines[2:])
    assert wide.getvalue().splitlines() == ["Title", f"  name  {value}"]


def test_a_long_identifier_is_shortened_like_a_hash_unless_wide() -> None:
    identifier = "0f8fad5b-d9cb-469f-a165-70867728950e"
    assert Output(io.StringIO(), io.StringIO()).identifier(identifier) == "0f8fad5b-d9c..."
    assert Output(io.StringIO(), io.StringIO(), wide=True).identifier(identifier) == identifier
    assert Output(io.StringIO(), io.StringIO()).identifier("HARBOR") == "HARBOR"


def test_no_fixed_sentence_ends_in_a_colon_and_every_one_ends_with_a_full_stop() -> None:
    """SPEC_DECISIONS 78: every fixed message of the command line and of the error table."""
    sentences = [*MESSAGES.values(), UNEXPECTED, TASK_FAILED, USAGE_MESSAGE]
    sentences += [
        failures.connection_failed(None, "DATABASE_URL").sentence,
        failures.connection_failed(RuntimeError("FATAL: no role"), "DATABASE_URL").sentence,
        failures.tables_missing().sentence,
        failures.privilege_missing("DATABASE_URL").sentence,
        failures.schema_mismatch().sentence,
        failures.migration_failed("0001_schema").sentence,
        failures.not_a_superuser().sentence,
    ]
    assert len({*sentences}) == len(sentences), "different causes have different sentences"
    for sentence in sentences:
        assert sentence.endswith(".") and not sentence.rstrip(".").endswith(":"), sentence
        assert sentence[0].isupper(), sentence


def test_the_worker_table_fits_names_the_six_failure_facts_and_shows_full_ids_with_wide() -> None:
    """Review 2, quality finding 12: the worker table was over 100 columns; it shows the facts of
    SPEC_DECISIONS 58 by the names of its JSON form, and the full submission id with `--wide`."""
    submission = uuid.UUID("11aa6b06-19aa-43ad-97e1-c07539c2ede5")
    failure: FailureDetail = {
        "stage": "CHECK", "failure_code": "OUTSIDE_RECORDS_UNAVAILABLE", "attempts": 3,
        "dependency": "outside_records", "http_status": 503, "reason": "SERVER_ERROR",
    }  # fmt: skip
    runs = [
        StageRun(submission, TapeStatus.RECEIVED, StageOutcome.ADVANCED, TapeStatus.CLEANED),
        StageRun(submission, TapeStatus.CLEANED, StageOutcome.RETRY_SCHEDULED, TapeStatus.CLEANED, failure),
    ]
    narrow, wide = io.StringIO(), io.StringIO()
    assert worker_report(WorkerReport(runs, []), Output(narrow, io.StringIO())) == 0
    worker_report(WorkerReport(runs, []), Output(wide, io.StringIO(), wide=True))
    lines = narrow.getvalue().splitlines()
    assert all(len(line) <= WIDTH for line in lines)
    assert lines[1].split() == ["submission", "from", "outcome", "status", "failure"]
    assert lines[2].split() == ["11aa6b06-19a...", "RECEIVED", "ADVANCED", "CLEANED"]
    text = " ".join(narrow.getvalue().split())
    assert ", ".join(f"{key} {value}" for key, value in failure.items()) in text
    assert str(submission) in wide.getvalue() and str(submission) not in narrow.getvalue()


def test_the_least_privilege_refusal_is_one_line_for_every_violation_code() -> None:
    """SPEC_DECISIONS 72: "refuse with one line naming DATABASE_URL and the first violation code"."""
    for code in Violation:
        sentence = refusal_sentence(RoleViolation(code, "public.audit_log"))
        assert len(sentence) <= WIDTH and fit(sentence) == sentence, code
        assert "DATABASE_URL" in sentence and code.value in sentence and "public.audit_log" not in sentence
