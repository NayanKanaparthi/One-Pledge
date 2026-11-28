"""`POST /v1/evaluate` in process: every answer is a schema of `contracts/key_holder.yaml`.

The body sent is the one `KeyHolderHttpClient.evaluate` builds: `request_id`, `key_version`
null and `elements`. Each WP2 edge case is checked for its status, its code, and that the
body holds only fields the contract defines and repeats nothing that was sent.
"""

from __future__ import annotations

import base64
import json
import re
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
import pytest
import yaml
from jsonschema import Draft202012Validator
from onepledge_key_holder.interface.wire import MESSAGES

if TYPE_CHECKING:
    from conftest import Harness

pytestmark = [pytest.mark.unit, pytest.mark.contract]

REPOSITORY = Path(__file__).resolve().parents[2]
CONTRACT = yaml.safe_load((REPOSITORY / "contracts" / "key_holder.yaml").read_text(encoding="utf-8"))
EXAMPLE = json.loads(
    (REPOSITORY / "contracts" / "examples" / "key_holder" / "evaluate.request.json").read_text(
        encoding="utf-8"
    )
)
REQUEST_ID = "1f0e5f7c-7f3a-4b8e-9d55-2c1f0a9b3e77"
IDENTITY = base64.b64encode(bytes(32)).decode()
CANARY = "ZQXJ7CANARY9"
# What the bank's client accepts (`key_holder_client.py`): these keys, this element pattern.
BANK_ELEMENT = re.compile(r"^[A-Za-z0-9+/]{43}=$")
ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"


def validator(schema: str) -> Draft202012Validator:
    return Draft202012Validator(
        {"$ref": f"#/components/schemas/{schema}", "components": CONTRACT["components"]}
    )


def check_error(response: httpx.Response, status: int, code: str) -> dict[str, Any]:
    """The response is the contract's envelope with this status and code, and echoes its request id."""
    assert response.status_code == status
    body = response.json()
    validator("ErrorResponse").validate(body)
    assert str(status) in CONTRACT["paths"]["/v1/evaluate"]["post"]["responses"]
    error: dict[str, Any] = body["error"]
    assert error["code"] == code
    assert response.headers["x-request-id"] == error["request_id"]
    assert "location" not in response.headers and "content-encoding" not in response.headers
    return error


def body(elements: object, **extra: object) -> dict[str, object]:
    return {"request_id": REQUEST_ID, "key_version": None, "elements": elements, **extra}


def test_the_body_the_bank_builds_is_answered_with_the_evaluate_response(
    make_holder: Callable[..., Harness],
) -> None:
    holder = make_holder(holder_id="kh2", version=4)
    response = holder.post(body(EXAMPLE["elements"]))
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert "content-encoding" not in response.headers
    answer = response.json()
    validator("EvaluateResponse").validate(answer)
    # Exactly the four fields of the schema, which are the four the bank's strict model reads.
    assert set(answer) == {"request_id", "holder_id", "key_version", "elements"}
    assert (answer["request_id"], answer["holder_id"], answer["key_version"]) == (REQUEST_ID, "kh2", 4)
    assert len(answer["elements"]) == len(EXAMPLE["elements"])
    assert all(BANK_ELEMENT.fullmatch(element) for element in answer["elements"])
    assert all(base64.b64decode(element) != bytes(32) for element in answer["elements"])


def test_key_version_may_be_omitted_or_pinned_to_the_active_one(
    make_holder: Callable[..., Harness], point: Callable[[bytes], str]
) -> None:
    holder = make_holder()
    element = point(b"x")
    omitted = holder.post({"request_id": REQUEST_ID, "elements": [element]})
    pinned = holder.post(body([element], key_version=1))
    assert omitted.status_code == pinned.status_code == 200
    assert omitted.json() == pinned.json()


def test_a_second_call_with_the_same_points_returns_the_same_elements(
    make_holder: Callable[..., Harness], point: Callable[[bytes], str]
) -> None:
    holder = make_holder()
    elements = [point(b"one"), point(b"two")]
    first, second = holder.evaluate(elements), holder.evaluate(elements)
    assert first.json()["elements"] == second.json()["elements"]
    assert first.json()["request_id"] != second.json()["request_id"]
    assert len(holder.journal.records) == 2


