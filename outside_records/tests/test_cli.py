"""`onepledge-outside`: every command on a real database, the one sentence errors, and serving."""

from __future__ import annotations

import io
import json
import os
import socket
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
import pytest

from onepledge_outside_records.infrastructure.db.migrations import MigrationRunner
from onepledge_outside_records.interface.cli import main
from onepledge_outside_records.interface.output import Console

if TYPE_CHECKING:
    from conftest import Database

pytestmark = pytest.mark.integration

VARIABLES = (
    "PG_ADMIN_URL",
    "DATABASE_URL",
    "MIGRATION_DATABASE_URL",
    "DB_APP_ROLE",
    "PORT",
    "BIND_HOST",
    "LOG_LEVEL",
    "OUTSIDE_CLI_TEST_KEY",
)
UNSEEDED_VIN = "1HGCM82633A004352"

type Run = Callable[..., tuple[int, str, str]]


@pytest.fixture
def run(monkeypatch: pytest.MonkeyPatch) -> Run:
    """`run(argv, **environment)` runs the command line in-process and returns code, stdout and stderr."""

    def invoke(argv: list[str], **environment: str) -> tuple[int, str, str]:
        for name in VARIABLES:
            monkeypatch.delenv(name, raising=False)
        for name, value in environment.items():
            monkeypatch.setenv(name, value)
        out, err = io.StringIO(), io.StringIO()
        code = main(argv, Console(out, err))
        return code, out.getvalue(), err.getvalue()

    return invoke


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
        return port


def json_lines(text: str) -> list[dict[str, Any]]:
    """Every line of a log stream, each of which must be one JSON object."""
    return [json.loads(line) for line in text.splitlines()]


def test_the_whole_command_sequence_on_a_fresh_database(
    run: Run, fresh_database: Database, example_dir: Path, example_records: Path, make_key: Callable[[], str]
) -> None:
    env = fresh_database.environment()
    name = fresh_database.target.database
    for _ in range(2):
        code, out, err = run(["db", "provision"], **env)
        assert code == 0
        assert all(line["service"] == "outside_records" for line in json_lines(err))
        assert out == f"Provisioned database {name} with roles {name}_owner and {name}_app.\n"
    code, out, err = run(["db", "migrate"], **env)
    assert (code, out) == (0, "Migrated the outside records database to revision 0002_grants.\n")
    assert all(line["service"] == "outside_records" for line in json_lines(err))
    code, out, _ = run(["seed", "--data-dir", str(example_dir)], **env)
    titles, payments, contracts = (
        len((example_records / name).read_text().splitlines()) - 1
        for name in ("titles.csv", "payments.csv", "contracts.csv")
    )
    assert code == 0
    assert out.splitlines() == [
        "Seeded outside records:",
        f"  title_records  {titles:>9,} rows",
        f"  payments       {payments:>9,} rows",
        f"  contracts      {contracts:>9,} rows",
    ]
    assert run(["seed", "--data-dir", str(example_dir)], **env)[1] == out
    key = make_key()
    argv = ["clients", "add", "--name", "harbor", "--key-env", "OUTSIDE_CLI_TEST_KEY"]
    code, out, _ = run(argv, **env, OUTSIDE_CLI_TEST_KEY=key)
    assert code == 0
    assert out.startswith("Client harbor is active (client id ")
    assert key not in out


