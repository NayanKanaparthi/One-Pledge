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
from onepledge_datagen.domain.text import plural
from onepledge_datagen.domain.text_values import MAX_INTEGER, IntegerIssue, parse_integer_text
from onepledge_datagen.infrastructure.codec import FileCodec
from onepledge_datagen.infrastructure.directory import LocalDataDirectory
from onepledge_datagen.interface.output import Console, Json, common_options, parse_arguments, run_guarded

MAX_LISTED_VIOLATIONS = 50


def seed_argument(text: str) -> int:
    """`--seed`: ASCII digits only, 0 to 999,999,999,999,999,999 (SPEC_DECISIONS 37)."""
    seed = parse_integer_text(text, 0, MAX_INTEGER)
    if isinstance(seed, IntegerIssue):
        raise argparse.ArgumentTypeError(
            "must be a whole number from 0 to 999,999,999,999,999,999 in ASCII digits"
        )
    return seed


def directory_argument(text: str) -> Path:
    """A directory argument: an empty value is a usage error, not the current directory."""
    if not text.strip():
        raise argparse.ArgumentTypeError("must name a directory, not be empty")
    return Path(text)


def build_parser() -> argparse.ArgumentParser:
    """Argument parser with the `generate` and `verify` subcommands."""
    parser = argparse.ArgumentParser(
        prog="onepledge-datagen",
        description="Generate and verify the seeded synthetic data set of OnePledge layer 1.",
    )
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    generate = commands.add_parser(
        "generate",
        parents=[common_options()],
        help="generate a data set from a seed and a profile",
        description="Generate tapes, outside records, reference data, the timeline and the answer key. "
        "Every invariant is checked on the rendered files before anything is written.",
    )
    generate.add_argument(
        "--seed",
        type=seed_argument,
        required=True,
        metavar="INT",
        help="whole number seed from 0 to 999,999,999,999,999,999; the same seed gives the same bytes",
    )
    generate.add_argument(
        "--profile",
        required=True,
        choices=[p.value for p in Profile],
        help="size profile: full, small or example",
    )
    generate.add_argument(
        "--out",
        type=directory_argument,
        required=True,
        metavar="DIR",
        help="output directory (must be empty unless --force)",
    )
    generate.add_argument("--force", action="store_true", help="replace an existing data set in --out")
    verify = commands.add_parser(
        "verify",
        parents=[common_options()],
        help="re-check checksums and every invariant, then compare with a regeneration",
        description="Re-check the checksums and every invariant of SPEC 5.8 on an existing data directory, "
        "then regenerate it in memory from the seed and profile of its manifest and require every file "
        "to be byte identical. Exits 1 and lists the problems when any is found.",
    )
    verify.add_argument(
        "--data-dir",
        type=directory_argument,
        required=True,
        metavar="DIR",
        help="the data directory to verify",
    )
    return parser


def _report_generation(console: Console, summary: GenerationSummary, target: Path) -> None:
    console.line(f"OnePledge data set: profile {summary.profile}, seed {summary.seed}")
    console.line()
    rows = [(s[0], s[1], s[2], s[3], s[4], f"{s[5]:,}") for s in summary.steps]
    console.table(("Step", "Bank", "Facility", "Kind", "As-of", "Rows"), rows)
    console.line()
    records = ", ".join(
        (
            plural(summary.titles, "title"),
            plural(summary.payments, "payment"),
            plural(summary.contracts, "contract"),
        )
    )
    console.line(f"Outside records: {records}")
    cases, quality = plural(summary.cases, "planted case"), plural(summary.messiness, "data quality row")
    console.line(f"Answer key: {cases}, {quality}")
    console.line(f"Invariants: all checks passed over {plural(summary.rows_checked, 'tape row')}")
    console.line(f"Wrote {plural(summary.files, 'file')} to {target}")
    steps: list[Json] = [
        {"step_id": s[0], "bank_code": s[1], "facility_code": s[2], "kind": s[3], "as_of": s[4], "rows": s[5]}
        for s in summary.steps
    ]
    console.result(
        {
            "profile": summary.profile,
            "seed": summary.seed,
            "out": str(target),
            "files": summary.files,
            "steps": steps,
            "outside_records": {
                "titles": summary.titles,
                "payments": summary.payments,
                "contracts": summary.contracts,
            },
            "cases": summary.cases,
            "messiness": summary.messiness,
            "rows_checked": summary.rows_checked,
        }
    )


def _list_violations(console: Console, violations: Sequence[str]) -> None:
    for violation in violations[:MAX_LISTED_VIOLATIONS]:
        console.error(f"  {violation}")
    if len(violations) > MAX_LISTED_VIOLATIONS:
        console.error(f"  and {len(violations) - MAX_LISTED_VIOLATIONS:,} more")


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
    generator = GenerateDataSet(codec, directory, checker)
    report = VerifyDataSet(codec, directory, checker, generator.files_for).run(source)
    if report.ok:
        files, rows = plural(report.files, "file"), plural(report.rows_checked, "tape row")
        console.line(
            f"verify: OK ({files}, {rows}, every invariant holds, byte identical to its regeneration)"
        )
        console.result({"data_dir": str(source), "files": report.files, "rows_checked": report.rows_checked})
        return 0
    sentence = f"verify: {plural(len(report.violations), 'problem')} in {source}."
    failures: list[Json] = [{"stage": f.stage.value, "problem": f.problem} for f in report.failures]
    console.failure("VERIFICATION_FAILED", sentence, {"violations": failures})
    _list_violations(console, report.violations)
    return 1


def main(argv: Sequence[str] | None = None, console: Console | None = None) -> int:
    """Entry point. Exit 0 on success, 1 on failure, 2 on a usage error."""
    out = console or Console()
    arguments = parse_arguments(build_parser(), argv, out)
    out.json_mode = arguments.json
    out.wide = arguments.wide
    return run_guarded(lambda: _run_reporting(arguments, out), out, debug=arguments.debug)


def _run_reporting(arguments: argparse.Namespace, console: Console) -> int:
    try:
        return run(arguments, console)
    except InvariantsViolatedError as error:
        sentence = f"{error.sentence} Nothing was written. This is a generator defect; please report it."
        console.failure(error.code, sentence, {"violations": list(error.violations)})
        _list_violations(console, error.violations)
        return 1
    except DataGenError as error:
        console.failure(error.code, error.sentence)
        return 1
