"""Review 3 F2 and F3 and the tests of SPEC_DECISIONS 101, on PostgreSQL, the real registry double and
the command line.

Each test states what is stored, audited and acknowledged, whether the next message is applied,
and the exit codes of `registry sync` and `worker --once`. Before decision 101 an unusable `type`
or id, a page holding `NaN`, or a 404 to an acknowledgement blocked the outbox for good with
nothing recorded, `worker --once` exited 0, and a bank that could no longer see checkpoints went
on submitting; a CASE_UPDATE that matched no alert was acknowledged and left no trace.
"""

from __future__ import annotations

import io
import json
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from bank_testkit.data import seed_reference
from bank_testkit.database import BankDatabase, pairs
from bank_testkit.doubles import REGISTRY_URL, InProcessDoubles
from bank_testkit.memory.clocks import ManualClock
from bank_testkit.outside import InProcessOutsideRecords
from bank_testkit.pipeline import BankAgentHarness, Services
from onepledge_bank_agent.domain.enums import TapeKind, TapeStatus
from onepledge_bank_agent.domain.integrity import SectionStatus
from onepledge_bank_agent.infrastructure.http.base import RETRY_DELAYS_SECONDS
from onepledge_bank_agent.interface.cli.main import main
from onepledge_bank_agent.interface.cli.output import Output

pytestmark = pytest.mark.integration

TAPE = "tapes/HARBOR/S06_HARBOR-LAKESIDE_PLEDGE.csv"
SECOND = "tapes/HARBOR/S07_HARBOR-SUMMIT_PLEDGE.csv"
AS_OF = date(2026, 10, 15)
AT = "2026-11-22T00:00:00Z"
CALL_ATTEMPTS = 1 + len(RETRY_DELAYS_SECONDS)
LIMIT_SECONDS = 300


@dataclass(frozen=True)
class Run:
    code: int
    out: str
    err: str

    def json(self) -> dict[str, Any]:
        document: dict[str, Any] = json.loads(self.out)
        return document


@dataclass
class Bank:
    """A Harbor bank whose first pledge tape completed, its command line and its clock."""

    harness: BankAgentHarness
    database: BankDatabase
    doubles: InProcessDoubles
    clock: ManualClock
    data: Path

    def cli(self, *argv: str) -> Run:
        out, err = io.StringIO(), io.StringIO()
        code = main(list(argv), Output(out, err), self.harness.collaborators)
        return Run(code, out.getvalue(), err.getvalue())

    def count(self, sql: str, *params: object) -> int:
        return int(pairs(self.database, f"SELECT 'n', ({sql})::text", params)["n"])

    def audited(self, action: str) -> int:
        return self.count("SELECT count(*) FROM audit_log WHERE action = %s", action)

    def asset_token(self) -> str:
        return pairs(self.database, "SELECT 't', token FROM loan_tokens WHERE token_kind = 'ASSET' LIMIT 1")[
            "t"
        ]


@pytest.fixture
def bank(
    bank_db: BankDatabase,
    small_data: Path,
    small_outside: InProcessOutsideRecords,
    doubles: InProcessDoubles,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Bank]:
    seed_reference(bank_db, small_data)
    clock = ManualClock(datetime.now(UTC))
    with BankAgentHarness(bank_db, Services(small_outside, doubles), clock=clock) as harness:
        tape = harness.ingest(small_data / TAPE, "HARBOR-LAKESIDE", TapeKind.PLEDGE, AS_OF)
        assert harness.run_until(tape, TapeStatus.COMPLETED) is TapeStatus.COMPLETED
        assert harness.sync().complete
        for name, value in harness.environment.items():
            monkeypatch.setenv(name, value)
        yield Bank(harness, bank_db, doubles, clock, small_data)


def element(kind: object, payload: object, **fields: object) -> dict[str, object]:
    return {"message_id": str(uuid.uuid4()), "position": 0, "type": kind, "created_at": AT,
            "payload": payload, **fields}  # fmt: skip


def alert(case_id: str, token: str) -> dict[str, object]:
    return {"case_id": case_id, "role": "HOLDER", "asset_token": token, "match_kind": "EXACT",
            "match_basis": "ASSET", "kinds_matched": ["ASSET"], "opened_seq": 4}  # fmt: skip


def unmatched_alert() -> dict[str, object]:
    return alert(str(uuid.uuid4()), "d" * 64)


