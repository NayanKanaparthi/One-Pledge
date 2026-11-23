"""Command line details of onepledge-datagen (SPEC_DECISIONS 78)."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from onepledge_datagen.interface.cli import main
from onepledge_datagen.interface.output import WIDTH, Console, fit
from onepledge_testtools.command_lines import Option, malformed, usage_problems, wants_json

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
def test_a_sentence_naming_a_long_path_wraps_even_with_wide(tmp_path: Path) -> None:
    """SPEC_DECISIONS 121.1: `--wide` lifts the width for results only, never for an error."""
    deep = str(tmp_path) + "/" + "directory-with-a-long-name/" * 3
    errors = []
    for wide in ([], ["--wide"]):
        err = io.StringIO()
        assert main(["verify", *wide, "--data-dir", deep], Console(io.StringIO(), err)) == 1
        errors.append(err.getvalue())
    assert errors[0] == errors[1]
    assert len(errors[0].splitlines()) > 1
    assert all(len(line) <= WIDTH or " " not in line.strip() for line in errors[0].splitlines())


# SPEC_DECISIONS 121: every command with about a dozen malformed command lines.
COMMAND_LINES = {
    "generate": (
        ["generate"],
        [
            Option("--seed", "1", checked=True),
            Option("--profile", "small", checked=True),
            Option("--out", "x"),
        ],
    ),
    "verify": (["verify"], [Option("--data-dir", "x")]),
}
MALFORMED = [
    (name, variant, argv)
    for name, (command, options) in COMMAND_LINES.items()
    for variant, argv in malformed(command, options).items()
]


@pytest.mark.unit
@pytest.mark.parametrize(("name", "variant", "argv"), MALFORMED, ids=[f"{n}: {v}" for n, v, _ in MALFORMED])
@pytest.mark.parametrize("json_form", [False, True], ids=["text", "json"])
def test_every_malformed_command_line_is_a_plain_usage_error(
    name: str, variant: str, argv: list[str], json_form: bool, capsys: pytest.CaptureFixture[str]
) -> None:
    line = [*argv, "--json"] if json_form and not wants_json(argv) else argv
    out = io.StringIO()
    with pytest.raises(SystemExit) as raised:
        main(line, Console(out, io.StringIO()))
    assert usage_problems(line, raised.value.code, out.getvalue(), capsys.readouterr().err) == []


@pytest.mark.unit
def test_an_invalid_choice_is_the_products_sentence(capsys: pytest.CaptureFixture[str]) -> None:
    """Review 4 failure 6: `--profile nope` printed argparse's repr at 118 columns."""
    with pytest.raises(SystemExit):
        main(["generate", "--seed", "1", "--profile", "nope", "--out", "x"])
    assert capsys.readouterr().err.splitlines()[-1] == (
        "onepledge-datagen generate: argument --profile must be one of: full, small, example."
    )


@pytest.mark.unit
@pytest.mark.parametrize("argv", [[], ["generate"], ["verify"]])
def test_help_is_at_most_100_columns_whatever_the_terminal(
    argv: list[str], capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COLUMNS", "400")
    with pytest.raises(SystemExit):
        main([*argv, "--help"])
    assert max(len(line) for line in capsys.readouterr().out.splitlines()) <= WIDTH
