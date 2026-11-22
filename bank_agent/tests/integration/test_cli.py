"""`onepledge-bank` (SPEC 19): db provision, migrate, status, seed-reference and worker, by an operator.

Most tests call `main()` in process with the variables set in the environment; the
tests about exit codes and stderr run the installed program as a child process.
"""

from __future__ import annotations

import hashlib
import io
import json
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
from bank_testkit.database import BankDatabase, drop, new_bank_database, pairs
from bank_testkit.doubles import InProcessDoubles
from bank_testkit.outside import InProcessOutsideRecords, ScriptedOutsideRecords
from bank_testkit.pipeline import BankAgentHarness, Services
from bank_testkit.programs import run_program
from onepledge_bank_agent.domain.enums import TapeKind
from onepledge_bank_agent.infrastructure.db.migrations import MigrationRunner, head_revision
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
        "PG_ADMIN_URL", "MIGRATION_DATABASE_URL", "DATABASE_URL", "DB_APP_ROLE", "BANK_CODE", "LOG_LEVEL",
        "OUTSIDE_RECORDS_URL", "OUTSIDE_RECORDS_API_KEY", *REGISTRY_SIDE,
    ):  # fmt: skip
        monkeypatch.delenv(name, raising=False)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)

    def invoke(*argv: str, collaborators: Collaborators | None = None) -> Run:
        out, err = io.StringIO(), io.StringIO()
        code = main(list(argv), Output(out, err), collaborators)
        return Run(code, out.getvalue(), err.getvalue())

    return invoke


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
    assert first == Run(
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
    MigrationRunner(bank_db.owner_url, bank_db.app_role).downgrade("0002_guards")
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
    MigrationRunner(bank_db.owner_url, bank_db.app_role).downgrade("0002_guards")
    versions = Path(__file__).resolve().parents[2] / "migrations" / "versions"
    expected = sorted(path.stem for path in versions.glob("0*.py") if path.stem > "0002_guards")
    finished = run_program("onepledge-bank", ["db", "migrate", "--json"], bank_db.environment())
    assert finished.returncode == 0, finished.stderr
    records, sentences = log_lines_and_sentences(finished.stderr)
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
    seeded = cli("seed-reference", "--data-dir", str(example_data), "--json")
    stored = pairs(bank_db, "SELECT 'lenders', count(*)::text FROM lenders UNION ALL "
                   "SELECT 'facilities', count(*)::text FROM facilities")  # fmt: skip
    digest = hashlib.sha256((example_data / "reference.json").read_bytes()).hexdigest()
    lenders, facilities = int(stored["lenders"]), int(stored["facilities"])
    assert seeded.code == 0 and facilities > 0
    assert seeded.json() == {
        "ok": True, "bank_code": "HARBOR", "source_sha256": digest,
        "lenders": {"inserted": lenders, "updated": 0, "unchanged": 0},
        "facilities": {"inserted": facilities, "updated": 0, "unchanged": 0},
    }  # fmt: skip
    text = cli("seed-reference", "--data-dir", str(example_data)).out.splitlines()
    assert text[0] == "Reference data of HARBOR"
    assert text[1].split() == ["table", "inserted", "updated", "unchanged"]
    assert text[2].split() == ["lenders", "0", "0", str(lenders)]
    assert text[-2:] == [
        "2 tables seeded",
        f"Lenders and facilities are up to date (reference.json {digest[:12]}...).",
    ]
    entries = audit_actions(bank_db)
    assert [(a, o, t) for a, o, t, _ in entries] == [
        ("REFERENCE_DATA_SEEDED", "REFERENCE_DATA", "SYSTEM")
    ] * 2
    assert entries[1][3] == {
        "via": "cli", "source_sha256": digest,
        "lenders": {"inserted": 0, "updated": 0, "unchanged": lenders},
        "facilities": {"inserted": 0, "updated": 0, "unchanged": facilities},
    }  # fmt: skip
    assert first_broken_seq(bank_db) is None
    missing = cli("seed-reference", "--data-dir", str(example_data / "nowhere"), "--json")
    assert (
        missing.code == 1
        and missing.err == f"cannot read {example_data / 'nowhere'}/reference.json: no such file.\n"
    )
    assert missing.json() == {
        "ok": False,
        "error": {"code": "REFERENCE_DATA_INVALID", "message": missing.err.strip()},
    }
    assert len(audit_actions(bank_db)) == 2, "a refused file is not audited"


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
        report = cli("worker", "--once", "--json", collaborators=harness.collaborators)
        idle = cli("worker", "--once", "--quiet", collaborators=harness.collaborators)
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
    assert cli("worker", "--once") == Run(1, "", sentence + "\n")
    refused = cli("worker", "--once", "--json")
    assert refused.json() == {"ok": False, "error": {"code": "SCHEMA_NOT_CURRENT", "message": sentence}}


def log_lines_and_sentences(stderr: str) -> tuple[list[dict[str, object]], list[str]]:
    """stderr split into JSON log records (each must parse as an object) and plain sentences."""
    records, sentences = [], []
    for line in stderr.splitlines():
        if line.startswith("{"):
            record = json.loads(line)
            assert isinstance(record, dict)
            records.append(record)
        else:
            sentences.append(line)
    return records, sentences


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
    assert finished.returncode == 0, finished.stderr
    document = json.loads(finished.stdout)
    assert document["ok"] is True and [s["outcome"] for s in document["stages"]] == [
        "ADVANCED",
        "RETRY_SCHEDULED",
    ]
    records, sentences = log_lines_and_sentences(finished.stderr)
    assert sentences == [] and records
    assert {record["service"] for record in records} == {"bank_agent"}
    assert {"tape_stage_advanced", "tape_stage_attempt_failed"} <= {str(r["event"]) for r in records}
    quiet = run_program("onepledge-bank", ["worker", "--once", "--json", "--quiet"], environment)
    quiet_records, _ = log_lines_and_sentences(quiet.stderr)
    assert json.loads(quiet.stdout)["ok"] is True
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
    ):
        finished = run_program(
            "onepledge-bank", command, {k: v for k, v in environment.items() if k != missing}
        )
        _, sentences = log_lines_and_sentences(finished.stderr)
        assert (finished.returncode, finished.stdout, sentences) == (
            1,
            "",
            [f"Configuration error: {missing} is required but not set."],
        ), command
        as_json = run_program(
            "onepledge-bank", [*command, "--json"], {k: v for k, v in environment.items() if k != missing}
        )
        assert json.loads(as_json.stdout)["error"]["code"] == "CONFIGURATION_ERROR"