@pytest.mark.parametrize("kind", [None, 7, "", "A" * 65, "ALE\u0000RT"], ids=repr)
def test_f2_a_message_with_an_unusable_type_is_quarantined_and_acknowledged_and_nothing_blocks(
    bank: Bank, kind: object
) -> None:
    poison = element(kind, {"note": "x"})
    bank.doubles.inject_element("HARBOR", poison)
    bank.doubles.inject_message("HARBOR", "ALERT", unmatched_alert())
    for _ in range(3):
        synced = bank.cli("registry", "sync", "--json")
        assert synced.code == 0, synced.err
    held = pairs(
        bank.database,
        "SELECT message_id::text, message_type || ' ' || reason FROM registry_messages_quarantined",
    )
    assert held == {poison["message_id"]: "UNREADABLE SCHEMA_MISMATCH"}
    assert bank.audited("REGISTRY_MESSAGE_QUARANTINED") == 1
    assert bank.audited("ALERT_UNMATCHED") == 1, "the next message was applied"
    assert bank.doubles.forged_pending("HARBOR") == 0, "both acknowledged"
    outcome = pairs(bank.database, "SELECT 'x', outcome || ' ' || ack_status FROM registry_outbox_messages "
                    "WHERE message_id = %s", (poison["message_id"],))  # fmt: skip
    assert outcome == {"x": "QUARANTINED ACKED"}
    worker = bank.cli("worker", "--once", "--json")
    assert (worker.code, worker.json()["ok"]) == (0, True), worker.err


