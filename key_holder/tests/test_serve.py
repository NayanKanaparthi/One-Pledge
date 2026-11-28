"""`onepledge-keyholder` as processes: provision, migrate, serve two holders, call them as the bank does.

The calls go over TCP with the body and the headers `KeyHolderHttpClient` sends. Holder 2
receives holder 1's elements unchanged, and unblinding its answer gives `k2 * k1 * P`.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

import oprf
import pytest

from onepledge_testtools.network import LOOPBACK_IPV4, free_port
from onepledge_testtools.processes import run, start, stop

if TYPE_CHECKING:
    from conftest import ScratchDatabase

pytestmark = pytest.mark.integration

PROGRAM = str(Path(sys.executable).parent / "onepledge-keyholder")
BOOT_SECONDS = 15.0


def environment(**values: str) -> dict[str, str]:
    base = {name: os.environ[name] for name in ("PATH", "SYSTEMROOT") if name in os.environ}
    return {**base, **values}


def b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def wait_for_health(port: int) -> dict[str, Any]:
    deadline = time.monotonic() + BOOT_SECONDS
    url = f"http://{LOOPBACK_IPV4}:{port}/healthz"
    while True:
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                body: dict[str, Any] = json.loads(response.read())
                return body
        except (urllib.error.URLError, ConnectionError):
            if time.monotonic() > deadline:
                raise
            time.sleep(0.1)


@pytest.fixture
def served() -> Iterator[list[Any]]:
    processes: list[Any] = []
    yield processes
    for process in processes:
        stop(process)


def serve(served: list[Any], database: ScratchDatabase, holder: str, scalar: bytes, **extra: str) -> int:
    """Start one holder on a free port with only the variables `serve` reads."""
    port = free_port()
    variables = {
        "HOLDER_ID": holder,
        "ACTIVE_KEY_VERSION": "1",
        "OPRF_SCALAR_B64": b64(scalar),
        "DATABASE_URL": database.app_url,
        "PORT": str(port),
        **extra,
    }
    served.append(start([PROGRAM, "serve"], environment(**variables)))
    return port


def evaluate(port: int, api_key: str, elements: list[str]) -> tuple[int, dict[str, str], dict[str, Any], str]:
    """One `POST /v1/evaluate` as the bank's client sends it; the request id it used is returned."""
    request_id = str(uuid.uuid4())
    payload = {"request_id": request_id, "key_version": None, "elements": elements}
    request = urllib.request.Request(
        f"http://{LOOPBACK_IPV4}:{port}/v1/evaluate",
        data=json.dumps(payload).encode(),
        method="POST",
        headers={
            "X-API-Key": api_key,
            "Accept": "application/json",
            "Accept-Encoding": "identity",
            "Content-Type": "application/json",
            "X-Request-ID": str(uuid.uuid4()),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310 - a loopback http URL built by this test
            return response.status, dict(response.headers), json.loads(response.read()), request_id
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), json.loads(error.read()), request_id


@pytest.mark.parametrize("holder", ["kh1", "kh2"])
def test_serve_boots_and_reports_its_identity(
    served: list[Any], database: ScratchDatabase, holder: str
) -> None:
    port = serve(served, database, holder, bytes(oprf.mask.random()))
    assert wait_for_health(port) == {
        "status": "ok",
        "service": "key_holder",
        "holder_id": holder,
        "active_key_version": 1,
    }


def test_two_holders_made_by_the_command_line_answer_the_bank_s_two_calls(
    served: list[Any], make_fresh_database: Callable[[], ScratchDatabase]
) -> None:
    databases = [make_fresh_database(), make_fresh_database()]
    scalars = [bytes(oprf.mask.random()), bytes(oprf.mask.random())]
    for database in databases:
        settings = environment(**database.environment(ACTIVE_KEY_VERSION="1"))
        provisioned = run([PROGRAM, "db", "provision"], settings)
        assert (provisioned.returncode, provisioned.stderr) == (0, "")
        name = database.target.database
        assert provisioned.stdout == f"Provisioned database {name} with roles {name}_owner and {name}_app.\n"
        migrated = run([PROGRAM, "db", "migrate"], settings)
        assert (migrated.returncode, migrated.stderr) == (0, "")
        assert migrated.stdout == (
            "Migrated the key holder database to revision 0002_grants. Key version 1 is active. "
            "Active member credentials: 1.\n"
        )
        # Both commands are safe to repeat.
        assert run([PROGRAM, "db", "provision"], settings).returncode == 0
        assert run([PROGRAM, "db", "migrate"], settings).returncode == 0
    ports = [
        serve(served, databases[0], "kh1", scalars[0]),
        serve(served, databases[1], "kh2", scalars[1]),
    ]
    assert [wait_for_health(port)["holder_id"] for port in ports] == ["kh1", "kh2"]

    inputs = [b"asset", b"vin", b"borrower"]
    points = [oprf.data.hash(value) for value in inputs]
    direct = [bytes(oprf.mask(scalars[1]).mask(oprf.mask(scalars[0]).mask(p))) for p in points]
    sent: list[str] = []
    for _ in range(2):
        blinds = [oprf.mask.random() for _ in points]
        blinded = [blind.mask(p).to_base64() for blind, p in zip(blinds, points, strict=True)]
        status_1, headers_1, first, id_1 = evaluate(ports[0], databases[0].api_key, blinded)
        status_2, headers_2, second, id_2 = evaluate(ports[1], databases[1].api_key, first["elements"])
        assert (status_1, status_2) == (200, 200)
        for headers in (headers_1, headers_2):
            assert headers["content-type"] == "application/json"
            assert "Content-Encoding" not in headers and "Location" not in headers
        assert first == {
            "request_id": id_1,
            "holder_id": "kh1",
            "key_version": 1,
            "elements": first["elements"],
        }
        assert second == {
            "request_id": id_2,
            "holder_id": "kh2",
            "key_version": 1,
            "elements": second["elements"],
        }
        assert len(first["elements"]) == len(second["elements"]) == len(blinded)
        unblinded = [
            bytes(blind.unmask(oprf.data.from_base64(element)))
            for blind, element in zip(blinds, second["elements"], strict=True)
        ]
        # The blind drops out: the bank gets k2 * k1 * P whatever blind it drew.
        assert unblinded == direct
        sent += [*blinded, *first["elements"], *second["elements"]]

    # A key of the other holder's database is unknown here: each holder has its own credentials.
    refused = evaluate(ports[0], "opk_unknown", [points[0].to_base64()])
    assert (refused[0], refused[2]["error"]["code"]) == (401, "UNAUTHENTICATED")

    for database in databases:
        logged = database.rows("SELECT member_id::text, item_count FROM evaluation_log ORDER BY at")
        assert logged == [(database.member_id, 3), (database.member_id, 3)]
    output = "".join("".join(stop(process)) for process in served)
    records = [json.loads(line) for line in output.splitlines()]
    requests = [r for r in records if r["event"] == "http_request" and r["route"] == "/v1/evaluate"]
    assert sorted((r["holder_id"], r["http_status"]) for r in requests) == [
        ("kh1", 200),
        ("kh1", 200),
        ("kh1", 401),
        ("kh2", 200),
        ("kh2", 200),
    ]
    assert all(set(r) <= LOG_KEYS for r in records)
    for secret in (*sent, *(d.api_key for d in databases), *(b64(s) for s in scalars)):
        assert secret not in output


LOG_KEYS = {
    "event",
    "level",
    "timestamp",
    "service",
    "holder_id",
    "method",
    "route",
    "http_status",
    "duration_ms",
    "request_id",
    "exception_type",
}


def test_serve_refuses_a_bad_holder_id_in_one_sentence_without_the_value() -> None:
    result = run([PROGRAM, "serve"], environment(HOLDER_ID="kh9", ACTIVE_KEY_VERSION="1"))
    assert (result.returncode, result.stdout, result.stderr) == (1, "", "HOLDER_ID must be kh1 or kh2.\n")


def test_serve_refuses_a_host_name_for_bind_host() -> None:
    result = run(
        [PROGRAM, "serve"], environment(HOLDER_ID="kh1", ACTIVE_KEY_VERSION="1", BIND_HOST="localhost")
    )
    assert result.returncode == 1
    assert result.stderr.startswith("BIND_HOST must be an IPv4 or IPv6 literal")
    assert "localhost" not in result.stderr


@pytest.mark.parametrize("scalar", [bytes(32), b"short", bytes(33)])
def test_serve_refuses_a_scalar_that_is_not_32_bytes_or_is_all_zeros(
    database: ScratchDatabase, scalar: bytes
) -> None:
    variables = environment(
        HOLDER_ID="kh1", ACTIVE_KEY_VERSION="1", DATABASE_URL=database.app_url, OPRF_SCALAR_B64=b64(scalar)
    )
    result = run([PROGRAM, "serve"], variables)
    assert (result.returncode, result.stdout) == (1, "")
    assert (
        result.stderr == "OPRF_SCALAR_B64 must be the standard base64 of 32 bytes that are not all zeros.\n"
    )


def test_serve_refuses_a_database_that_is_not_migrated_or_records_another_key_version(
    database: ScratchDatabase, fresh_database: ScratchDatabase
) -> None:
    scalar = b64(bytes(oprf.mask.random()))
    base = {"HOLDER_ID": "kh1", "OPRF_SCALAR_B64": scalar}
    other_version = run(
        [PROGRAM, "serve"], environment(**base, ACTIVE_KEY_VERSION="2", DATABASE_URL=database.app_url)
    )
    assert other_version.returncode == 1
    assert other_version.stderr.startswith("ACTIVE_KEY_VERSION is not the active key version recorded")
    missing = run(
        [PROGRAM, "serve"], environment(**base, ACTIVE_KEY_VERSION="1", DATABASE_URL=fresh_database.app_url)
    )
    assert missing.returncode == 1
    assert missing.stderr.startswith(
        "The database of DATABASE_URL is not reachable or has no key holder tables"
    )
    assert "secret" not in missing.stderr and fresh_database.app_url not in missing.stderr
    no_url = run([PROGRAM, "serve"], environment(**base, ACTIVE_KEY_VERSION="1"))
    assert (no_url.returncode, no_url.stderr) == (1, "DATABASE_URL is required.\n")


def test_db_migrate_before_db_provision_says_what_to_run(fresh_database: ScratchDatabase) -> None:
    result = run(
        [PROGRAM, "db", "migrate"], environment(**fresh_database.environment(ACTIVE_KEY_VERSION="1"))
    )
    assert (result.returncode, result.stdout) == (1, "")
    assert "Run db provision first" in result.stderr
    assert fresh_database.owner_url not in result.stderr


@pytest.mark.parametrize("arguments", [[], ["db"], ["db", "seed"], ["serve", "--port", "1"]])
def test_a_missing_or_unknown_command_is_a_usage_error(arguments: list[str]) -> None:
    result = run([PROGRAM, *arguments], environment())
    assert result.returncode == 2