def test_json_results_are_one_document_and_logs_stay_on_stderr(
    run: Run, fresh_database: Database, example_dir: Path, example_records: Path
) -> None:
    env = fresh_database.environment()
    name = fresh_database.target.database
    code, out, _ = run(["db", "provision", "--json"], **env)
    assert (code, json.loads(out)) == (
        0,
        {"ok": True, "database": name, "owner_role": f"{name}_owner", "app_role": f"{name}_app"},
    )
    code, out, err = run(["db", "migrate", "--json"], **env)
    assert (code, json.loads(out)) == (0, {"ok": True, "revision": "0002_grants"})
    assert json_lines(err)
    code, out, err = run(["db", "migrate", "--json", "--quiet"], **env)
    assert (code, json.loads(out)["ok"], err) == (0, True, "")
    code, out, _ = run(["seed", "--data-dir", str(example_dir), "--json"], **env)
    titles = len((example_records / "titles.csv").read_text().splitlines()) - 1
    assert json.loads(out)["tables"]["title_records"] == titles
    code, out, err = run(["seed", "--data-dir", str(example_dir / "absent"), "--json"], **env)
    document = json.loads(out)
    assert (code, document["ok"], document["error"]["code"]) == (1, False, "INVALID_INPUT")
    assert document["error"]["message"] == err.strip()
    code, out, err = run(["db", "migrate", "--json"])
    assert json.loads(out)["error"] == {
        "code": "CONFIGURATION_ERROR",
        "message": "Configuration error: MIGRATION_DATABASE_URL is required but not set.",
    }


def test_a_row_count_of_one_is_singular(
    run: Run, database: Database, tmp_path: Path, copy_records: Callable[[Path], Path]
) -> None:
    directory = copy_records(tmp_path)
    for name in ("titles.csv", "payments.csv", "contracts.csv"):
        path = directory / "outside_records" / name
        path.write_text("".join(path.read_text().splitlines(keepends=True)[:2]))
    code, out, _ = run(["seed", "--data-dir", str(directory)], **database.environment())
    assert code == 0
    assert out.splitlines()[1:] == [
        "  title_records           1 row",
        "  payments                1 row",
        "  contracts               1 row",
    ]


