"""Every command of `onepledge-bank` against about a dozen malformed command lines (SPEC_DECISIONS 121).

Exit 2; every line on stderr at most 100 columns, help included; the usage error is the
product's own sentence with no repr characters and nothing the operator typed; exactly one
USAGE document on stdout when an argument starting with `--json` was given, nothing otherwise.
"""

from __future__ import annotations

import io

import pytest

from onepledge_bank_agent.interface.cli.main import main
from onepledge_bank_agent.interface.cli.output import Output
from onepledge_testtools.command_lines import Option, malformed, usage_problems

pytestmark = pytest.mark.unit

COMMANDS: list[tuple[list[str], list[Option]]] = [
    (["db", "provision"], []),
    (["db", "migrate"], []),
    (["db", "status"], []),
    (["seed-reference"], [Option("--data-dir", "var/data/x")]),
    (["worker"], [Option("--once", None, required=False)]),
    (["registry", "sync"], []),
    (["integrity", "report"], []),
    (["integrity", "verify"], []),
]
CASES = [
    (name, argv)
    for command, options in COMMANDS
    for name, argv in malformed(command, options).items()
    if name not in ("no command", "unknown command") or command == ["db", "provision"]
]


@pytest.mark.parametrize(("name", "argv"), CASES, ids=[f"{argv[:2]} {name}" for name, argv in CASES])
def test_a_malformed_command_line_is_a_usage_error_in_the_products_words(
    name: str, argv: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    out = io.StringIO()
    with pytest.raises(SystemExit) as raised:
        main(argv, Output(out, io.StringIO()))
    assert usage_problems(argv, raised.value.code, out.getvalue(), capsys.readouterr().err) == [], name


@pytest.mark.parametrize(
    "command",
    [[], ["db"], ["db", "migrate"], ["seed-reference"], ["worker"], ["registry"], ["integrity", "verify"]],
)
def test_every_help_line_is_at_most_100_columns(
    command: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as raised:
        main([*command, "--help"])
    assert raised.value.code == 0
    assert [line for line in capsys.readouterr().out.splitlines() if len(line) > 100] == []


def test_worker_help_names_every_dependency_and_its_variables(capsys: pytest.CaptureFixture[str]) -> None:
    """Review 3 quality finding 10: the worker calls outside records, both key holders and the registry."""
    with pytest.raises(SystemExit):
        main(["worker", "--help"])
    text = " ".join(capsys.readouterr().out.split())
    for variable in (
        "OUTSIDE_RECORDS_URL",
        "KEY_HOLDER_1_URL",
        "KEY_HOLDER_2_URL",
        "REGISTRY_URL",
        "DATABASE_URL",
    ):
        assert variable in text, variable
