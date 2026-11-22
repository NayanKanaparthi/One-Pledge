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

from bank_testkit.data import CONTRACTS, seed_reference
from bank_testkit.database import BankDatabase, pairs
from bank_testkit.doubles import REGISTRY_URL, InProcessDoubles
from bank_testkit.outside import InProcessOutsideRecords
from bank_testkit.pipeline import BankAgentHarness, Services
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
    assert (plain.code, plain.out, plain.err.splitlines()[-1]) == (1, "", sentence)


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
    assert worker.code == 0, worker.err
    assert worker.json()["ok"] is True
