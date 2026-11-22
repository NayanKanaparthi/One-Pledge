"""SPEC 21.4: every request the bank agent's key holder and registry clients send validates against
`key_holder.yaml` and `registry.yaml`: a contract route and method, every required parameter present,
every path, query and header parameter and every JSON body valid against its schema.

The requests are the ones the clients really sent during the small replay for three banks, a case
position, a resubmission and a deep verification, recorded at the transport as they left the client.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml
from jsonschema import Draft202012Validator

from bank_testkit.banks import ThreeBanks
from bank_testkit.data import CONTRACTS
from bank_testkit.database import BankDatabase, drop, new_bank_database, provision_and_migrate
from bank_testkit.doubles import KEY_HOLDER_URLS, REGISTRY_URL, SentRequest
from bank_testkit.outside import InProcessOutsideRecords
from onepledge_bank_agent.application.review_alert import ReviewAction
from onepledge_bank_agent.domain.enums import BankPosition

pytestmark = pytest.mark.contract

CONTRACT_OF = {REGISTRY_URL: "registry.yaml", **dict.fromkeys(KEY_HOLDER_URLS.values(), "key_holder.yaml")}


@dataclass(frozen=True)
class Operation:
    template: str
    method: str
    spec: dict[str, Any]


class Contract:
    """One OpenAPI document: its operations and validators for its schemas."""

    def __init__(self, path: Path) -> None:
        self.document: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
        self.operations = [
            Operation(template, method.upper(), operation)
            for template, item in self.document["paths"].items()
            for method, operation in item.items()
            if method in ("get", "post", "put", "patch", "delete")
        ]

    def operation(self, method: str, path: str) -> Operation | None:
        for operation in self.operations:
            pattern = "^" + re.sub(r"\{[^}]+\}", "[^/]+", operation.template) + "$"
            if operation.method == method and re.match(pattern, path):
                return operation
        return None

    def resolve(self, node: dict[str, Any]) -> dict[str, Any]:
        while "$ref" in node:
            found: Any = self.document
            for part in node["$ref"].removeprefix("#/").split("/"):
                found = found[part]
            node = found
        return node

    def validator(self, schema: dict[str, Any]) -> Draft202012Validator:
        return Draft202012Validator({**schema, "components": self.document["components"]})


def problems(contract: Contract, request: SentRequest) -> list[str]:
    """Every way the request departs from its contract (empty when it validates)."""
    operation = contract.operation(request.method, request.path)
    if operation is None:
        return [f"{request.method} {request.path} is not a contract route"]
    found: list[str] = []
    headers = {name.lower(): value for name, value in request.headers.items()}
    segments = dict(zip(operation.template.split("/"), request.path.split("/"), strict=True))
    for parameter in map(contract.resolve, operation.spec.get("parameters", [])):
        name, where = parameter["name"], parameter["in"]
        value = {
            "query": request.query.get(name),
            "header": headers.get(name.lower()),
            "path": segments.get("{" + name + "}"),
        }[where]
        if value is None:
            if parameter.get("required"):
                found.append(f"{where} parameter {name} is missing")
            continue
        schema = contract.resolve(parameter["schema"])
        typed: object = (
            int(value) if schema.get("type") == "integer" and value.lstrip("-").isdigit() else value
        )
        found += [
            f"{where} {name}: {error.message}" for error in contract.validator(schema).iter_errors(typed)
        ]
    body_spec = operation.spec.get("requestBody")
    if body_spec is not None:
        schema = body_spec["content"]["application/json"]["schema"]
        document = json.loads(request.body)
        found += [f"body: {error.message}" for error in contract.validator(schema).iter_errors(document)]
    elif request.body:
        found.append("a body was sent to an operation without one")
    return found


@pytest.fixture(scope="module")
def sent(small_data: Path, small_outside: InProcessOutsideRecords) -> Iterator[list[SentRequest]]:
    made: list[BankDatabase] = []

    def new_database() -> BankDatabase:
        database = new_bank_database()
        provision_and_migrate(database)
        made.append(database)
        return database

    try:
        with ThreeBanks(new_database, small_data, small_outside) as banks:
            banks.replay()
            with banks.databases["NORTHGATE"].connect("admin") as connection:
                row = connection.execute("SELECT alert_id FROM alerts ORDER BY alert_id LIMIT 1").fetchone()
            assert row is not None and isinstance(row[0], uuid.UUID)
            northgate = banks.agents["NORTHGATE"]
            northgate.review(row[0], ReviewAction.START_REVIEW)
            northgate.review(row[0], ReviewAction.SUBMIT_POSITION, BankPosition.DATA_ERROR)
            for agent in banks.agents.values():
                agent.verify()
            yield list(banks.doubles.sent)
    finally:
        for database in made:
            drop(database)


def test_every_key_holder_and_registry_request_validates_against_its_contract(
    sent: list[SentRequest],
) -> None:
    contracts = {url: Contract(CONTRACTS / name) for url, name in CONTRACT_OF.items()}
    found = (contracts[r.base_url].operation(r.method, r.path) for r in sent)
    assert {operation.template for operation in found if operation is not None} >= {
        "/v1/evaluate", "/v1/submissions", "/v1/certificates/{certificate_id}", "/v1/outbox",
        "/v1/outbox/{message_id}/ack", "/v1/cases/{case_id}/resolution", "/v1/ledger/events",
    }  # fmt: skip
    failures = [(r.method, r.path, problem) for r in sent for problem in problems(contracts[r.base_url], r)]
    assert failures == []


def test_the_contract_check_itself_finds_a_bad_request() -> None:
    contract = Contract(CONTRACTS / "registry.yaml")
    bad = SentRequest(REGISTRY_URL, "POST", "/v1/submissions", {}, {}, b'{"kind": "PLEDGE"}')
    found = problems(contract, bad)
    assert any("Idempotency-Key" in problem for problem in found)
    assert any(problem.startswith("body:") for problem in found)
    assert problems(contract, SentRequest(REGISTRY_URL, "GET", "/v1/nowhere", {}, {}, b"")) == [
        "GET /v1/nowhere is not a contract route"
    ]