def test_a_missing_or_unknown_key_is_401(
    make_holder: Callable[..., Harness], point: Callable[[bytes], str]
) -> None:
    holder = make_holder()
    payload = body([point(b"x")])
    missing = holder.api.request("POST", "/v1/evaluate", json_body=payload)
    unknown = holder.post(payload, **{"X-API-Key": "opk_unknown"})
    empty = holder.post(payload, **{"X-API-Key": ""})
    for response in (missing, unknown, empty):
        check_error(response, 401, "UNAUTHENTICATED")
    assert holder.journal.records == []


def test_a_duplicate_api_key_header_is_401_even_when_both_are_valid(
    make_holder: Callable[..., Harness], point: Callable[[bytes], str]
) -> None:
    holder = make_holder()
    headers = [("X-API-Key", holder.api_key), ("X-API-Key", holder.api_key)]
    response = holder.api.request("POST", "/v1/evaluate", json_body=body([point(b"x")]), headers=headers)
    check_error(response, 401, "UNAUTHENTICATED")
    assert holder.journal.records == []


def test_authentication_comes_before_every_other_check(make_holder: Callable[..., Harness]) -> None:
    holder = make_holder()
    too_large = holder.api.request("POST", "/v1/evaluate", content=b"x" * 1_100_000)
    not_json = holder.api.request("POST", "/v1/evaluate", content=b"{")
    for response in (too_large, not_json):
        check_error(response, 401, "UNAUTHENTICATED")


def test_the_identity_point_is_422_invalid_element_by_index(make_holder: Callable[..., Harness]) -> None:
    holder = make_holder()
    error = check_error(holder.post(body([IDENTITY])), 422, "INVALID_ELEMENT")
    assert error["details"] == [{"field": "elements[0]", "issue": "NOT_A_VALID_POINT"}]
    assert error["message"] == "One or more elements are not valid group elements."
    assert holder.journal.records == []


def test_one_bad_element_among_good_ones_returns_no_partial_result(
    make_holder: Callable[..., Harness], point: Callable[[bytes], str]
) -> None:
    holder = make_holder()
    good = point(b"loan")
    not_a_point = base64.b64encode(bytes([255]) * 32).decode()
    response = holder.post(body([good, good, good, not_a_point, good, IDENTITY]))
    error = check_error(response, 422, "INVALID_ELEMENT")
    assert error["details"] == [
        {"field": "elements[3]", "issue": "NOT_A_VALID_POINT"},
        {"field": "elements[5]", "issue": "NOT_A_VALID_POINT"},
    ]
    assert "elements" not in response.json()
    assert good not in response.text and not_a_point not in response.text
    assert holder.journal.records == [] and holder.journal.used == {}


def test_non_canonical_base64_is_422_invalid_element(
    make_holder: Callable[..., Harness], point: Callable[[bytes], str]
) -> None:
    holder = make_holder()
    good = point(b"loan")
    spelled = good[:42] + ALPHABET[ALPHABET.index(good[42]) + 1] + "="
    assert BANK_ELEMENT.fullmatch(spelled) and base64.b64decode(spelled) == base64.b64decode(good)
    error = check_error(holder.post(body([good, spelled])), 422, "INVALID_ELEMENT")
    assert error["details"] == [{"field": "elements[1]", "issue": "NOT_A_VALID_POINT"}]


def test_an_unknown_pinned_key_version_is_409(
    make_holder: Callable[..., Harness], point: Callable[[bytes], str]
) -> None:
    holder = make_holder()
    error = check_error(holder.post(body([point(b"x")], key_version=9)), 409, "KEY_VERSION_UNAVAILABLE")
    assert "details" not in error
    assert holder.journal.records == []


def test_the_daily_cap_is_429_with_retry_after(
    make_holder: Callable[..., Harness], point: Callable[[bytes], str]
) -> None:
    holder = make_holder(cap=10)
    six = [point(bytes([index])) for index in range(6)]
    assert holder.post(body(six)).status_code == 200
    limited = holder.post(body(six))
    check_error(limited, 429, "RATE_LIMITED")
    assert limited.headers["retry-after"] == "43200"
    assert int(limited.headers["retry-after"]) >= 1
    assert [record.item_count for record in holder.journal.records] == [6]
    # Four more still fit: the refused request used nothing.
    assert holder.post(body(six[:4])).status_code == 200


