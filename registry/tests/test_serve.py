"""`onepledge-registry serve` as a process: it boots, answers `/healthz` over TCP, refuses bad settings."""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from onepledge_testtools.network import LOOPBACK_IPV4, free_port
from onepledge_testtools.processes import run, start, stop

pytestmark = pytest.mark.integration

PROGRAM = str(Path(sys.executable).parent / "onepledge-registry")
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


def test_serve_boots_and_answers_health() -> None:
    port = free_port()
    process = start([PROGRAM, "serve"], environment(PORT=str(port)))
    try:
        assert wait_for_health(port) == {"status": "ok", "service": "registry"}
    finally:
        stop(process)


def test_serve_refuses_a_bad_port_in_one_sentence_without_the_value() -> None:
    result = run([PROGRAM, "serve"], environment(PORT="84oo"))
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == "PORT must be a whole number from 1 to 65535 in ASCII digits.\n"


def test_a_missing_command_is_a_usage_error() -> None:
    assert run([PROGRAM], environment()).returncode == 2
