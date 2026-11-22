"""Command line details of the doubles and the contract checker (SPEC_DECISIONS 78)."""

from __future__ import annotations

import io
import json
from collections.abc import Callable, Sequence

import pytest

from onepledge_contract_fakes.cli_output import WIDTH, CliOutput, fit
from onepledge_contract_fakes.contracts_check import cli as contracts_cli
from onepledge_contract_fakes.errors import ConfigurationError
from onepledge_contract_fakes.key_holder import cli as key_holder_cli
from onepledge_contract_fakes.key_holder.settings import KeyHolderSettings
from onepledge_contract_fakes.registry import cli as registry_cli
from onepledge_contract_fakes.settings import EnvironmentReader

USAGE = {
    "ok": False,
    "error": {"code": "USAGE", "message": "The command line is not valid. See the usage text."},
}
Main = Callable[[Sequence[str] | None, CliOutput | None], int]
MAINS: dict[str, Main] = {
    "contracts": contracts_cli.main,
    "key_holder": key_holder_cli.main,
    "registry": registry_cli.main,
}


@pytest.mark.unit
@pytest.mark.parametrize(
    ("tool", "argv"),
    [
        ("contracts", ["check", "--json", "--no-such-option"]),
        ("contracts", ["--json"]),
        ("key_holder", ["serve", "--json"]),
        ("registry", ["serve", "--json", "extra"]),
    ],
)
def test_a_usage_error_with_json_is_one_usage_document_on_stdout(
    tool: str, argv: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    out = io.StringIO()
    with pytest.raises(SystemExit) as raised:
        MAINS[tool](argv, CliOutput(out, io.StringIO()))
    assert raised.value.code == 2
    assert json.loads(out.getvalue()) == USAGE
    assert "usage:" in capsys.readouterr().err


@pytest.mark.unit
def test_a_usage_error_without_json_prints_nothing_on_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    out = io.StringIO()
    with pytest.raises(SystemExit):
        contracts_cli.main(["check", "--no-such-option"], CliOutput(out, io.StringIO()))
    assert out.getvalue() == ""


@pytest.mark.unit
@pytest.mark.parametrize("tool", sorted(MAINS))
def test_debug_help_says_what_it_prints(tool: str, capsys: pytest.CaptureFixture[str]) -> None:
    command = "check" if tool == "contracts" else "serve"
    with pytest.raises(SystemExit):
        MAINS[tool]([command, "--help"], None)
    text = " ".join(capsys.readouterr().out.split())
    assert "--debug print the exception type and call frames" in text
    assert "--wide do not wrap human readable output at 100 columns" in text


@pytest.mark.unit
def test_a_long_line_wraps_at_100_columns_keeping_its_indent_and_wide_keeps_it_whole() -> None:
    line = "    " + " ".join(["word"] * 40)
    wrapped = fit(line).split("\n")
    assert len(wrapped) > 1 and all(len(part) <= WIDTH for part in wrapped)
    assert wrapped[0].startswith("    word") and all(part.startswith("      word") for part in wrapped[1:])
    assert fit(line, wide=True) == line
    assert fit("short") == "short"


@pytest.mark.unit
def test_the_last_resort_of_serve_writes_its_error_record_with_its_log_lines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def explode() -> KeyHolderSettings:
        raise RuntimeError("boom")

    monkeypatch.setattr(key_holder_cli, "load_settings", explode)
    out, err = io.StringIO(), io.StringIO()
    assert key_holder_cli.main(["serve"], CliOutput(out, err)) == 1
    record = json.loads(out.getvalue())
    assert (record["level"], record["event"], record["exception_type"]) == (
        "error",
        "unhandled_exception",
        "RuntimeError",
    )
    assert record["service"] == "key_holder" and "boom" not in out.getvalue()
    assert err.getvalue() == "Unexpected error. Run with --debug for details.\n"


# SPEC_DECISIONS 78: a well formed number out of range says so (review 3 finding 8).
@pytest.mark.unit
def test_a_daily_cap_of_ten_to_the_eighteen_is_out_of_range() -> None:
    environment = {"FAKE_KH_DAILY_ELEMENT_CAP": "1" + "0" * 18}
    with pytest.raises(ConfigurationError) as raised:
        EnvironmentReader(environment).integer("FAKE_KH_DAILY_ELEMENT_CAP", 1, minimum=1)
    assert raised.value.sentence() == (
        "Configuration error: FAKE_KH_DAILY_ELEMENT_CAP is out of range (1 to 999,999,999,999,999,999)."
    )


# The settings every service shares have one sentence each, word for word outside records' sentences.
SHARED = {
    ("PORT", "+80"): "Configuration error: PORT must be a whole number written with the digits 0 to 9.",
    ("PORT", "70000"): "Configuration error: PORT is out of range (1 to 65,535).",
    ("LOG_LEVEL", "LOUD"): "Configuration error: LOG_LEVEL must be DEBUG, INFO, WARNING, ERROR or CRITICAL.",
    ("BIND_HOST", "localhost"): (
        "Configuration error: BIND_HOST must be an IPv4 or IPv6 address; set it to 127.0.0.1 or 0.0.0.0."
    ),
}
READERS: dict[str, Callable[[EnvironmentReader], object]] = {
    "PORT": EnvironmentReader.port,
    "LOG_LEVEL": EnvironmentReader.log_level,
    "BIND_HOST": EnvironmentReader.bind_host,
}


@pytest.mark.unit
@pytest.mark.parametrize(("setting", "sentence"), list(SHARED.items()), ids=[" ".join(k) for k in SHARED])
def test_a_shared_setting_has_the_sentence_of_every_service(setting: tuple[str, str], sentence: str) -> None:
    name, value = setting
    with pytest.raises(ConfigurationError) as raised:
        READERS[name](EnvironmentReader({name: value}))
    assert raised.value.sentence() == sentence
    assert not sentence.endswith(":") and len(sentence) <= WIDTH
