"""Served doubles: one error record per failure, the health value as `service`, and the CLI guard rails."""

from __future__ import annotations

import hmac
import io
import json
import socket
import threading
import time
from collections.abc import Callable, Iterator

import httpx
import pytest
from fastapi import FastAPI

from onepledge_contract_fakes.cli_output import CliOutput
from onepledge_contract_fakes.key_holder import cli as key_holder_cli
from onepledge_contract_fakes.logging_setup import configure_logging
from onepledge_contract_fakes.members import MemberDirectory
from onepledge_contract_fakes.registry import cli as registry_cli
from onepledge_contract_fakes.server import build_server
from onepledge_contract_fakes.testing import new_members
from onepledge_contract_fakes.web import RequestContextMiddleware, install_error_handlers

pytestmark = pytest.mark.integration


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
        return port


def failing_app() -> FastAPI:
    app = FastAPI()
    install_error_handlers(app)
    app.add_middleware(RequestContextMiddleware)

    @app.get("/v1/boom")
    async def boom() -> None:
        raise RuntimeError("1HGCM82633A004352 exploded")

    return app


@pytest.fixture
def served_failing_app(capsys: pytest.CaptureFixture[str]) -> Iterator[Callable[[], list[dict[str, object]]]]:
    """The failing app served by uvicorn exactly as `run_server` serves a double, logging to stdout."""
    configure_logging("registry", "INFO")
    port = free_port()
    server = build_server(failing_app(), "127.0.0.1", port)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    response = httpx.get(f"http://127.0.0.1:{port}/v1/boom", timeout=5)
    assert response.status_code == 500 and response.json()["error"]["code"] == "INTERNAL_ERROR"
    server.should_exit = True
    thread.join(timeout=10)

    def lines() -> list[dict[str, object]]:
        return [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")]

    yield lines


def test_one_unhandled_failure_writes_exactly_one_error_record(
    served_failing_app: Callable[[], list[dict[str, object]]],
) -> None:
    records = served_failing_app()
    errors = [record for record in records if record["level"] == "error"]
    assert len(errors) == 1
    assert errors[0]["event"] == "unhandled_exception" and errors[0]["exception_type"] == "RuntimeError"
    assert all(record["service"] == "registry" for record in records)
    assert "1HGCM82633A004352" not in json.dumps(records) and "exploded" not in json.dumps(records)


def test_the_service_names_are_the_health_values() -> None:
    assert (registry_cli.SERVICE_NAME, key_holder_cli.SERVICE_NAME) == ("registry", "key_holder")


def test_every_configured_member_is_compared(monkeypatch: pytest.MonkeyPatch) -> None:
    members, keys = new_members(4)
    directory = MemberDirectory(members)
    calls: list[str] = []
    original = hmac.compare_digest

    def counting(a: str, b: str) -> bool:
        calls.append(b)
        return original(a, b)

    monkeypatch.setattr(hmac, "compare_digest", counting)
    assert directory.identify(keys[0]) == members[0].member_id
    assert calls == [m.api_key_sha256 for m in members]


@pytest.mark.parametrize("cli_main", [registry_cli.main, key_holder_cli.main])
def test_serve_refuses_json_as_a_usage_error(
    cli_main: Callable[..., int], capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exited:
        cli_main(["serve", "--json"], CliOutput(io.StringIO(), io.StringIO()))
    assert exited.value.code == 2
    assert "unrecognized arguments: --json" in capsys.readouterr().err