def test_the_worker_never_needs_an_owner_credential(
    bank_db: BankDatabase, small_data: Path, doubles: InProcessDoubles
) -> None:
    """The worker runs from an environment without MIGRATION_DATABASE_URL, PG_ADMIN_URL or DB_APP_ROLE."""
    seed_reference(bank_db, small_data)
    runtime_only = {"DATABASE_URL": bank_db.app_url, "BANK_CODE": "HARBOR", **unreachable_services(doubles)}
    finished = run_program("onepledge-bank", ["worker", "--once", "--json"], runtime_only)
    assert json.loads(finished.stdout) == {"ok": True, "stages": []}


def role_sentence(code: str) -> str:
    """The refusal of SPEC_DECISIONS 72: the variable and the first violation code."""
    return f"DATABASE_URL connects as a role that is not least-privilege ({code}); use the app role."


def test_the_worker_refuses_to_start_as_the_owner_role(
    bank_db: BankDatabase, doubles: InProcessDoubles
) -> None:
    """SPEC_DECISIONS 43.4: as the owner, the worker exits 1 with one line naming DATABASE_URL."""
    as_owner = {"DATABASE_URL": bank_db.owner_url, "BANK_CODE": "HARBOR", **unreachable_services(doubles)}
    finished = run_program("onepledge-bank", ["worker", "--once", "--json"], as_owner)
    assert finished.returncode == 1
    assert json.loads(finished.stdout) == {
        "ok": False,
        "error": {"code": "ROLE_NOT_LEAST_PRIVILEGE", "message": role_sentence("ROLE_OWNS_OBJECT")},
    }
    assert log_lines_and_sentences(finished.stderr)[1] == [role_sentence("ROLE_OWNS_OBJECT")]
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
        ("GRANT UPDATE (detail) ON audit_log TO {role}", "TABLE_PRIVILEGE_EXTRA"),
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
        assert cli(*command) == Run(1, "", role_sentence(code) + "\n"), command
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
    assert cli("worker", "--once") == Run(1, "", role_sentence("ROLE_IS_SUPERUSER") + "\n")
    assert pairs(bank_db, "SELECT 'audit', count(*)::text FROM audit_log") == {"audit": "0"}


