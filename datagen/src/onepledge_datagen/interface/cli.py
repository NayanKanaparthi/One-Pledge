"""`onepledge-datagen generate` and `onepledge-datagen verify` (SPEC 5.1)."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from onepledge_datagen.application.generate import GenerateDataSet, GenerationSummary
from onepledge_datagen.application.invariants import InvariantChecker
from onepledge_datagen.application.verify import VerifyDataSet
from onepledge_datagen.domain.entities import Profile
from onepledge_datagen.domain.errors import DataGenError, InvariantsViolatedError
from onepledge_datagen.infrastructure.codec import FileCodec
from onepledge_datagen.infrastructure.directory import LocalDataDirectory
from onepledge_datagen.interface.output import Console

MAX_LISTED_VIOLATIONS = 50


def build_parser() -> argparse.ArgumentParser:
    """Argument parser with the `generate` and `verify` subcommands."""
    parser = argparse.ArgumentParser(
        prog="onepledge-datagen",
        description="Generate and verify the seeded synthetic data set of OnePledge layer 1.",
    )
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    generate = commands.add_parser(
        "generate",
        help="generate a data set from a seed and a profile",
        description="Generate tapes, outside records, reference data, the timeline and the answer key. "
        "Every invariant is checked on the rendered files before anything is written.",
    )
    generate.add_argument(
        "--seed", type=int, required=True, help="integer seed; the same seed gives the same bytes"
    )
    generate.add_argument(
        "--profile",
        required=True,
        choices=[p.value for p in Profile],
        help="size profile: full, small or example",
    )
    generate.add_argument(
        "--out", type=Path, required=True, help="output directory (must be empty unless --force)"
    )
    generate.add_argument("--force", action="store_true", help="replace an existing data set in --out")
    verify = commands.add_parser(
        "verify",
        help="re-check checksums and every invariant of a data directory",
        description="Re-check the checksums and every invariant of SPEC 5.8 on an existing data directory. "
        "Exits 1 and lists the violations when any is found.",
    )
    verify.add_argument("--data-dir", type=Path, required=True, help="the data directory to verify")
    return parser


def _report_generation(console: Console, summary: GenerationSummary, target: Path) -> None:
    console.line(f"OnePledge data set: profile {summary.profile}, seed {summary.seed}")
    console.line()
    rows = [(s[0], s[1], s[2], s[3], s[4], f"{s[5]:,}") for s in summary.steps]
    console.table(("Step", "Bank", "Facility", "Kind", "As-of", "Rows"), rows)
    console.line()
    records = f"{summary.titles:,} titles, {summary.payments:,} payments, {summary.contracts:,} contracts"
    console.line(f"Outside records: {records}")
    console.line(f"Answer key: {summary.cases:,} planted cases, {summary.messiness:,} data quality rows")
    console.line(f"Invariants: all checks passed over {summary.rows_checked:,} tape rows")
    console.line(f"Wrote {summary.files} files to {target}")


def _list_violations(console: Console, violations: Sequence[str]) -> None:
    for violation in violations[:MAX_LISTED_VIOLATIONS]:
        console.error(f"  {violation}")
    if len(violations) > MAX_LISTED_VIOLATIONS:
        console.error(f"  and {len(violations) - MAX_LISTED_VIOLATIONS} more")


def run(arguments: argparse.Namespace, console: Console) -> int:
    """Run one parsed command and return the exit code."""
    codec, directory, checker = FileCodec(), LocalDataDirectory(), InvariantChecker()
    if arguments.command == "generate":
        target: Path = arguments.out
        summary = GenerateDataSet(codec, directory, checker).run(
            arguments.seed, Profile(arguments.profile), target, arguments.force
        )
        _report_generation(console, summary, target)
        return 0
    source: Path = arguments.data_dir
    report = VerifyDataSet(codec, directory, checker).run(source)
    if report.ok:
        console.line(
            f"verify: OK ({report.files} files, {report.rows_checked:,} tape rows, every invariant holds)"
        )
        return 0
    console.error(f"verify: {len(report.violations)} invariant violations in {source}:")
    _list_violations(console, report.violations)
    return 1


def main(argv: Sequence[str] | None = None, console: Console | None = None) -> int:
    """Entry point. Exit 0 on success, 1 on failure, 2 on a usage error."""
    out = console or Console()
    arguments = build_parser().parse_args(argv)
    try:
        return run(arguments, out)
    except InvariantsViolatedError as error:
        out.error(f"{error.sentence} Nothing was written. This is a generator defect; please report it.")
        _list_violations(out, error.violations)
        return 1
    except DataGenError as error:
        out.error(error.sentence)
        return 1
