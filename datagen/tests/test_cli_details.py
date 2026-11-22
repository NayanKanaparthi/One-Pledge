"""Command line details of onepledge-datagen (SPEC_DECISIONS 78)."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from onepledge_datagen.interface.cli import main
from onepledge_datagen.interface.output import WIDTH, Console, fit

USAGE = {
    "ok": False,
    "error": {"code": "USAGE", "message": "The command line is not valid. See the usage text."},
}


@pytest.mark.unit
@pytest.mark.parametrize(
    "argv",
    [
        ["generate", "--seed", "abc", "--profile", "small", "--out", "x", "--json"],
        ["verify", "--json"],
        ["--json", "nonsense"],
    ],
    ids=["seed not a number (review 3 finding 7)", "missing data dir", "unknown command"],
)
def test_a_usage_error_with_json_is_one_usage_document_on_stdout(
    argv: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    out = io.StringIO()
    with pytest.raises(SystemExit) as raised:
        main(argv, Console(out, io.StringIO()))
    assert raised.value.code == 2
    assert json.loads(out.getvalue()) == USAGE
    assert "usage:" in capsys.readouterr().err


@pytest.mark.unit
@pytest.mark.parametrize("command", ["generate", "verify"])
def test_debug_help_says_what_it_prints(command: str, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main([command, "--help"])
    text = " ".join(capsys.readouterr().out.split())
    assert "--debug print the exception type and call frames" in text
    assert "--wide do not wrap human readable output at 100 columns" in text


@pytest.mark.unit
def test_a_long_line_wraps_at_100_columns_keeping_its_indent_and_wide_keeps_it_whole() -> None:
    line = "  " + " ".join(["word"] * 40)
    wrapped = fit(line).split("\n")
    assert len(wrapped) > 1 and all(len(part) <= WIDTH for part in wrapped)
    assert all(part.startswith("    word") for part in wrapped[1:])
    assert fit(line, wide=True) == line


@pytest.mark.integration
def test_a_sentence_naming_a_long_path_wraps_unless_wide(tmp_path: Path) -> None:
    deep = str(tmp_path) + "/" + "directory-with-a-long-name/" * 3
    err = io.StringIO()
    assert main(["verify", "--data-dir", deep], Console(io.StringIO(), err)) == 1
    assert len(err.getvalue().splitlines()) > 1
    assert all(len(line) <= WIDTH or " " not in line.strip() for line in err.getvalue().splitlines())
    err = io.StringIO()
    assert main(["verify", "--wide", "--data-dir", deep], Console(io.StringIO(), err)) == 1
    assert len(err.getvalue().splitlines()) == 1
