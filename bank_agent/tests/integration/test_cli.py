"""`onepledge-bank` (SPEC 19): db provision, migrate, status, seed-reference and worker, by an operator.

Most tests call `main()` in process with the variables set in the environment; the
tests about exit codes and stderr run the installed program as a child process.
"""

from __future__ import annotations

import io
import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pytest

from bank_testkit.data import seed_reference
from bank_testkit.database import BankDatabase, drop, new_bank_database, pairs
from bank_testkit.outside import InProcessOutsideRecords
from bank_testkit.pipeline import BankAgentHarness
from bank_testkit.programs import run_program
from onepledge_bank_agent.domain.enums import TapeKind
from onepledge_bank_agent.infrastructure.db.migrations import head_revision
from onepledge_bank_agent.interface.cli.main import main
from onepledge_bank_agent.interface.cli.output import Output
from onepledge_bank_agent.interface.composition import Collaborators

pytestmark = pytest.mark.integration

TAPE = "tapes/HARBOR/S01_HARBOR-SUMMIT_BACK_BOOK.csv"


@dataclass
class Run:
    """One in-process invocation: exit code, stdout and stderr."""

    code: int
    out: str
    err: str

    def json(self) -> object:
        """Stdout parsed as JSON."""
        return json.loads(self.out)


type Cli = Callable[..., Run]


@pytest.fixture
def fresh() -> Iterator[BankDatabase]:
    """Names for a bank database that does not exist yet; dropped afterwards."""
    database = new_bank_database()
    yield database
    drop(database)


def cli_for(monkeypatch: pytest.MonkeyPatch, environment: dict[str, str]) -> Cli:
    """Calls `main()` with exactly these bank agent variables in the process environment."""
    for name in ("PG_ADMIN_URL", "MIGRATION_DATABASE_URL", "DATABASE_URL", "DB_APP_ROLE", "BANK_CODE"):
        monkeypatch.delenv(name, raising=False)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)

    def invoke(*argv: str, collaborators: Collaborators | None = None) -> Run:
        out, err = io.StringIO(), io.StringIO()
        code = main(list(argv), Output(out, err), collaborators)
        return Run(code, out.getvalue(), err.getvalue())

    return invoke


