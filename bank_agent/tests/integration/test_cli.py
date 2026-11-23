"""`onepledge-bank` (SPEC 19): db provision, migrate, status, seed-reference and worker, by an operator.

Most tests call `main()` in process with the variables set in the environment; the
tests about exit codes and stderr run the installed program as a child process.
"""

from __future__ import annotations

import hashlib
import io
import json
import signal
import subprocess
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import httpx
import psycopg
import pytest
from psycopg import sql

from bank_testkit.audit_chain import first_broken_seq
from bank_testkit.data import seed_reference
from bank_testkit.database import (
    BankDatabase,
    drop,
    migration_runner,
    new_bank_database,
    pairs,
    provision_and_migrate,
)
from bank_testkit.doubles import InProcessDoubles
from bank_testkit.outside import InProcessOutsideRecords, ScriptedOutsideRecords
from bank_testkit.pipeline import BankAgentHarness, Services
from bank_testkit.programs import run_program, split_stderr, unwrap
from onepledge_bank_agent.domain.enums import TapeKind
from onepledge_bank_agent.infrastructure.db.migrations import head_revision
from onepledge_bank_agent.infrastructure.db.provisioning import DatabaseProvisioner
from onepledge_bank_agent.infrastructure.db.role_check import ADVICE, Violation
from onepledge_bank_agent.infrastructure.http.base import HttpTimeouts
from onepledge_bank_agent.interface.cli.main import main
from onepledge_bank_agent.interface.cli.output import Output
from onepledge_bank_agent.interface.composition import Collaborators

pytestmark = pytest.mark.integration

TAPE = "tapes/HARBOR/S01_HARBOR-SUMMIT_BACK_BOOK.csv"
REGISTRY_SIDE = (
    "REGISTRY_URL", "REGISTRY_API_KEY", "REGISTRY_MEMBER_ID", "REGISTRY_PUBLIC_KEYS", "KEY_HOLDER_1_URL",
    "KEY_HOLDER_1_API_KEY", "KEY_HOLDER_2_URL", "KEY_HOLDER_2_API_KEY", "HALT_ON_REGISTRY_INTEGRITY_FAILURE",
)  # fmt: skip
UNREACHABLE = "http://127.0.0.1:9"
IDENTITY = {"BANK_CODE": "HARBOR", "BANK_NAME": "Harbor Bank"}


def unreachable_services(doubles: InProcessDoubles) -> dict[str, str]:
    """Outside records, registry and key holder variables that point at a closed port."""
    variables = doubles.environment("HARBOR")
    variables.update(
        {
            "OUTSIDE_RECORDS_URL": UNREACHABLE,
            "OUTSIDE_RECORDS_API_KEY": "opk_" + "k" * 43,
            "REGISTRY_URL": UNREACHABLE,
            "KEY_HOLDER_1_URL": UNREACHABLE,
            "KEY_HOLDER_2_URL": UNREACHABLE,
        }
    )
    return variables


@dataclass
class Run:
    """One in-process invocation: exit code, stdout and stderr."""

    code: int
    out: str
    err: str

    def json(self) -> object:
        """Stdout parsed as JSON."""
        return json.loads(self.out)

    def outcome(self) -> tuple[int, str, str]:
        """Exit code, stdout and the sentences on stderr with every wrapped line joined back."""
        return self.code, self.out, unwrap(self.err)


type Cli = Callable[..., Run]


@pytest.fixture
def fresh() -> Iterator[BankDatabase]:
    """Names for a bank database that does not exist yet; dropped afterwards."""
    database = new_bank_database()
    yield database
    drop(database)


