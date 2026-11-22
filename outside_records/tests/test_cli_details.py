"""Command line details of onepledge-outside (SPEC_DECISIONS 78)."""

from __future__ import annotations

import io
import json

import pytest

from onepledge_outside_records.domain.errors import OperatorError
from onepledge_outside_records.infrastructure.settings import ServeSettings
from onepledge_outside_records.interface import cli
from onepledge_outside_records.interface.output import WIDTH, Console, fit

USAGE = {
    "ok": False,
    "error": {"code": "USAGE", "message": "The command line is not valid. See the usage text."},
}


@pytest.mark.unit
@pytest.mark.parametrize(
    "argv",
    [["seed", "--data-dir", "", "--json"], ["serve", "--json"], ["db", "--json"], ["--json", "nonsense"]],
    ids=["empty data dir", "serve refuses json", "db without subcommand", "unknown command"],
)
def test_a_usage_error_with_json_is_one_usage_document_on_stdout(
    argv: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    out = io.StringIO()
    with pytest.raises(SystemExit) as raised:
        cli.main(argv, Console(out, io.StringIO()))
    assert raised.value.code == 2
    assert json.loads(out.getvalue()) == USAGE
    assert "usage:" in capsys.readouterr().err


@pytest.mark.unit
@pytest.mark.parametrize("command", [["db", "migrate"], ["seed"], ["clients", "add"], ["serve"]])
def test_debug_help_says_what_it_prints(command: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        cli.main([*command, "--help"])
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


@pytest.mark.unit
def test_the_last_resort_writes_one_error_record_then_the_sentence(monkeypatch: pytest.MonkeyPatch) -> None:
    def explode(*_: object) -> ServeSettings:
        raise RuntimeError("boom")

    monkeypatch.setattr(cli, "load_serve_settings", explode)
    out, err = io.StringIO(), io.StringIO()
    assert cli.main(["serve"], Console(out, err)) == 1
    record = json.loads(out.getvalue())
    assert (record["level"], record["event"], record["exception_type"], record["service"]) == (
        "error",
        "unhandled_exception",
        "RuntimeError",
        "outside_records",
    )
    assert (
        "boom" not in out.getvalue() and err.getvalue() == "Unexpected error. Run with --debug for details.\n"
    )


# The settings every service shares have one sentence each, word for word the doubles' sentences.
SHARED = {
    ("PORT", "+80"): "Configuration error: PORT must be a whole number written with the digits 0 to 9.",
    ("PORT", "70000"): "Configuration error: PORT is out of range (1 to 65,535).",
    ("LOG_LEVEL", "LOUD"): "Configuration error: LOG_LEVEL must be DEBUG, INFO, WARNING, ERROR or CRITICAL.",
    ("BIND_HOST", "localhost"): (
        "Configuration error: BIND_HOST must be an IPv4 or IPv6 address; set it to 127.0.0.1 or 0.0.0.0."
    ),
}


@pytest.mark.unit
@pytest.mark.parametrize(
    ("setting", "sentence"), list(SHARED.items()), ids=[k[0] + " " + k[1] for k in SHARED]
)
def test_a_shared_setting_has_the_sentence_of_every_service(setting: tuple[str, str], sentence: str) -> None:
    name, value = setting
    with pytest.raises(OperatorError) as raised:
        ServeSettings.from_env({name: value})
    assert raised.value.sentence == sentence
    assert not sentence.endswith(":") and len(sentence) <= WIDTH