def test_provision_migrate_and_status_bring_up_a_new_bank_database(
    fresh: BankDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli = cli_for(monkeypatch, fresh.environment())
    first = cli("db", "provision")
    assert first == Run(
        0,
        f"Provisioned database {fresh.name} with roles {fresh.target.owner_role} and {fresh.app_role}.\n",
        "",
    )
    again = cli("db", "provision", "--json")
    assert (again.code, again.json()) == (
        0,
        {"database": fresh.name, "owner_role": fresh.target.owner_role, "app_role": fresh.app_role},
    )
    assert cli("db", "migrate") == Run(0, f"Migrated the bank database to revision {head_revision()}.\n", "")
    assert cli("db", "migrate", "--json").json() == {"revision": head_revision()}
    status = cli("db", "status", "--json")
    assert status.json() == {
        "database": fresh.name,
        "current_revision": head_revision(),
        "head_revision": head_revision(),
        "at_head": True,
    }
    text = cli("db", "status").out.splitlines()
    assert text[0] == "Bank database schema" and text[-1].split() == ["at", "latest", "yes"]
    assert fresh.app_url.split("@")[0] not in first.out + status.out, "no password is printed"


def test_status_reports_a_database_behind_the_latest_revision(
    bank_db: BankDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli = cli_for(monkeypatch, bank_db.environment())
    with bank_db.connect("owner") as connection:
        connection.execute("UPDATE alembic_version SET version_num = '0002_guards'")
    behind = cli("db", "status", "--json")
    assert (behind.code, behind.json()) == (
        0,
        {
            "database": bank_db.name,
            "current_revision": "0002_guards",
            "head_revision": head_revision(),
            "at_head": False,
        },
    )
    assert cli("db", "status").out.splitlines()[-1].split()[-4:] == ["no,", "run", "db", "migrate"]
    with bank_db.connect("owner") as connection:
        connection.execute("UPDATE alembic_version SET version_num = %s", (head_revision(),))
    assert cli("db", "status", "--json").json() == {
        "database": bank_db.name,
        "current_revision": head_revision(),
        "head_revision": head_revision(),
        "at_head": True,
    }


def test_seed_reference_loads_the_bank_reference_data_and_is_idempotent(
    bank_db: BankDatabase, example_data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli = cli_for(monkeypatch, bank_db.environment())
    seeded = cli("seed-reference", "--data-dir", str(example_data), "--json")
    stored = pairs(bank_db, "SELECT 'lenders', count(*)::text FROM lenders UNION ALL "
                   "SELECT 'facilities', count(*)::text FROM facilities")  # fmt: skip
    assert seeded.code == 0
    assert seeded.json() == {
        "bank_code": "HARBOR",
        "lenders": int(stored["lenders"]),
        "facilities": int(stored["facilities"]),
    }
    assert int(stored["facilities"]) > 0
    text = cli("seed-reference", "--data-dir", str(example_data))
    assert text.out.splitlines()[0] == "Reference data of HARBOR"
    assert text.out.splitlines()[-1] == "Lenders and facilities are up to date."
    assert pairs(bank_db, "SELECT 'facilities', count(*)::text FROM facilities") == {
        "facilities": stored["facilities"]
    }
    missing = cli("seed-reference", "--data-dir", str(example_data / "nowhere"))
    assert missing.code == 1 and missing.err.count("\n") == 1 and missing.out == ""


def test_worker_once_runs_every_runnable_stage_and_reports_it(
    bank_db: BankDatabase,
    small_data: Path,
    small_outside: InProcessOutsideRecords,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seed_reference(bank_db, small_data)
    with BankAgentHarness(bank_db, small_outside) as harness:
        submission = harness.ingest(small_data / TAPE, "HARBOR-SUMMIT", TapeKind.BACK_BOOK, date(2026, 9, 30))
        cli = cli_for(monkeypatch, harness.environment)
        report = cli("worker", "--once", "--json", collaborators=harness.collaborators)
        idle = cli("worker", "--once", collaborators=harness.collaborators)
    assert report.code == 0, report.err
    assert report.json() == {
        "stages": [
            {
                "submission_id": str(submission.submission_id),
                "from": "RECEIVED",
                "outcome": "ADVANCED",
                "status": "CLEANED",
                "error_code": "",
            },
            {
                "submission_id": str(submission.submission_id),
                "from": "CLEANED",
                "outcome": "ADVANCED",
                "status": "CHECKED",
                "error_code": "",
            },
        ]
    }
    assert pairs(bank_db, "SELECT 'status', status FROM tape_submissions") == {"status": "CHECKED"}
    assert idle.out.splitlines()[-2:] == ["0 stages run", "Nothing else is runnable now."]


def test_the_worker_refuses_a_schema_that_is_not_at_the_latest_revision(
    bank_db: BankDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli = cli_for(monkeypatch, {**bank_db.environment(), "OUTSIDE_RECORDS_URL": "http://127.0.0.1:9"})
    monkeypatch.setenv("OUTSIDE_RECORDS_API_KEY", "k" * 32)
    with bank_db.connect("owner") as connection:
        connection.execute("UPDATE alembic_version SET version_num = '0002_guards'")
    refused = cli("worker", "--once")
    assert refused == Run(
        1, "", f"The database {bank_db.name} is not at the latest revision; run db migrate.\n"
    )


def test_a_missing_variable_is_one_sentence_on_stderr_and_exit_1(bank_db: BankDatabase) -> None:
    environment = bank_db.environment()
    for command, missing in (
        (["db", "status"], "DATABASE_URL"),
        (["db", "migrate"], "MIGRATION_DATABASE_URL"),
        (["db", "provision"], "PG_ADMIN_URL"),
        (["seed-reference", "--data-dir", "."], "BANK_CODE"),
    ):
        finished = run_program(
            "onepledge-bank", command, {k: v for k, v in environment.items() if k != missing}
        )
        assert (finished.returncode, finished.stdout, finished.stderr) == (
            1,
            "",
            f"Configuration error: {missing} is required but not set.\n",
        ), command


def test_an_unreachable_database_is_one_sentence_without_the_url(bank_db: BankDatabase) -> None:
    unreachable = "postgresql://someone:hunter2-secret@127.0.0.1:9/nowhere"
    finished = run_program(
        "onepledge-bank", ["db", "status", "--json"], {**bank_db.environment(), "DATABASE_URL": unreachable}
    )
    assert finished.returncode == 1 and finished.stdout == ""
    assert finished.stderr == "PostgreSQL refused the connection at DATABASE_URL; run db provision first.\n"
    assert "hunter2" not in finished.stderr


def test_a_usage_error_exits_2(bank_db: BankDatabase) -> None:
    for arguments in (["db"], ["seed-reference"], ["no-such-command"], ["worker", "--twice"]):
        finished = run_program("onepledge-bank", arguments, bank_db.environment())
        assert finished.returncode == 2, arguments
        assert "Traceback" not in finished.stderr and finished.stdout == ""
    helped = run_program("onepledge-bank", ["--help"], bank_db.environment())
    assert helped.returncode == 0 and "seed-reference" in helped.stdout and "worker" in helped.stdout