def cli_for(monkeypatch: pytest.MonkeyPatch, environment: dict[str, str]) -> Cli:
    """Calls `main()` with exactly these bank agent variables in the process environment."""
    for name in (
        "PG_ADMIN_URL", "MIGRATION_DATABASE_URL", "DATABASE_URL", "DB_APP_ROLE", "BANK_CODE", "BANK_NAME",
        "BIND_HOST", "PORT", "LOG_LEVEL",
        "OUTSIDE_RECORDS_URL", "OUTSIDE_RECORDS_API_KEY", *REGISTRY_SIDE,
    ):  # fmt: skip
        monkeypatch.delenv(name, raising=False)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)

    def invoke(*argv: str, collaborators: Collaborators | None = None) -> Run:
        out, err = io.StringIO(), io.StringIO()
        code = main(list(argv), Output(out, err), collaborators)
        run = Run(code, out.getvalue(), err.getvalue())
        if not {"--wide", "--debug"} & set(argv):  # SPEC_DECISIONS 78: every human readable line of every run
            split_stderr(run.err)
            if "--json" not in argv:
                unwrap(run.out)
        return run

    return invoke


REFERENCE_DIGESTS = ("content_sha256", "lenders_sha256", "facilities_sha256")


def split_digests(detail: object) -> tuple[dict[str, object], tuple[object, ...]]:
    """A REFERENCE_DATA_SEEDED detail without its digests, and the digests."""
    assert isinstance(detail, dict)
    rest = {key: value for key, value in detail.items() if key not in REFERENCE_DIGESTS}
    return rest, tuple(detail[key] for key in REFERENCE_DIGESTS)


def audit_actions(database: BankDatabase) -> list[tuple[object, ...]]:
    with database.connect("admin") as connection:
        return connection.execute(
            "SELECT action, object_type, actor_type, detail FROM audit_log ORDER BY seq"
        ).fetchall()


def test_provision_migrate_and_status_bring_up_a_new_bank_database(
    fresh: BankDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli = cli_for(monkeypatch, fresh.environment())
    first = cli("db", "provision")
    assert (first.code, unwrap(first.out), first.err) == (
        0,
        f"Provisioned database {fresh.name} with roles {fresh.target.owner_role} and {fresh.app_role}.\n",
        "",
    )
    again = cli("db", "provision", "--json")
    assert (again.code, again.json()) == (
        0,
        {
            "ok": True,
            "database": fresh.name,
            "owner_role": fresh.target.owner_role,
            "app_role": fresh.app_role,
        },
    )
    head = head_revision()
    assert cli("db", "migrate") == Run(0, f"Migrated the bank database from revision none to {head}.\n", "")
    assert cli("db", "migrate", "--json").json() == {
        "ok": True, "from_revision": head, "to_revision": head, "changed": False,
    }  # fmt: skip
    assert cli("db", "migrate").out == f"The bank database is already at revision {head}.\n"
    status = cli("db", "status", "--json")
    assert status.json() == {
        "ok": True, "database": fresh.name, "current_revision": head, "head_revision": head, "at_head": True,
    }  # fmt: skip
    text = cli("db", "status").out.splitlines()
    assert text[0] == "Bank database schema" and text[-1].split() == ["at", "latest", "yes"]
    assert fresh.app_url.split("@")[0] not in first.out + status.out, "no password is printed"
    assert audit_actions(fresh) == [
        ("SCHEMA_MIGRATED", "SCHEMA", "SYSTEM", {"via": "cli", "from_revision": None, "to_revision": head})
    ], "one entry: only the migration that changed the revision is audited"
    assert first_broken_seq(fresh) is None


def test_migrating_a_database_behind_the_head_records_both_revisions(
    bank_db: BankDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    migration_runner(bank_db).downgrade("0002_guards")
    cli = cli_for(monkeypatch, bank_db.environment())
    migrated = cli("db", "migrate", "--json")
    assert migrated.json() == {
        "ok": True, "from_revision": "0002_guards", "to_revision": head_revision(), "changed": True,
    }  # fmt: skip
    ((action, _, _, detail),) = audit_actions(bank_db)
    assert (action, detail) == (
        "SCHEMA_MIGRATED",
        {"via": "cli", "from_revision": "0002_guards", "to_revision": head_revision()},
    )


def test_db_migrate_logs_each_applied_revision_and_no_library_line(bank_db: BankDatabase) -> None:
    """SPEC_DECISIONS 43.6: a schema_migration_applied record per applied revision; no Alembic INFO."""
    migration_runner(bank_db).downgrade("0002_guards")
    versions = Path(__file__).resolve().parents[2] / "migrations" / "versions"
    expected = sorted(path.stem for path in versions.glob("0*.py") if path.stem > "0002_guards")
    finished = run_program("onepledge-bank", ["db", "migrate", "--json"], bank_db.environment())
    assert finished.returncode == 0, finished.stderr
    records, sentences = split_stderr(finished.stderr)
    assert sentences == []
    assert [record["event"] for record in records] == ["schema_migration_applied"] * len(expected)
    assert [record["revision"] for record in records] == expected


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
            "ok": True,
            "database": bank_db.name,
            "current_revision": "0002_guards",
            "head_revision": head_revision(),
            "at_head": False,
        },
    )
    assert cli("db", "status").out.splitlines()[-1].split()[-4:] == ["no,", "run", "db", "migrate"]


