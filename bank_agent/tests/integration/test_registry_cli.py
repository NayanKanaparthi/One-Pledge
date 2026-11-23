"""`onepledge-bank registry sync`, `integrity report` and `integrity verify` by an operator (SPEC 19).

`main()` runs in process with the bank agent's variables in the environment and the
harness's collaborators, so its HTTP clients reach the in-process doubles. The JSON
report validates against `IntegrityReport` in `bank_agent.yaml`.
"""

from __future__ import annotations

import io
import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import pytest
import yaml
from jsonschema import Draft202012Validator
from psycopg import sql

from bank_testkit.data import CONTRACTS, seed_reference
from bank_testkit.database import BankDatabase, pairs
from bank_testkit.doubles import REGISTRY_URL, InProcessDoubles
from bank_testkit.outside import InProcessOutsideRecords
from bank_testkit.pipeline import BankAgentHarness, Services
from bank_testkit.programs import unwrap
from onepledge_bank_agent.domain.enums import TapeKind
from onepledge_bank_agent.interface.cli.main import main
from onepledge_bank_agent.interface.cli.output import Output

pytestmark = pytest.mark.integration


@dataclass
class Run:
    code: int
    out: str
    err: str

    def json(self) -> dict[str, Any]:
        document: dict[str, Any] = json.loads(self.out)
        return document


type Cli = Callable[..., Run]


@pytest.fixture
def cli(
    bank_db: BankDatabase,
    small_data: Path,
    small_outside: InProcessOutsideRecords,
    doubles: InProcessDoubles,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Cli]:
    seed_reference(bank_db, small_data)
    with BankAgentHarness(bank_db, Services(small_outside, doubles)) as harness:
        tape = small_data / "tapes/HARBOR/S06_HARBOR-LAKESIDE_PLEDGE.csv"
        harness.ingest(tape, "HARBOR-LAKESIDE", TapeKind.PLEDGE, date(2026, 10, 15))
        harness.run_worker()
        for name, value in harness.environment.items():
            monkeypatch.setenv(name, value)

        def invoke(*argv: str) -> Run:
            out, err = io.StringIO(), io.StringIO()
            code = main(list(argv), Output(out, err), harness.collaborators)
            return Run(code, out.getvalue(), err.getvalue())

        yield invoke


def report_validator() -> Draft202012Validator:
    document = yaml.safe_load((CONTRACTS / "bank_agent.yaml").read_text(encoding="utf-8"))
    schema = {"$ref": "#/components/schemas/IntegrityReport", "components": document["components"]}
    return Draft202012Validator(schema)


def test_registry_sync_processes_pending_messages_and_is_audited_as_cli(
    cli: Cli, bank_db: BankDatabase, doubles: InProcessDoubles
) -> None:
    doubles.control(REGISTRY_URL, "POST", "/_test/checkpoint")
    synced = cli("registry", "sync", "--json")
    assert synced.code == 0, synced.err
    document = synced.json()
    assert document["ok"] is True and document["skipped"] is False
    assert document["checkpoints_verified"] >= 1 and document["checkpoints_failed"] == 0
    plain = cli("registry", "sync")
    assert plain.code == 0 and "Registry sync" in plain.out and "messages processed" in plain.out
    audited = "SELECT action, detail->>'via' FROM audit_log WHERE action = 'REGISTRY_SYNC_REQUESTED'"
    assert pairs(bank_db, audited) == {"REGISTRY_SYNC_REQUESTED": "cli"}


