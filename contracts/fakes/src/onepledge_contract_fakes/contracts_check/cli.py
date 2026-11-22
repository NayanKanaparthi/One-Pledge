"""Command line of the contract checker: `onepledge-contracts check [--contracts-dir DIR]`.

Part of the TEST DOUBLES package `onepledge_contract_fakes`; it checks contracts and is not a service.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from onepledge_contract_fakes.canonical_json import JsonValue
from onepledge_contract_fakes.cli_output import CliOutput, common_options, plural, run_guarded
from onepledge_contract_fakes.contracts_check.checker import CheckOutcome, CheckReport, ContractsChecker


def build_parser() -> argparse.ArgumentParser:
    """Argument parser with one `check` subcommand."""
    parser = argparse.ArgumentParser(
        prog="onepledge-contracts",
        description="Check the OnePledge contracts: OpenAPI validity, examples, references and test vectors.",
    )
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    check = commands.add_parser(
        "check",
        parents=[common_options(result=True, logs=False)],
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
        if outcome.skipped_because is not None:
            out.line(f"contracts-check: SKIP {outcome.skipped_because}")
        elif outcome.ok:
            out.line(f"contracts-check: OK   {outcome.name}: {outcome.summary}")
        else:
            out.line(f"contracts-check: FAIL {outcome.name}: {plural(len(outcome.problems), 'problem')}")
            for problem in outcome.problems:
                out.line(f"    {problem}")
    if report.ok:
        files, examples = plural(report.contract_count, "file"), plural(report.example_count, "example")
        out.line(f"contracts-check: OK ({files}, {examples})")
    else:
        out.line(f"contracts-check: FAILED, {plural(problem_count(report), 'problem')}")


def problem_count(report: CheckReport) -> int:
    """Problems over every check (a skipped check adds none)."""
    return sum(len(outcome.problems) for outcome in report.outcomes)


def _summary(outcome: CheckOutcome) -> str:
    """What the check found: its success sentence only when it passed (SPEC_DECISIONS 40)."""
    if outcome.skipped_because is not None:
        return outcome.skipped_because
    return outcome.summary if outcome.ok else f"{plural(len(outcome.problems), 'problem')} found"


def report_json(report: CheckReport) -> dict[str, JsonValue]:
    """The facts the text output shows, as JSON."""
    checks: list[JsonValue] = [
        {
            "name": outcome.name,
            "status": "SKIPPED" if outcome.skipped_because else ("OK" if outcome.ok else "FAILED"),
            "summary": _summary(outcome),
            "problems": list(outcome.problems),
        }
        for outcome in report.outcomes
    ]
    return {
        "files": report.contract_count,
        "examples": report.example_count,
        "problems": problem_count(report),
        "checks": checks,
    }


def main(argv: Sequence[str] | None = None, output: CliOutput | None = None) -> int:
    """Entry point. Returns 0 when every check passes, 1 otherwise, 2 on a usage error."""
    out = output or CliOutput()
    arguments = build_parser().parse_args(argv)
    out.json_mode = arguments.json
    return run_guarded(lambda: _check(arguments.contracts_dir, out), out, debug=arguments.debug)


def _check(contracts_dir: Path, out: CliOutput) -> int:
    if not contracts_dir.is_dir():
        out.failure(
            "CONTRACTS_DIR_NOT_FOUND",
            f"The contracts directory {contracts_dir} does not exist; pass --contracts-dir.",
        )
        return 1
    report = ContractsChecker(contracts_dir).run()
    render(report, out)
    if report.ok:
        out.result(report_json(report))
        return 0
    sentence = f"The contracts check found {plural(problem_count(report), 'problem')}."
    out.failure("CONTRACTS_INVALID", sentence, report_json(report))
    return 1