def test_seed_reference_loads_the_bank_reference_data_audits_it_and_is_idempotent(
    bank_db: BankDatabase, example_data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli = cli_for(monkeypatch, bank_db.environment())
    first = cli("seed-reference", "--data-dir", str(example_data))
    stored = pairs(bank_db, "SELECT 'lenders', count(*)::text FROM lenders UNION ALL "
                   "SELECT 'facilities', count(*)::text FROM facilities")  # fmt: skip
    digest = hashlib.sha256((example_data / "reference.json").read_bytes()).hexdigest()
    lenders, facilities = int(stored["lenders"]), int(stored["facilities"])
    assert first.code == 0 and facilities > 0
    text = first.out.splitlines()
    assert text[0] == "Reference data of HARBOR"
    assert text[1].split() == ["table", "inserted", "updated", "unchanged"]
    assert text[2].split() == ["lenders", str(lenders), "0", "0"]
    assert text[-2:] == [
        "2 tables seeded",
        f"{lenders + facilities:,} rows inserted, 0 updated, 0 unchanged (reference.json {digest[:12]}...).",
    ], "the counts it audits (SPEC_DECISIONS 78), not a fixed sentence"
    seeded = cli("seed-reference", "--data-dir", str(example_data), "--json")
    assert seeded.json() == {
        "ok": True, "bank_code": "HARBOR", "source_sha256": digest,
        "lenders": {"inserted": 0, "updated": 0, "unchanged": lenders},
        "facilities": {"inserted": 0, "updated": 0, "unchanged": facilities},
    }  # fmt: skip
    again = cli("seed-reference", "--data-dir", str(example_data)).out.splitlines()
    assert again[-1] == (
        f"Nothing changed: {lenders + facilities:,} rows already up to date "
        f"(reference.json {digest[:12]}...)."
    )
    entries = audit_actions(bank_db)
    assert [(a, o, t) for a, o, t, _ in entries] == [
        ("REFERENCE_DATA_SEEDED", "REFERENCE_DATA", "SYSTEM")
    ] * 3
    details = [split_digests(detail) for _, _, _, detail in entries]
    assert len({digests for _, digests in details}) == 1, "SPEC_DECISIONS 107: same tables, same digests"
    assert [detail for detail, _ in details] == [
        {
            "via": "cli", "source_sha256": digest,
            "lenders": {"inserted": lenders, "updated": 0, "unchanged": 0},
            "facilities": {"inserted": facilities, "updated": 0, "unchanged": 0},
        },
        *[{
            "via": "cli", "source_sha256": digest,
            "lenders": {"inserted": 0, "updated": 0, "unchanged": lenders},
            "facilities": {"inserted": 0, "updated": 0, "unchanged": facilities},
        }] * 2,
    ], "the printed counts are the audited ones"  # fmt: skip
    assert first_broken_seq(bank_db) is None
    missing = cli("seed-reference", "--data-dir", str(example_data / "nowhere"), "--json")
    assert (
        missing.code == 1
        and unwrap(missing.err) == f"{example_data / 'nowhere'}/reference.json: no such file.\n"
    )
    assert missing.json() == {
        "ok": False,
        "error": {"code": "REFERENCE_DATA_INVALID", "message": unwrap(missing.err).strip()},
    }
    assert len(audit_actions(bank_db)) == 3, "a refused file is not audited"


def test_worker_once_runs_every_runnable_stage_and_reports_it(
    bank_db: BankDatabase,
    small_data: Path,
    small_outside: InProcessOutsideRecords,
    doubles: InProcessDoubles,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seed_reference(bank_db, small_data)
    with BankAgentHarness(bank_db, Services(small_outside, doubles)) as harness:
        submission = harness.ingest(small_data / TAPE, "HARBOR-SUMMIT", TapeKind.BACK_BOOK, date(2026, 9, 30))
        cli = cli_for(monkeypatch, harness.environment)
        handlers = (signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGINT))
        report = cli("worker", "--once", "--json", collaborators=harness.collaborators)
        idle = cli("worker", "--once", "--quiet", collaborators=harness.collaborators)
    assert (signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGINT)) == handlers, (
        "the worker gives back the handlers it found, so a SIGTERM still stops this test run"
    )
    assert report.code == 0, report.err
    no_failure = dict.fromkeys(("stage", "failure_code", "attempts", "dependency", "http_status", "reason"))
    stage = {"submission_id": str(submission.submission_id), "outcome": "ADVANCED", **no_failure}
    assert report.json() == {
        "ok": True,
        "stages": [
            {**stage, "from": "RECEIVED", "status": "CLEANED"},
            {**stage, "from": "CLEANED", "status": "CHECKED"},
            {**stage, "from": "CHECKED", "status": "TOKENIZED"},
            {**stage, "from": "TOKENIZED", "status": "SUBMITTED"},
            {**stage, "from": "SUBMITTED", "status": "COMPLETED"},
        ],
    }
    assert pairs(bank_db, "SELECT 'status', status FROM tape_submissions") == {"status": "COMPLETED"}
    assert idle.out.splitlines()[-2:] == ["0 stages run", "Nothing else is runnable now."]


