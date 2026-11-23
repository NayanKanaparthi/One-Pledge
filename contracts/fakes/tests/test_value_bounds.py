"""The round-trip bounds of SPEC_DECISIONS 100 on what the doubles store or return.

A value the doubles keep or answer must lie inside the contract, the PostgreSQL column type
and the Python type that reads it: timestamps from 2000-01-01T00:00:00Z up to but not
including 2100-01-01, key versions inside an `int` column, text no longer than 255
characters where the contract gives no bound. Each bound is probed just inside and just
outside.
"""

from __future__ import annotations

import base64
import hashlib
import json
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import oprf
import pytest

from onepledge_contract_fakes.bounds import INT_MAX, MAX_TEXT, TIMESTAMP_END, TIMESTAMP_START
from onepledge_contract_fakes.clock import rfc3339
from onepledge_contract_fakes.errors import ConfigurationError, InvariantViolationError
from onepledge_contract_fakes.key_holder.app import create_key_holder_app
from onepledge_contract_fakes.key_holder.settings import KeyHolderSettings
from onepledge_contract_fakes.registry.app import create_registry_app
from onepledge_contract_fakes.registry.model import FIELD_KINDS
from onepledge_contract_fakes.testing import (
    AsgiTestClient,
    FixedClock,
    key_holder_config,
    new_api_key,
    new_members,
    new_scalar,
    registry_config,
)

CONTROL_KEY = "b" * 40


@pytest.mark.unit
@pytest.mark.parametrize(
    "moment",
    [TIMESTAMP_START, TIMESTAMP_END - timedelta(microseconds=1), datetime(2026, 11, 23, tzinfo=UTC)],
)
def test_a_timestamp_inside_the_bounds_is_written(moment: datetime) -> None:
    assert rfc3339(moment).endswith("Z")


@pytest.mark.unit
@pytest.mark.parametrize(
    "moment",
    [
        TIMESTAMP_START - timedelta(microseconds=1),
        TIMESTAMP_END,
        datetime(9999, 12, 31, 23, 59, 59, tzinfo=UTC),
        datetime(2000, 1, 1, 0, 0, 0, tzinfo=timezone(timedelta(hours=12))),
        datetime(2026, 1, 1),
    ],
    ids=["before 2000", "2100", "year 9999", "offset pushes before 2000", "no offset"],
)
def test_a_timestamp_outside_the_bounds_is_never_written(moment: datetime) -> None:
    with pytest.raises(InvariantViolationError):
        rfc3339(moment)


@pytest.fixture
def key_holder() -> Iterator[tuple[AsgiTestClient, str]]:
    members, keys = new_members(1)
    config = key_holder_config("kh1", members, daily_element_cap=10, test_control_key=CONTROL_KEY)
    with AsgiTestClient(create_key_holder_app(config, FixedClock())) as client:
        yield client, keys[0]


def _evaluate(key_version: int) -> dict[str, object]:
    element = base64.b64encode(bytes(oprf.data.hash(b"p"))).decode()
    return {"request_id": str(uuid.uuid4()), "key_version": key_version, "elements": [element]}


@pytest.mark.contract
@pytest.mark.parametrize(("key_version", "status"), [(INT_MAX, 409), (INT_MAX + 1, 422), (2**63, 422)])
def test_a_pinned_key_version_is_held_to_an_int_column(
    key_holder: tuple[AsgiTestClient, str], key_version: int, status: int
) -> None:
    client, key = key_holder
    response = client.post("/v1/evaluate", json=_evaluate(key_version), headers={"X-API-Key": key})
    assert response.status_code == status
    if status == 422:
        assert response.json()["error"]["details"] == [{"field": "key_version", "issue": "OUT_OF_RANGE"}]


@pytest.mark.unit
@pytest.mark.parametrize(("version", "accepted"), [(INT_MAX, True), (INT_MAX + 1, False)])
def test_key_versions_in_the_settings_are_held_to_an_int_column(version: int, accepted: bool) -> None:
    members, _ = new_members(1)
    environment = {
        "HOLDER_ID": "kh1",
        "PORT": "18301",
        "FAKE_KH_KEYS": json.dumps({str(version): base64.b64encode(new_scalar()).decode()}),
        "FAKE_KH_ACTIVE_VERSION": str(version),
        "FAKE_KH_MEMBERS": json.dumps(
            [{"member_id": m.member_id, "api_key_sha256": m.api_key_sha256} for m in members]
        ),
    }
    if accepted:
        assert KeyHolderSettings.from_env(environment).active_version == INT_MAX
    else:
        with pytest.raises(ConfigurationError):
            KeyHolderSettings.from_env(environment)


@pytest.fixture
def registry() -> Iterator[tuple[AsgiTestClient, str]]:
    members, keys = new_members(1)
    app = create_registry_app(
        registry_config(members, new_api_key(), test_control_key=CONTROL_KEY), FixedClock()
    )
    with AsgiTestClient(app) as client:
        yield client, keys[0]


def _submission(key_version: str) -> dict[str, Any]:
    def code(text: str) -> str:
        return hashlib.sha256(text.encode()).hexdigest()

    item = {
        "item_ref": str(uuid.uuid4()),
        "asset_token": code("asset"),
        "field_tokens": {kind: code(kind) for kind in FIELD_KINDS},
    }
    return {"schema_version": 1, "kind": "PLEDGE", "key_version": key_version, "items": [item]}


@pytest.mark.contract
@pytest.mark.parametrize(
    ("key_version", "issue"),
    [
        (f"{INT_MAX + 1}.1", "OUT_OF_RANGE"),
        (f"1.{INT_MAX + 1}", "OUT_OF_RANGE"),
        ("1." + "1" * (MAX_TEXT - 1), "TOO_LONG"),
    ],
)
def test_a_submitted_key_version_is_held_to_int_parts_and_the_text_bound(
    registry: tuple[AsgiTestClient, str], key_version: str, issue: str
) -> None:
    client, key = registry
    response = client.post(
        "/v1/submissions",
        json=_submission(key_version),
        headers={"X-API-Key": key, "Idempotency-Key": "a" * 64},
    )
    assert response.status_code == 422
    assert response.json()["error"]["details"] == [{"field": "key_version", "issue": issue}]


@pytest.mark.contract
def test_a_key_version_at_the_bounds_is_compared_not_refused(registry: tuple[AsgiTestClient, str]) -> None:
    client, key = registry
    response = client.post(
        "/v1/submissions",
        json=_submission(f"{INT_MAX}.{INT_MAX}"),
        headers={"X-API-Key": key, "Idempotency-Key": "b" * 64},
    )
    assert response.status_code != 422


@pytest.mark.contract
def test_a_tamper_certificate_id_must_be_a_canonical_uuid(registry: tuple[AsgiTestClient, str]) -> None:
    client, _ = registry
    response = client.post(
        "/_test/tamper",
        json={"mode": "CERTIFICATE_BODY", "certificate_id": "x" * 5000},
        headers={"X-Test-Control-Key": CONTROL_KEY},
    )
    assert response.status_code == 422
    assert response.json()["error"]["details"] == [{"field": "certificate_id", "issue": "PATTERN_MISMATCH"}]