def test_1001_elements_is_422_validation_error_too_many_items(
    make_holder: Callable[..., Harness], point: Callable[[bytes], str]
) -> None:
    holder = make_holder()
    element = point(b"x")
    assert holder.post(body([element] * 1000)).status_code == 200
    error = check_error(holder.post(body([element] * 1001)), 422, "VALIDATION_ERROR")
    assert error["details"] == [{"field": "elements", "issue": "TOO_MANY_ITEMS"}]
    assert [record.item_count for record in holder.journal.records] == [1000]


def test_a_body_over_the_size_limit_is_413(make_holder: Callable[..., Harness]) -> None:
    holder = make_holder()
    headers = {"X-API-Key": holder.api_key, "Content-Type": "application/json"}
    response = holder.api.request("POST", "/v1/evaluate", content=b"x" * 1_100_000, headers=headers)
    check_error(response, 413, "PAYLOAD_TOO_LARGE")


@pytest.mark.parametrize(
    ("payload", "details"),
    [
        ({"request_id": REQUEST_ID}, [("elements", "REQUIRED")]),
        ({"elements": []}, [("request_id", "REQUIRED"), ("elements", "TOO_FEW_ITEMS")]),
        (body("nope"), [("elements", "INVALID_TYPE")]),
        (body([CANARY]), [("elements[0]", "TOO_SHORT")]),
        (body([CANARY * 4]), [("elements[0]", "TOO_LONG")]),
        (body(["*" * 43 + "="]), [("elements[0]", "PATTERN_MISMATCH")]),
        (body([7]), [("elements[0]", "INVALID_TYPE")]),
        (body([IDENTITY], request_id=CANARY), [("request_id", "PATTERN_MISMATCH")]),
        (body([IDENTITY], request_id=REQUEST_ID.upper()), [("request_id", "PATTERN_MISMATCH")]),
        (body([IDENTITY], request_id=7), [("request_id", "INVALID_TYPE")]),
        (body([IDENTITY], key_version=0), [("key_version", "OUT_OF_RANGE")]),
        (body([IDENTITY], key_version=2_147_483_648), [("key_version", "OUT_OF_RANGE")]),
        (body([IDENTITY], key_version="1"), [("key_version", "INVALID_TYPE")]),
        (body([IDENTITY], key_version=True), [("key_version", "INVALID_TYPE")]),
        (body([IDENTITY], key_version=1.5), [("key_version", "INVALID_TYPE")]),
        (body([IDENTITY], **{CANARY: 1}), [("body", "UNKNOWN_FIELD")]),
        ([body([IDENTITY])], [("body", "INVALID_JSON")]),
    ],
)
def test_a_body_outside_the_schema_is_422_naming_fields_and_issues_only(
    make_holder: Callable[..., Harness], payload: object, details: list[tuple[str, str]]
) -> None:
    holder = make_holder()
    response = holder.post(payload)
    error = check_error(response, 422, "VALIDATION_ERROR")
    assert [(detail["field"], detail["issue"]) for detail in error["details"]] == details
    assert CANARY not in response.text
    assert holder.journal.records == []


@pytest.mark.parametrize(
    "raw", [b"{", b"", b"\xff\xfe", b'{"request_id": NaN}', b'{"elements": [], "elements": []}']
)
def test_a_body_that_is_not_one_json_object_is_422(make_holder: Callable[..., Harness], raw: bytes) -> None:
    holder = make_holder()
    headers = {"X-API-Key": holder.api_key, "Content-Type": "application/json"}
    error = check_error(
        holder.api.request("POST", "/v1/evaluate", content=raw, headers=headers), 422, "VALIDATION_ERROR"
    )
    assert error["details"] == [{"field": "body", "issue": "INVALID_JSON"}]