def test_the_worker_refuses_a_schema_that_is_not_at_the_latest_revision(
    bank_db: BankDatabase, doubles: InProcessDoubles, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli = cli_for(monkeypatch, {**bank_db.environment(), **unreachable_services(doubles)})
    with bank_db.connect("owner") as connection:
        connection.execute("UPDATE alembic_version SET version_num = '0003_grants'")
    sentence = f"The database {bank_db.name} is not at the latest revision; run db migrate."
    assert cli("worker", "--once").outcome() == (1, "", sentence + "\n")
    refused = cli("worker", "--once", "--json")
    assert refused.json() == {"ok": False, "error": {"code": "SCHEMA_NOT_CURRENT", "message": sentence}}


def test_worker_once_json_in_a_child_process_writes_one_document_and_only_log_lines_on_stderr(
    bank_db: BankDatabase, small_data: Path, doubles: InProcessDoubles
) -> None:
    """SPEC_DECISIONS 27: stdout parses as one JSON document, every stderr line as a JSON object."""
    seed_reference(bank_db, small_data)
    with BankAgentHarness(bank_db, Services(ScriptedOutsideRecords(unreachable), doubles)) as harness:
        harness.ingest(small_data / TAPE, "HARBOR-SUMMIT", TapeKind.BACK_BOOK, date(2026, 9, 30))
    environment = {**bank_db.environment(), **unreachable_services(doubles), "LOG_LEVEL": "DEBUG",
                   "HTTP_CONNECT_TIMEOUT_SECONDS": "0.2"}  # fmt: skip
    finished = run_program("onepledge-bank", ["worker", "--once", "--json"], environment)
    assert finished.returncode == 1, "the registry is unreachable: the sync was incomplete (decision 101)"
    document = json.loads(finished.stdout)
    assert document["ok"] is False and [s["outcome"] for s in document["stages"]] == [
        "ADVANCED",
        "RETRY_SCHEDULED",
    ]
    problems = document["problems"]
    assert [p["code"] for p in problems] == ["OUTBOX_SYNC_INCOMPLETE", "REGISTRY_INTEGRITY_BROKEN"]
    records, sentences = split_stderr(finished.stderr)
    assert " ".join(" ".join(sentences).split()) == " ".join(p["message"] for p in problems), "only those"
    assert records and {record["service"] for record in records} == {"bank_agent"}
    assert {"tape_stage_advanced", "tape_stage_attempt_failed"} <= {str(r["event"]) for r in records}
    quiet = run_program("onepledge-bank", ["worker", "--once", "--json", "--quiet"], environment)
    quiet_records, _ = split_stderr(quiet.stderr)
    assert json.loads(quiet.stdout)["problems"] == problems
    assert {record["level"] for record in quiet_records} <= {"warning", "error", "critical"}


def unreachable(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("unreachable", request=request)


def test_a_missing_variable_is_one_sentence_on_stderr_and_exit_1(
    bank_db: BankDatabase, doubles: InProcessDoubles
) -> None:
    environment = {**bank_db.environment(), **unreachable_services(doubles)}
    for command, missing in (
        (["db", "status"], "DATABASE_URL"),
        (["db", "migrate"], "MIGRATION_DATABASE_URL"),
        (["db", "provision"], "PG_ADMIN_URL"),
        (["seed-reference", "--data-dir", "."], "BANK_CODE"),
        (["worker", "--once"], "OUTSIDE_RECORDS_URL"),
        (["worker", "--once"], "REGISTRY_URL"),
        (["worker", "--once"], "KEY_HOLDER_2_API_KEY"),
        (["registry", "sync"], "REGISTRY_API_KEY"),
        (["integrity", "verify"], "REGISTRY_PUBLIC_KEYS"),
        (["worker", "--once"], "BANK_CODE"),
        (["worker", "--once"], "BANK_NAME"),
        (["registry", "sync"], "BANK_CODE"),
        (["registry", "sync"], "BANK_NAME"),
        (["integrity", "report"], "BANK_CODE"),
        (["integrity", "verify"], "BANK_NAME"),
    ):
        finished = run_program(
            "onepledge-bank", command, {k: v for k, v in environment.items() if k != missing}
        )
        _, sentences = split_stderr(finished.stderr)
        assert (finished.returncode, finished.stdout, sentences) == (
            1,
            "",
            [f"Configuration error: {missing} is required but not set."],
        ), command
        as_json = run_program(
            "onepledge-bank", [*command, "--json"], {k: v for k, v in environment.items() if k != missing}
        )
        assert json.loads(as_json.stdout)["error"]["code"] == "CONFIGURATION_ERROR"


def test_no_runtime_command_reads_bind_host_or_port(bank_db: BankDatabase, doubles: InProcessDoubles) -> None:
    """SPEC_DECISIONS 73, point 1: values `serve` would refuse stop no worker and no one-shot command."""
    environment = {**bank_db.environment(), **unreachable_services(doubles), "BIND_HOST": "localhost",
                   "PORT": "http"}  # fmt: skip
    finished = run_program("onepledge-bank", ["integrity", "report", "--json"], environment)
    report = json.loads(finished.stdout)
    assert report["ok"] is True, finished.stderr
    assert finished.returncode == (0 if report["status"] == "OK" else 1), "the report's own exit rule"
    ran_without_a_registry(run_program("onepledge-bank", ["worker", "--once", "--json"], environment))


WITHOUT_A_REGISTRY = ["OUTBOX_SYNC_INCOMPLETE", "REGISTRY_INTEGRITY_BROKEN"]
"""What `worker --once` reports when the registry cannot be reached and the bank never synced: it
ran, and exits 1 for that reason alone (SPEC_DECISIONS 101)."""


def ran_without_a_registry(finished: subprocess.CompletedProcess[str]) -> None:
    document = json.loads(finished.stdout)
    assert finished.returncode == 1 and document["stages"] == [], finished.stderr
    assert [problem["code"] for problem in document["problems"]] == WITHOUT_A_REGISTRY


def test_the_worker_never_needs_an_owner_credential(
    bank_db: BankDatabase, small_data: Path, doubles: InProcessDoubles
) -> None:
    """The worker runs from an environment without MIGRATION_DATABASE_URL, PG_ADMIN_URL or DB_APP_ROLE."""
    seed_reference(bank_db, small_data)
    runtime_only = {"DATABASE_URL": bank_db.app_url, **IDENTITY, **unreachable_services(doubles)}
    ran_without_a_registry(run_program("onepledge-bank", ["worker", "--once", "--json"], runtime_only))


def role_sentence(code: str) -> str:
    """The refusal of SPEC_DECISIONS 72: the variable and the first violation code."""
    return f"The role of DATABASE_URL is not least-privilege ({code}); {ADVICE[Violation(code)]}."


def test_the_worker_refuses_to_start_as_the_owner_role(
    bank_db: BankDatabase, doubles: InProcessDoubles
) -> None:
    """SPEC_DECISIONS 43.4: as the owner, the worker exits 1 with one line naming DATABASE_URL."""
    as_owner = {"DATABASE_URL": bank_db.owner_url, **IDENTITY, **unreachable_services(doubles)}
    finished = run_program("onepledge-bank", ["worker", "--once", "--json"], as_owner)
    assert finished.returncode == 1
    assert json.loads(finished.stdout) == {
        "ok": False,
        "error": {"code": "ROLE_NOT_LEAST_PRIVILEGE", "message": role_sentence("ROLE_OWNS_OBJECT")},
    }
    assert split_stderr(finished.stderr)[1] == [role_sentence("ROLE_OWNS_OBJECT")]
    assert pairs(bank_db, "SELECT 'audit', count(*)::text FROM audit_log") == {"audit": "0"}


@pytest.mark.parametrize(
    ("grant", "code"),
    [
        ("GRANT UPDATE ON audit_log TO {role}", "TABLE_PRIVILEGE_EXTRA"),
        ("GRANT DELETE ON alerts TO {role}", "TABLE_PRIVILEGE_EXTRA"),
        ("GRANT TRUNCATE ON loans TO {role}", "TABLE_PRIVILEGE_EXTRA"),
        ("GRANT CREATE ON SCHEMA public TO {role}", "ROLE_HAS_SCHEMA_PRIVILEGE"),
        ("GRANT UPDATE ON registry_messages_quarantined TO {role}", "TABLE_PRIVILEGE_EXTRA"),
        ("GRANT UPDATE ON certificates TO {role}", "TABLE_PRIVILEGE_EXTRA"),
        ("GRANT UPDATE (detail) ON audit_log TO {role}", "COLUMN_PRIVILEGE"),
        ("GRANT {owner} TO {role}", "ROLE_HAS_MEMBERSHIP"),
    ],
)
def test_every_guarded_command_refuses_an_app_role_with_a_forbidden_privilege(
    bank_db: BankDatabase, doubles: InProcessDoubles, monkeypatch: pytest.MonkeyPatch, grant: str, code: str
) -> None:
    """SPEC_DECISIONS 72: worker, registry sync and integrity verify refuse; integrity report shows it."""
    with bank_db.connect("admin") as connection:
        connection.execute(
            sql.SQL(grant).format(
                role=sql.Identifier(bank_db.app_role), owner=sql.Identifier(bank_db.target.owner_role)
            )
        )
    cli = cli_for(monkeypatch, {**bank_db.environment(), **unreachable_services(doubles)})
    for command in (("worker", "--once"), ("registry", "sync"), ("integrity", "verify")):
        assert cli(*command).outcome() == (1, "", role_sentence(code) + "\n"), command
    report = cli("integrity", "report", "--json")
    controls = {c["name"]: c["ok"] for c in json.loads(report.out)["database_controls"]["controls"]}
    assert controls["APP_ROLE_GRANTS_EXACT"] is False


def test_the_worker_refuses_to_start_as_a_superuser_whatever_the_server_checks_of_passwords(
    bank_db: BankDatabase, doubles: InProcessDoubles, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SPEC_DECISIONS 68.11: the build server trusts every local connection, so the check reads the
    connected role's attributes and privileges, not which URL was supplied."""
    superuser_url = bank_db.admin_url.rsplit("/", 1)[0] + "/" + bank_db.name
    with psycopg.connect(superuser_url) as connection:
        assert connection.execute(
            "SELECT rolsuper FROM pg_roles WHERE rolname = current_user"
        ).fetchone() == (True,)
    environment = {**bank_db.environment(), "DATABASE_URL": superuser_url, **unreachable_services(doubles)}
    cli = cli_for(monkeypatch, environment)
    assert cli("worker", "--once").outcome() == (1, "", role_sentence("ROLE_IS_SUPERUSER") + "\n")
    assert pairs(bank_db, "SELECT 'audit', count(*)::text FROM audit_log") == {"audit": "0"}


def sentence_of(finished: subprocess.CompletedProcess[str]) -> tuple[int, str, str]:
    """Exit code, the `--json` error code and the one sentence on stderr."""
    _, sentences = split_stderr(finished.stderr)
    document = json.loads(finished.stdout)
    assert sentences == [document["error"]["message"]]
    return finished.returncode, document["error"]["code"], sentences[0]


def test_an_unreachable_database_and_a_refused_connection_are_different_sentences_without_the_url(
    bank_db: BankDatabase,
) -> None:
    """SPEC_DECISIONS 78: the real cause, the variable, never the value."""
    unreachable = "postgresql://someone:hunter2-secret@127.0.0.1:9/nowhere"
    server = bank_db.app_url.rsplit("@", 1)[1].split("/", 1)[0]
    refused = f"postgresql://someone:hunter2-secret@{server}/optest_no_such_database"
    environment = bank_db.environment()
    away = run_program(
        "onepledge-bank", ["db", "status", "--json"], {**environment, "DATABASE_URL": unreachable}
    )
    assert sentence_of(away) == (
        1,
        "DATABASE_UNAVAILABLE",
        "PostgreSQL is not reachable at DATABASE_URL; start it or fix the URL.",
    )
    no = run_program("onepledge-bank", ["db", "status", "--json"], {**environment, "DATABASE_URL": refused})
    assert sentence_of(no) == (
        1, "DATABASE_UNAVAILABLE",
        "PostgreSQL refused the connection of DATABASE_URL: its role, password or database is not set up; "
        "run db provision, or fix the URL.",
    )  # fmt: skip
    assert "hunter2" not in away.stderr + away.stdout + no.stderr + no.stdout


def test_a_database_without_tables_and_a_role_without_a_privilege_are_different_sentences(
    fresh: BankDatabase, small_data: Path, doubles: InProcessDoubles
) -> None:
    """SPEC_DECISIONS 78: the same two causes, the same two sentences, in every command."""
    DatabaseProvisioner(fresh.admin_url).provision(fresh.target)
    missing = (1, "SCHEMA_NOT_CURRENT", "The bank database tables are missing; run db migrate first.")
    seeding = ["seed-reference", "--data-dir", str(small_data), "--json"]
    assert sentence_of(run_program("onepledge-bank", seeding, fresh.environment())) == missing
    runtime = {**fresh.environment(), **unreachable_services(doubles)}
    for command in (
        ["worker", "--once"],
        ["registry", "sync"],
        ["integrity", "verify"],
        ["integrity", "report"],
    ):
        assert sentence_of(run_program("onepledge-bank", [*command, "--json"], runtime)) == missing, command
    migration_runner(fresh).upgrade()
    with fresh.connect("admin") as connection:
        connection.execute(
            sql.SQL("REVOKE SELECT ON alembic_version FROM {}").format(sql.Identifier(fresh.app_role))
        )
    denied = run_program("onepledge-bank", ["db", "status", "--json"], fresh.environment())
    assert sentence_of(denied) == (
        1, "CONFIGURATION_ERROR",
        "The role of DATABASE_URL lacks a privilege this command needs; "
        "run db migrate to restore the grants, or use the role the command expects.",
    )  # fmt: skip


def test_provision_with_an_admin_url_that_is_not_a_superuser_says_so(fresh: BankDatabase) -> None:
    owner_of_other = new_bank_database()
    provision_and_migrate(owner_of_other)
    try:
        environment = {**fresh.environment(), "PG_ADMIN_URL": owner_of_other.owner_url}
        refused = run_program("onepledge-bank", ["db", "provision", "--json"], environment)
    finally:
        drop(owner_of_other)
    assert sentence_of(refused) == (
        1, "CONFIGURATION_ERROR",
        "PG_ADMIN_URL connects as a role that is not a superuser; db provision needs a superuser.",
    )  # fmt: skip


def test_an_unexpected_error_is_one_sentence_and_the_traceback_needs_debug(
    bank_db: BankDatabase,
    doubles: InProcessDoubles,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A defect reaches the last resort handler through an injected collaborator, with no patching;
    it writes one error record with the type and frames before its sentence (SPEC_DECISIONS 78)."""

    def exploding_factory(base_url: str, timeouts: HttpTimeouts) -> httpx.Client:
        raise RuntimeError("1HGCM82633A004352 must never be printed")

    broken = Collaborators(http_client_factory=exploding_factory)
    cli = cli_for(monkeypatch, {**bank_db.environment(), **unreachable_services(doubles)})
    plain = cli("registry", "sync", "--json", collaborators=broken)
    sentence = "Unexpected error. Run with --debug for details."
    assert plain.outcome()[0::2] == (1, sentence + "\n") and "1HGCM82633A004352" not in plain.out
    assert plain.json() == {"ok": False, "error": {"code": "INTERNAL_ERROR", "message": sentence}}
    records, _ = split_stderr(capsys.readouterr().err)
    errors = [r for r in records if r["level"] == "error"]
    assert [(r["event"], r["exception_type"], r["error_code"]) for r in errors] == [
        ("unhandled_exception", "RuntimeError", "INTERNAL_ERROR")
    ]
    assert "stack" in errors[0] and "1HGCM82633A004352" not in json.dumps(records)
    debug = cli("registry", "sync", "--debug", collaborators=broken)
    assert debug.code == 1 and debug.err.endswith("Run with --debug for details.\n")
    trace = debug.err.splitlines()
    assert trace[0] == "Traceback, frames only (most recent call last):"
    assert any(line.endswith(" exploding_factory") for line in trace), "the frames: file, line, function"
    assert "RuntimeError" in trace, "the exception type on a line of its own"
    assert "1HGCM82633A004352" not in debug.err and "must never be printed" not in debug.err
    assert "raise RuntimeError" not in debug.err, "no source line (SPEC_DECISIONS 53.12)"


def test_a_usage_error_exits_2_and_with_json_prints_one_document(bank_db: BankDatabase) -> None:
    usage = (["db"], ["seed-reference"], ["no-such-command"], ["worker", "--twice"])
    for arguments in usage:
        finished = run_program("onepledge-bank", arguments, bank_db.environment())
        assert finished.returncode == 2, arguments
        assert "Traceback" not in finished.stderr and finished.stdout == ""
        as_json = run_program("onepledge-bank", [*arguments, "--json"], bank_db.environment())
        assert as_json.returncode == 2 and "usage:" in as_json.stderr, arguments
        assert json.loads(as_json.stdout)["error"]["code"] == "USAGE", arguments
    helped = run_program("onepledge-bank", ["--help"], bank_db.environment())
    assert helped.returncode == 0 and "seed-reference" in helped.stdout and "worker" in helped.stdout