def test_an_unreachable_database_is_one_sentence_without_the_url(bank_db: BankDatabase) -> None:
    unreachable_url = "postgresql://someone:hunter2-secret@127.0.0.1:9/nowhere"
    finished = run_program(
        "onepledge-bank",
        ["db", "status", "--json"],
        {**bank_db.environment(), "DATABASE_URL": unreachable_url},
    )
    sentence = "PostgreSQL refused the connection at DATABASE_URL; run db provision first."
    assert finished.returncode == 1
    assert json.loads(finished.stdout) == {
        "ok": False,
        "error": {"code": "DATABASE_UNAVAILABLE", "message": sentence},
    }
    assert log_lines_and_sentences(finished.stderr)[1] == [sentence]
    assert "hunter2" not in finished.stderr + finished.stdout


def test_an_unexpected_error_is_one_sentence_and_the_traceback_needs_debug(
    bank_db: BankDatabase, doubles: InProcessDoubles, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A defect reaches the last resort handler through an injected collaborator, with no patching."""

    def exploding_factory(base_url: str, timeouts: HttpTimeouts) -> httpx.Client:
        raise RuntimeError("1HGCM82633A004352 must never be printed")

    broken = Collaborators(http_client_factory=exploding_factory)
    cli = cli_for(monkeypatch, {**bank_db.environment(), **unreachable_services(doubles)})
    plain = cli("registry", "sync", "--json", collaborators=broken)
    sentence = "Unexpected error. Run with --debug for details."
    assert plain.code == 1 and plain.err == sentence + "\n" and "1HGCM82633A004352" not in plain.out
    assert plain.json() == {"ok": False, "error": {"code": "INTERNAL_ERROR", "message": sentence}}
    debug = cli("registry", "sync", "--debug", collaborators=broken)
    assert debug.code == 1 and debug.err.endswith("Run with --debug for details.\n")
    trace = debug.err.splitlines()
    assert trace[0] == "Traceback, frames only (most recent call last):"
    assert any(line.endswith(" exploding_factory") for line in trace), "the frames: file, line, function"
    assert "RuntimeError" in trace, "the exception type on a line of its own"
    assert "1HGCM82633A004352" not in debug.err and "must never be printed" not in debug.err
    assert "raise RuntimeError" not in debug.err, "no source line (SPEC_DECISIONS 53.12)"


def test_a_usage_error_exits_2(bank_db: BankDatabase) -> None:
    usage = (["db"], ["seed-reference"], ["no-such-command"], ["worker", "--twice"], ["worker", "--json"])
    for arguments in usage:
        finished = run_program("onepledge-bank", arguments, bank_db.environment())
        assert finished.returncode == 2, arguments
        assert "Traceback" not in finished.stderr and finished.stdout == ""
    helped = run_program("onepledge-bank", ["--help"], bank_db.environment())
    assert helped.returncode == 0 and "seed-reference" in helped.stdout and "worker" in helped.stdout