def test_an_unexpected_error_is_one_sentence_and_debug_adds_the_traceback(
    run: Run, database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode(self: object) -> str:
        raise RuntimeError("boom")

    monkeypatch.setattr(MigrationRunner, "head_revision", explode)
    code, out, err = run(["db", "migrate"], **database.environment())
    assert (code, out, err.splitlines()[-1]) == (1, "", "Unexpected error. Run with --debug for details.")
    assert "Traceback" not in err
    code, _, err = run(["db", "migrate", "--debug"], **database.environment())
    assert code == 1 and "Traceback (most recent call last)" in err and "RuntimeError: boom" in err


def test_serve_refuses_json_as_a_usage_error(run: Run, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as raised:
        run(["serve", "--json"])
    assert raised.value.code == 2
    assert "unrecognized arguments: --json" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("argv", "missing"),
    [
        (["db", "provision"], "MIGRATION_DATABASE_URL"),
        (["db", "migrate"], "MIGRATION_DATABASE_URL"),
        (["seed", "--data-dir", "x"], "MIGRATION_DATABASE_URL"),
        (["clients", "add", "--name", "a", "--key-env", "K"], "MIGRATION_DATABASE_URL"),
        (["serve"], "PORT"),
    ],
)
def test_a_missing_variable_is_one_sentence(run: Run, argv: list[str], missing: str) -> None:
    assert run(argv) == (1, "", f"Configuration error: {missing} is required but not set.\n")


def test_usage_errors_exit_with_2(run: Run, capsys: pytest.CaptureFixture[str]) -> None:
    for argv in ([], ["db"], ["seed"], ["clients", "add", "--name", "x"], ["unknown"]):
        with pytest.raises(SystemExit) as raised:
            run(argv)
        assert raised.value.code == 2
    assert "usage: onepledge-outside" in capsys.readouterr().err


def test_help_lists_every_command(run: Run, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as raised:
        run(["--help"])
    assert raised.value.code == 0
    text = capsys.readouterr().out
    for command in ("db", "seed", "clients", "serve"):
        assert command in text


def test_serving_an_unmigrated_database_says_what_to_run(run: Run, provisioned_database: Database) -> None:
    code, _, err = run(["serve"], **provisioned_database.environment(PORT=str(free_port())))
    assert code == 1
    assert err == "The database at DATABASE_URL cannot be read; run db provision and db migrate first.\n"


def test_serving_on_a_busy_port_says_so(run: Run, database: Database) -> None:
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        port = busy.getsockname()[1]
        code, _, err = run(["serve"], **database.environment(PORT=str(port)))
    assert code == 1
    assert err == f"Port {port} on 127.0.0.1 is already in use or not available; set another PORT.\n"


def test_adding_a_client_needs_the_key_variable_and_the_key_format(run: Run, database: Database) -> None:
    env = database.environment()
    argv = ["clients", "add", "--name", "x", "--key-env", "OUTSIDE_CLI_TEST_KEY"]
    code, _, err = run(argv, **env)
    assert code == 1
    assert (
        err
        == "The environment variable OUTSIDE_CLI_TEST_KEY is not set; export the client key in it first.\n"
    )
    code, _, err = run(argv, **env, OUTSIDE_CLI_TEST_KEY="secret-value-123")
    assert code == 1
    assert "secret-value-123" not in err


def test_seeding_a_directory_that_is_not_a_data_set_says_so(
    run: Run, database: Database, tmp_path: Path
) -> None:
    code, _, err = run(["seed", "--data-dir", str(tmp_path)], **database.environment())
    assert code == 1
    assert err.endswith("cannot be read; pass the directory written by onepledge-datagen.\n")


def get(url: str, key: str | None = None) -> tuple[int, dict[str, object]]:
    response = httpx.get(url, headers={} if key is None else {"X-API-Key": key}, timeout=2)
    body: dict[str, object] = response.json()
    return response.status_code, body


@pytest.fixture
def served(database: Database, api_key: str) -> Iterator[tuple[str, subprocess.Popen[bytes]]]:
    """`onepledge-outside serve` as a child process on a free port; yields its base URL."""
    port = free_port()
    env = {
        **{k: v for k, v in os.environ.items() if k not in VARIABLES},
        "DATABASE_URL": database.app_url,
        "PORT": str(port),
        "LOG_LEVEL": "INFO",
    }
    script = "from onepledge_outside_records.interface.cli import main; raise SystemExit(main(['serve']))"
    process = subprocess.Popen(  # noqa: S603 - fixed interpreter and arguments
        [sys.executable, "-c", script], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            get(f"{base}/healthz")
            break
        except httpx.TransportError:
            time.sleep(0.1)
    yield base, process
    if process.poll() is None:
        process.terminate()
        process.communicate(timeout=10)


def test_the_served_api_answers_and_logs_no_vin(
    served: tuple[str, subprocess.Popen[bytes]], api_key: str, example_records: Path
) -> None:
    base, process = served
    seeded = (example_records / "titles.csv").read_text().splitlines()[1].split(",")[0]
    assert get(f"{base}/healthz") == (200, {"status": "ok", "service": "outside_records"})
    status, body = get(f"{base}/v1/titles/{seeded}", api_key)
    assert (status, body["vin"]) == (200, seeded)
    assert get(f"{base}/v1/titles/{UNSEEDED_VIN}", api_key)[0] == 404
    assert get(f"{base}/v1/titles/{seeded}")[0] == 401
    assert get(f"{base}/v1/payments?vin={seeded}", api_key)[0] == 200
    process.terminate()
    stdout, stderr = process.communicate(timeout=10)
    lines = [json.loads(line) for line in stdout.decode().splitlines() if line.strip()]
    assert lines[0]["event"] == "service_started"
    assert {line["service"] for line in lines} == {"outside_records"}
    access = [line for line in lines if line["event"] == "http_request"]
    assert [line["route"] for line in access] == [
        "/healthz",
        "/healthz",
        "/v1/titles/{vin}",
        "/v1/titles/{vin}",
        "/v1/titles/{vin}",
        "/v1/payments",
    ]
    assert [line["client"] for line in access] == [None, None, "harbor", "harbor", None, "harbor"]
    text = stdout.decode() + stderr.decode()
    for forbidden in (seeded, UNSEEDED_VIN, seeded.lower(), api_key, "?vin="):
        assert forbidden not in text
