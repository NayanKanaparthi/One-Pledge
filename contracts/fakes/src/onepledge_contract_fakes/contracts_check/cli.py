"""Command line of the contract checker: `onepledge-contracts check [--contracts-dir DIR]`.

Part of the TEST DOUBLES package `onepledge_contract_fakes`; it checks contracts and is not a service.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from onepledge_contract_fakes.cli_output import CliOutput
from onepledge_contract_fakes.contracts_check.checker import CheckReport, ContractsChecker


def build_parser() -> argparse.ArgumentParser:
    """Argument parser with one `check` subcommand."""
    parser = argparse.ArgumentParser(
        prog="onepledge-contracts",
        description="Check the OnePledge contracts: OpenAPI validity, examples, references and test vectors.",
    )
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    check = commands.add_parser(
        "check",
        help="run every contract check and print one line per check",
        description="Validate the four OpenAPI files, every indexed example, every externalValue link and "
        "the published test vectors. Exits 0 when everything passes and 1 otherwise.",
    )
    check.add_argument(
        "--contracts-dir",
        type=Path,
        default=Path("contracts"),
        help="directory holding the contract YAML files and examples/ (default: contracts)",
    )
    return parser


def render(report: CheckReport, out: CliOutput) -> None:
    """One line per check, the problems under failed checks, then the closing line."""
    for outcome in report.outcomes:
        if outcome.ok:
            out.line(f"contracts-check: OK   {outcome.name}: {outcome.summary}")
            continue
        out.line(f"contracts-check: FAIL {outcome.name}: {_problems(len(outcome.problems))}")
        for problem in outcome.problems:
            out.line(f"    {problem}")
    if report.ok:
        out.line(f"contracts-check: OK ({report.contract_count} files, {report.example_count} examples)")
    else:
        failures = sum(len(outcome.problems) for outcome in report.outcomes)
        out.line(f"contracts-check: FAILED ({_problems(failures)})")


def _problems(count: int) -> str:
    return f"{count} problem" if count == 1 else f"{count} problems"


def main(argv: Sequence[str] | None = None, output: CliOutput | None = None) -> int:
    """Entry point. Returns 0 when every check passes, 1 otherwise."""
    out = output or CliOutput()
    arguments = build_parser().parse_args(argv)
    contracts_dir: Path = arguments.contracts_dir
    if not contracts_dir.is_dir():
        out.error(f"The contracts directory {contracts_dir} does not exist; pass --contracts-dir.")
        return 1
    report = ContractsChecker(contracts_dir).run()
    render(report, out)
    return 0 if report.ok else 1