def test_integrity_report_and_verify_print_the_sections_and_a_contract_document(
    cli: Cli, bank_db: BankDatabase
) -> None:
    before = cli("integrity", "report", "--json")
    assert before.code == 0, before.err
    document = before.json()
    assert document.pop("ok") is True
    assert list(report_validator().iter_errors(document)) == []
    assert document["audit_log"]["status"] == "NOT_VERIFIED"
    verified = cli("integrity", "verify", "--json")
    assert verified.code == 0, verified.err
    deep = verified.json()
    assert deep.pop("ok") is True and deep["status"] == "OK"
    assert list(report_validator().iter_errors(deep)) == []
    plain = cli("integrity", "report")
    assert plain.code == 0
    for section in ("registry_chain", "certificates", "audit_log", "raw_data", "database_controls"):
        assert section in plain.out
    assert "Traceback" not in plain.out + plain.err
    run = "SELECT action, detail->>'via' FROM audit_log WHERE action = 'INTEGRITY_VERIFICATION_RUN'"
    assert pairs(bank_db, run) == {"INTEGRITY_VERIFICATION_RUN": "cli"}


def test_integrity_verify_without_the_registry_is_one_sentence_and_stores_nothing(
    cli: Cli, bank_db: BankDatabase, doubles: InProcessDoubles
) -> None:
    doubles.down.add(REGISTRY_URL)
    failed = cli("integrity", "verify", "--json")
    assert failed.code == 1
    assert failed.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"
    assert pairs(bank_db, "SELECT 'runs', count(*)::text FROM integrity_runs") == {"runs": "0"}