@pytest.mark.parametrize(
    "unusable",
    [
        {"position": 0, "type": "ALERT", "created_at": AT, "payload": {}},
        {"message_id": "not-a-uuid", "position": 0, "type": "ALERT", "created_at": AT, "payload": {}},
        "a string",
    ],
    ids=["no message id", "a message id that is not a UUID", "an element that is a string"],
)
def test_f2_an_element_without_a_usable_id_is_recorded_once_and_the_bank_goes_blind(
    bank: Bank, unusable: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    bank.doubles.inject_element("HARBOR", unusable)
    bank.doubles.inject_message("HARBOR", "ALERT", unmatched_alert())
    runs = [bank.cli("registry", "sync", "--json") for _ in range(3)]
    assert [r.code for r in runs] == [1, 1, 1]
    assert {r.json()["error"]["code"] for r in runs} == {"OUTBOX_SYNC_INCOMPLETE"}
    held = pairs(bank.database, "SELECT reason, count(*)::text FROM registry_messages_quarantined "
                 "WHERE message_id IS NULL GROUP BY 1")  # fmt: skip
    assert held == {"UNACKNOWLEDGEABLE": "1"}, "a redelivery adds nothing"
    assert bank.audited("REGISTRY_MESSAGE_QUARANTINED") == 1
    assert bank.audited("ALERT_UNMATCHED") == 1, "the message after it was applied"
    worker = bank.cli("worker", "--once", "--json")
    assert worker.code == 1
    assert "OUTBOX_SYNC_INCOMPLETE" in [p["code"] for p in worker.json()["problems"]]
    bank.clock.advance(LIMIT_SECONDS + 1)
    assert bank.cli("registry", "sync", "--json").code == 1
    assert bank.audited("REGISTRY_OUTBOX_BLIND") == 1
    report = bank.harness.report().registry_messages
    assert report.status is SectionStatus.BROKEN and report.outbox is not None and report.outbox.blind


@pytest.mark.parametrize("page", [b'{"messages": [NaN], "has_more": false}', b"[]"], ids=["NaN", "an array"])
def test_f2_a_page_that_cannot_be_read_is_recorded_and_makes_the_bank_blind(bank: Bank, page: bytes) -> None:
    bank.doubles.inject_page("HARBOR", page, times=3)  # two syncs and the worker's
    quarantined = bank.count("SELECT count(*) FROM registry_messages_quarantined")
    runs = [bank.cli("registry", "sync", "--json") for _ in range(2)]
    assert [(r.code, r.json()["error"]["code"]) for r in runs] == [(1, "DEPENDENCY_REJECTED")] * 2
    assert bank.count("SELECT count(*) FROM registry_messages_quarantined") == quarantined
    state = pairs(bank.database, "SELECT 'x', last_reason FROM registry_outbox_state")
    assert state == {"x": "MALFORMED_RESPONSE"}
    assert bank.audited("REGISTRY_SYNC_REQUESTED") == 2
    bank.clock.advance(LIMIT_SECONDS + 1)
    worker = bank.cli("worker", "--once", "--json")
    assert worker.code == 1
    codes = [p["code"] for p in worker.json()["problems"]]
    assert codes[:2] == ["OUTBOX_SYNC_INCOMPLETE", "REGISTRY_INTEGRITY_BROKEN"], codes
    assert bank.audited("REGISTRY_OUTBOX_BLIND") == 1
    synced = bank.cli("registry", "sync", "--json")
    assert synced.code == 0, "the next readable page ends it"
    assert bank.audited("REGISTRY_OUTBOX_RECOVERED") == 1


def test_f2_an_acknowledgement_answered_404_is_recorded_once_and_the_message_is_handled_once(
    bank: Bank,
) -> None:
    future = element("FUTURE_TYPE", {"note": "x"})
    bank.doubles.inject_element("HARBOR", future)
    bank.doubles.script_ack(str(future["message_id"]), 404)
    runs = [bank.cli("registry", "sync", "--json") for _ in range(7)]
    assert {(r.code, r.json()["error"]["code"]) for r in runs} == {(1, "OUTBOX_SYNC_INCOMPLETE")}
    assert bank.audited("REGISTRY_MESSAGE_IGNORED") == 1, "never handled twice"
    assert bank.audited("REGISTRY_ACK_REFUSED") == 1, "recorded once"
    outcome = pairs(bank.database, "SELECT 'x', outcome || ' ' || ack_status FROM registry_outbox_messages "
                    "WHERE message_id = %s", (future["message_id"],))  # fmt: skip
    assert outcome == {"x": "IGNORED REFUSED"}


def test_an_acknowledgement_answered_503_three_times_then_204_is_taken_and_handled_once(bank: Bank) -> None:
    future = element("FUTURE_TYPE", {"note": "x"})
    bank.doubles.inject_element("HARBOR", future)
    bank.doubles.script_ack(str(future["message_id"]), 503, 503, 503, 204)
    codes = [bank.cli("registry", "sync", "--json").code for _ in range(1 + 3 // CALL_ATTEMPTS)]
    assert codes[-1] == 0 and bank.doubles.forged_pending("HARBOR") == 0
    assert bank.audited("REGISTRY_MESSAGE_IGNORED") == 1
    assert bank.audited("REGISTRY_ACK_REFUSED") == 0


def test_a_ledger_outage_of_ten_syncs_defers_the_checkpoint_blinds_the_bank_and_one_good_sync_restores_it(
    bank: Bank,
) -> None:
    bank.doubles.control(REGISTRY_URL, "POST", "/_test/checkpoint")
    bank.doubles.fail_path("/v1/ledger/events", 503, 11 * CALL_ATTEMPTS)  # ten syncs and the catch-up
    seen = bank.count("SELECT count(*) FROM checkpoints_seen")
    for _ in range(10):
        bank.doubles.inject_message("HARBOR", "ALERT", unmatched_alert())
        result = bank.harness.sync()
        assert (result.complete, result.reason) == (False, "REGISTRY_UNAVAILABLE")
        assert bank.doubles.forged_pending("HARBOR") == 0, "alerts behind the checkpoint are delivered"
        bank.clock.advance(60)
    assert bank.audited("ALERT_UNMATCHED") == 10
    assert bank.count("SELECT count(*) FROM registry_messages_quarantined") == 0, "a deferral is not poison"
    assert bank.count("SELECT count(*) FROM checkpoints_seen") == seen
    assert bank.audited("REGISTRY_OUTBOX_BLIND") == 1
    second = bank.harness.ingest(bank.data / SECOND, "HARBOR-SUMMIT", TapeKind.PLEDGE, AS_OF)
    submissions = sum(1 for r in bank.doubles.sent if r.path == "/v1/submissions")
    assert bank.harness.run_until(second, TapeStatus.COMPLETED) is TapeStatus.FAILED
    failure = pairs(bank.database, "SELECT 'x', failure_code FROM tape_submissions WHERE submission_id = %s",
                    (second.submission_id,))  # fmt: skip
    assert failure == {"x": "REGISTRY_INTEGRITY_BROKEN"}, "it ran every stage up to submit"
    assert sum(1 for r in bank.doubles.sent if r.path == "/v1/submissions") == submissions, "nothing sent"
    result = bank.harness.sync()
    assert result.complete and result.checkpoints_verified >= 1, "one good sync restores everything"
    assert bank.audited("REGISTRY_OUTBOX_RECOVERED") == 1
    assert bank.harness.report().registry_messages.status is SectionStatus.OK
    assert bank.harness.retry(second).status is TapeStatus.TOKENIZED
    assert bank.harness.run_until(second, TapeStatus.COMPLETED) is TapeStatus.COMPLETED, "no permanent halt"


def test_f3_a_case_update_before_its_alert_is_kept_and_applied_when_the_alert_arrives(bank: Bank) -> None:
    token, case_id = bank.asset_token(), str(uuid.uuid4())
    update = {"case_id": case_id, "asset_token": token, "status": "CONFIRMED", "resolved_seq": 600}
    bank.doubles.inject_message("HARBOR", "CASE_UPDATE", update)
    synced = bank.cli("registry", "sync", "--json")
    assert synced.code == 0, synced.err
    held = pairs(bank.database, "SELECT reason, quarantine_id::text FROM registry_messages_quarantined")
    assert list(held) == ["UNMATCHED"]
    audited = pairs(bank.database, "SELECT action, outcome || ' ' || object_id::text FROM audit_log "
                    "WHERE action = 'CASE_UPDATE_UNMATCHED'")  # fmt: skip
    assert audited == {"CASE_UPDATE_UNMATCHED": f"FAILURE {held['UNMATCHED']}"}
    assert bank.doubles.forged_pending("HARBOR") == 0
    bank.doubles.inject_message("HARBOR", "ALERT", alert(case_id, token))
    assert bank.cli("registry", "sync", "--json").code == 0
    stored = pairs(bank.database, "SELECT 'x', status || ' ' || case_status FROM alerts "
                   "WHERE registry_case_id = %s", (case_id,))  # fmt: skip
    assert stored == {"x": "ESCALATED CONFIRMED"}
    late = bank.count("SELECT count(*) FROM audit_log WHERE action = 'ALERT_CASE_UPDATED' "
                      "AND detail->>'from_quarantine_id' = %s", held["UNMATCHED"])  # fmt: skip
    assert late == 1
    assert bank.count("SELECT count(*) FROM registry_outbox_messages WHERE outcome = 'APPLIED_LATE'") == 1
    report = bank.cli("integrity", "report", "--json").json()
    assert report["registry_messages"]["status"] == "OK", "no open quarantined row"


def test_f2_a_bank_blind_behind_an_unacknowledgeable_element_does_not_release_to_a_rewritten_registry(
    bank: Bank,
) -> None:
    bank.doubles.inject_element("HARBOR", "no id")
    assert not bank.harness.sync().complete
    bank.clock.advance(LIMIT_SECONDS + 1)
    bank.doubles.tamper(mode="REWRITE_HISTORY", seq=1)
    loans = [uuid.UUID(v) for v in pairs(bank.database, "SELECT loan_id::text, loan_id::text FROM loans "
                                          "WHERE state = 'LOCKED' LIMIT 3")]  # fmt: skip
    release = bank.harness.release(bank.harness.facility_id("HARBOR-LAKESIDE"), loans, AS_OF)
    submissions = sum(1 for r in bank.doubles.sent if r.path == "/v1/submissions")
    assert bank.harness.run_until(release, TapeStatus.COMPLETED) is TapeStatus.FAILED
    failure = pairs(bank.database, "SELECT 'x', failure_code FROM tape_submissions WHERE submission_id = %s",
                    (release.submission_id,))  # fmt: skip
    assert failure == {"x": "REGISTRY_INTEGRITY_BROKEN"}
    assert sum(1 for r in bank.doubles.sent if r.path == "/v1/submissions") == submissions, "nothing sent"


def test_y15_a_handler_write_refused_by_a_trigger_is_poison_refused_by_database_with_its_sqlstate(
    bank: Bank,
) -> None:
    """Decision 101, last test: kills mutation 14 (a store refusal treated as a deferral)."""
    with bank.database.connect("admin") as connection:
        connection.execute(
            "CREATE FUNCTION optest_refuse_alert() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
            "RAISE EXCEPTION 'refused' USING ERRCODE = '23514'; END $$"
        )
        connection.execute(
            "CREATE TRIGGER optest_refuse_alert BEFORE INSERT ON alerts "
            "FOR EACH ROW EXECUTE FUNCTION optest_refuse_alert()"
        )
        connection.execute(
            "REVOKE EXECUTE ON FUNCTION optest_refuse_alert() FROM PUBLIC"
        )  # as a migration would
    try:
        refused = alert(str(uuid.uuid4()), bank.asset_token())
        bank.doubles.inject_message("HARBOR", "ALERT", refused)
        bank.doubles.inject_message("HARBOR", "ALERT", unmatched_alert())
        synced = bank.cli("registry", "sync", "--json")
        assert synced.code == 0, synced.err
    finally:
        with bank.database.connect("admin") as connection:
            connection.execute("DROP TRIGGER optest_refuse_alert ON alerts")
            connection.execute("DROP FUNCTION optest_refuse_alert()")
    held = pairs(bank.database, "SELECT reason, message_type FROM registry_messages_quarantined")
    assert held == {"REFUSED_BY_DATABASE": "ALERT"}
    detail = pairs(bank.database, "SELECT 'x', detail->>'sqlstate' FROM audit_log "
                   "WHERE action = 'REGISTRY_MESSAGE_QUARANTINED'")  # fmt: skip
    assert detail == {"x": "23514"}
    assert bank.audited("ALERT_UNMATCHED") == 1, "the next message was applied"
    assert bank.doubles.forged_pending("HARBOR") == 0
