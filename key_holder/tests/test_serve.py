"""`onepledge-keyholder serve` as a process: it boots, answers `/healthz` over TCP, refuses bad settings."""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from onepledge_testtools.network import LOOPBACK_IPV4, free_port
from onepledge_testtools.processes import run, start, stop

pytestmark = pytest.mark.integration

PROGRAM = str(Path(sys.executable).parent / "onepledge-keyholder")
BOOT_SECONDS = 15.0


def environment(**values: str) -> dict[str, str]:
    base = {name: os.environ[name] for name in ("PATH", "SYSTEMROOT") if name in os.environ}
    return {**base, **values}


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


@pytest.mark.parametrize(("holder", "version"), [("kh1", "1"), ("kh2", "4")])
def test_serve_boots_and_reports_its_identity(served: list[Any], holder: str, version: str) -> None:
    port = free_port()
    served.append(
        start([PROGRAM, "serve"], environment(HOLDER_ID=holder, ACTIVE_KEY_VERSION=version, PORT=str(port)))
    )
    assert wait_for_health(port) == {
        "status": "ok",
        "service": "key_holder",
        "holder_id": holder,
        "active_key_version": int(version),
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


def test_a_missing_command_is_a_usage_error() -> None:
    result = run([PROGRAM], environment())
    assert result.returncode == 2