def test_a_registry_that_refuses_the_credentials_is_dependency_rejected(
    cli: Cli, bank_db: BankDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A permanent dependency failure is DEPENDENCY_REJECTED with its fixed sentence (SPEC_DECISIONS 54)."""
    monkeypatch.setenv("REGISTRY_API_KEY", "opk_" + "x" * 43)
    sentence = (
        "A service this action depends on refused the request. Nothing was changed. "
        "Check the service credentials and configuration."
    )
    refused = cli("registry", "sync", "--json")
    assert refused.code == 1
    assert refused.json() == {"ok": False, "error": {"code": "DEPENDENCY_REJECTED", "message": sentence}}
    plain = cli("registry", "sync")
    assert (plain.code, plain.out, unwrap(plain.err).splitlines()[-1]) == (1, "", sentence)


def test_a_poison_checkpoint_neither_fails_registry_sync_nor_starves_worker_once(
    cli: Cli, bank_db: BankDatabase, small_data: Path, doubles: InProcessDoubles
) -> None:
    """Review 2, F1, by the command line: before decision 85 every sync and `worker --once` exited 1."""
    body = {
        "schema_version": 1, "checkpoint_seq": 2**63, "last_event_seq": 9000, "head_hash": "a" * 64,
        "signed_at": "2026-11-22T00:00:00Z",
    }  # fmt: skip
    payload = {"body": body, "signature": "A" * 86 + "==", "signing_key_id": "registry-local-1"}
    doubles.inject_message("HARBOR", "CHECKPOINT", payload)
    for _ in range(3):
        synced = cli("registry", "sync", "--json")
        assert synced.code == 0, synced.err
    assert pairs(bank_db, "SELECT reason, count(*)::text FROM registry_messages_quarantined GROUP BY 1") == {
        "VALUE_OUT_OF_RANGE": "1"
    }
    assert doubles.forged_pending("HARBOR") == 0
    worker = cli("worker", "--once", "--json")
    assert worker.code == 1, "SPEC_DECISIONS 101: a quarantined CHECKPOINT halts the bank for good"
    assert [problem["code"] for problem in worker.json()["problems"]] == ["REGISTRY_INTEGRITY_BROKEN"]


def section_line(text: str, name: str) -> list[str]:
    """The words of the Sections table row of one section."""
    return next(line.split() for line in text.splitlines() if line.split()[:1] == [name])


def test_integrity_report_and_verify_exit_1_when_broken_and_0_when_ok(
    cli: Cli, bank_db: BankDatabase
) -> None:
    """SPEC_DECISIONS 78, 93 item 8: the same exit rule for both, in text and in JSON."""
    assert [cli("integrity", "verify").code, cli("integrity", "report", "--json").code] == [0, 0]
    with bank_db.connect("admin") as connection:
        connection.execute("ALTER TABLE audit_log DISABLE TRIGGER ALL")
        connection.execute("UPDATE audit_log SET detail = detail || '{\"x\": 1}' WHERE seq = 3")
        connection.execute("ALTER TABLE audit_log ENABLE TRIGGER ALL")
    verify, report = cli("integrity", "verify", "--json"), cli("integrity", "report")
    assert (verify.code, verify.json()["status"], verify.json()["audit_log"]["first_bad_seq"]) == (
        1,
        "BROKEN",
        3,
    )
    assert report.code == 1 and section_line(report.out, "audit_log")[:2] == ["audit_log", "BROKEN"]
    assert cli("integrity", "verify").code == 1


def test_integrity_report_shows_a_schema_behind_the_code_instead_of_refusing(
    cli: Cli, bank_db: BankDatabase
) -> None:
    """Review 2, quality finding 8: the report is exempt from the head check, verify is not."""
    with bank_db.connect("owner") as connection:
        connection.execute("UPDATE alembic_version SET version_num = '0003_grants'")
    report = cli("integrity", "report", "--json")
    assert report.code == 1, report.err
    controls = {c["name"]: c["ok"] for c in report.json()["database_controls"]["controls"]}
    assert [name for name, ok in controls.items() if not ok] == ["SCHEMA_AT_MIGRATION_HEAD"]
    plain = cli("integrity", "report")
    assert plain.code == 1 and "SCHEMA_AT_MIGRATION_HEAD" in plain.out and plain.err == ""
    refused = cli("integrity", "verify", "--json")
    assert (refused.code, refused.json()["error"]["code"]) == (1, "SCHEMA_NOT_CURRENT")


def test_a_section_whose_query_fails_is_not_verified_with_the_reason_and_the_rest_is_printed(
    cli: Cli, bank_db: BankDatabase
) -> None:
    with bank_db.connect("owner") as connection:
        connection.execute("UPDATE alembic_version SET version_num = '0003_grants'")
        connection.execute("ALTER TABLE registry_messages_quarantined RENAME COLUMN reason TO reason_before")
    report = cli("integrity", "report", "--json")
    assert report.code == 1, report.err
    document = report.json()
    assert document.pop("ok") is True
    assert list(report_validator().iter_errors(document)) == []
    assert document["registry_messages"] == {"status": "NOT_VERIFIED", "quarantined": 0}
    assert document["certificates"]["status"] == "OK" and document["certificates"]["total"] >= 1
    plain = cli("integrity", "report")
    assert section_line(plain.out, "registry_messages")[:2] == ["registry_messages", "NOT_VERIFIED"]
    assert "SQLSTATE 42703" in plain.out and "certificates" in plain.out
    assert "Traceback" not in plain.out + plain.err


def test_integrity_report_shows_a_role_that_cannot_read_what_it_reports_on(
    cli: Cli, bank_db: BankDatabase
) -> None:
    """SPEC_DECISIONS 72, 93 item 8: a privilege taken from the app role is a false control and an
    unread section in a printed report, never a refusal of the whole report."""
    with bank_db.connect("admin") as connection:
        connection.execute(
            sql.SQL("REVOKE SELECT ON alembic_version, registry_messages_quarantined FROM {}").format(
                sql.Identifier(bank_db.app_role)
            )
        )
    report = cli("integrity", "report", "--json")
    assert report.code == 1, report.err
    document = report.json()
    controls = {c["name"]: c["ok"] for c in document["database_controls"]["controls"]}
    assert controls["SCHEMA_AT_MIGRATION_HEAD"] is False and controls["APP_ROLE_GRANTS_EXACT"] is False
    assert document["registry_messages"]["status"] == "NOT_VERIFIED"
    plain = cli("integrity", "report")
    assert plain.code == 1 and plain.err == ""
    assert "TABLE_PRIVILEGE_MISSING" in plain.out and "SQLSTATE 42501" in plain.out