def test_a_body_without_a_json_content_type_is_422(
    make_holder: Callable[..., Harness], point: Callable[[bytes], str]
) -> None:
    holder = make_holder()
    raw = json.dumps(body([point(b"x")])).encode()
    plain = holder.api.request(
        "POST",
        "/v1/evaluate",
        content=raw,
        headers={"X-API-Key": holder.api_key, "Content-Type": "text/plain"},
    )
    error = check_error(plain, 422, "VALIDATION_ERROR")
    assert error["details"] == [{"field": "Content-Type", "issue": "INVALID_VALUE"}]
    with_charset = holder.api.request(
        "POST",
        "/v1/evaluate",
        content=raw,
        headers={"X-API-Key": holder.api_key, "Content-Type": "application/json; charset=utf-8"},
    )
    assert with_charset.status_code == 200


def test_a_query_parameter_is_422_and_is_never_named(
    make_holder: Callable[..., Harness], point: Callable[[bytes], str]
) -> None:
    holder = make_holder()
    response = holder.api.request(
        "POST",
        f"/v1/evaluate?{CANARY}=1",
        json_body=body([point(b"x")]),
        headers={"X-API-Key": holder.api_key},
    )
    error = check_error(response, 422, "VALIDATION_ERROR")
    assert error["details"] == [{"field": "query", "issue": "UNKNOWN_PARAMETER"}]
    assert CANARY not in response.text


def test_the_request_id_header_is_echoed_when_canonical_and_replaced_otherwise(
    make_holder: Callable[..., Harness], point: Callable[[bytes], str]
) -> None:
    holder = make_holder()
    correlation = str(uuid.uuid4())
    ok = holder.post(body([point(b"x")]), **{"X-Request-ID": correlation})
    assert ok.headers["x-request-id"] == correlation
    # The header is a correlation id (decision 2): the body's request_id is what the answer echoes.
    assert ok.json()["request_id"] == REQUEST_ID
    refused = holder.api.request("POST", "/v1/evaluate", headers={"X-Request-ID": correlation})
    assert check_error(refused, 401, "UNAUTHENTICATED")["request_id"] == correlation
    replaced = holder.api.request("GET", "/healthz", headers={"X-Request-ID": CANARY})
    assert replaced.headers["x-request-id"] != CANARY
    assert str(uuid.UUID(replaced.headers["x-request-id"])) == replaced.headers["x-request-id"]


def test_a_repeated_request_id_header_is_422_duplicate(make_holder: Callable[..., Harness]) -> None:
    holder = make_holder()
    headers = [("X-Request-ID", str(uuid.uuid4())), ("X-Request-ID", str(uuid.uuid4()))]
    response = holder.api.request("GET", "/healthz", headers=headers)
    assert response.status_code == 422
    validator("ErrorResponse").validate(response.json())
    assert response.json()["error"]["details"] == [{"field": "X-Request-ID", "issue": "DUPLICATE"}]


def test_an_unknown_route_and_a_wrong_method_use_the_envelope(make_holder: Callable[..., Harness]) -> None:
    holder = make_holder()
    for path in ("/nowhere", "/v1/evaluate/", "/_test/stats", "/v1"):
        response = holder.api.request("GET", path)
        assert response.status_code == 404 and "location" not in response.headers
        validator("ErrorResponse").validate(response.json())
        assert response.json()["error"]["code"] == "NOT_FOUND"
    wrong = holder.api.request("GET", "/v1/evaluate")
    assert wrong.status_code == 405 and wrong.headers["allow"] == "POST"
    validator("ErrorResponse").validate(wrong.json())
    assert wrong.json()["error"]["code"] == "METHOD_NOT_ALLOWED"


class BrokenJournal:
    """A journal whose database fails in a way no code expected."""

    def accept(self, record: object, window_start: object, cap: int) -> bool:
        del record, window_start, cap
        raise RuntimeError(CANARY)


def test_an_unexpected_failure_is_the_500_envelope_and_says_nothing_about_it(
    make_holder: Callable[..., Harness], point: Callable[[bytes], str]
) -> None:
    holder = make_holder()
    vars(holder.use_cases.evaluate)["_journal"] = BrokenJournal()
    response = holder.post(body([point(b"x")]))
    assert response.status_code == 500
    validator("ErrorResponse").validate(response.json())
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"
    assert CANARY not in response.text and "elements" not in response.json()


def test_every_error_code_this_service_uses_is_in_the_contract_enum() -> None:
    codes = CONTRACT["components"]["schemas"]["ErrorResponse"]["properties"]["error"]["properties"]["code"][
        "enum"
    ]
    assert set(MESSAGES) == set(codes)
