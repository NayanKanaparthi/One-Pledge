"""`onepledge-outside`: every command on a real database, the one sentence errors, and serving."""

from __future__ import annotations

import io
import json
import os
import select
import socket
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
import pytest
from sqlalchemy.engine import make_url

from onepledge_outside_records.domain.errors import OperatorError
from onepledge_outside_records.infrastructure.db.migrations import MigrationRunner
from onepledge_outside_records.infrastructure.logging_setup import configure_logging
from onepledge_outside_records.interface.cli import main
from onepledge_outside_records.interface.output import Console

if TYPE_CHECKING:
    from conftest import Database

# Several tests call `serve` in process and expect it to refuse; if a refusal regressed, the
# service would start and the test would never end, so every test here has a limit of 60 s
# (SPEC_DECISIONS 76).
pytestmark = [pytest.mark.integration, pytest.mark.timeout(60)]

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
BIND_HOST_REFUSED = (
    "Configuration error: BIND_HOST must be an IPv4 or IPv6 address; set it to 127.0.0.1 or 0.0.0.0.\n"
)

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
        # --wide: the test database's long name would otherwise wrap the line at 100 columns.
        code, out, err = run(["db", "provision", "--wide"], **env)
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
    code, out, err = run(["seed", "--data-dir", str(example_dir / "absent"), "--json", "--wide"], **env)
    document = json.loads(out)
    assert (code, document["ok"], document["error"]["code"]) == (1, False, "INVALID_INPUT")
    assert document["error"]["message"] == err.strip()
    code, out, err = run(["db", "migrate", "--json"])
    assert json.loads(out)["error"] == {
        "code": "CONFIGURATION_ERROR",
        "message": "Configuration error: MIGRATION_DATABASE_URL is required but not set.",
    }


def test_a_row_count_of_one_is_singular(
    run: Run, provisioned_database: Database, tmp_path: Path, copy_records: Callable[[Path], Path]
) -> None:
    # A database of its own: seeding one row each must not replace the session's example records.
    MigrationRunner(provisioned_database.owner_url, provisioned_database.app_role).upgrade()
    directory = copy_records(tmp_path)
    for name in ("titles.csv", "payments.csv", "contracts.csv"):
        path = directory / "outside_records" / name
        path.write_text("".join(path.read_text().splitlines(keepends=True)[:2]))
    code, out, _ = run(["seed", "--data-dir", str(directory)], **provisioned_database.environment())
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
    # SPEC_DECISIONS 53.12: the type and the frames, never the message or a source line.
    assert code == 1 and "RuntimeError, raised at:\n" in err and " explode\n" in err
    assert "boom" not in err and "Traceback" not in err and 'raise RuntimeError("boom")' not in err


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
    assert err == "The outside records tables are missing; run db migrate first.\n"


def test_serving_on_a_busy_port_says_so(run: Run, database: Database) -> None:
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        port = busy.getsockname()[1]
        code, _, err = run(["serve"], **database.environment(PORT=str(port)))
    assert code == 1
    assert err == "PORT is already in use or not permitted on BIND_HOST; set another PORT.\n"


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
    code, _, err = run(["seed", "--data-dir", str(tmp_path), "--wide"], **database.environment())
    assert code == 1
    assert err.endswith("cannot be read; pass the directory written by onepledge-datagen.\n")


# SPEC_DECISIONS 72: serve refuses any role that is not least-privilege, with one line naming
# DATABASE_URL and the first violation code; test_role_check.py has a case for every point.


def test_serving_as_the_owner_role_is_refused(run: Run, database: Database) -> None:
    env = database.environment(PORT=str(free_port()), DATABASE_URL=database.owner_url)
    assert run(["serve"], **env) == (
        1,
        "",
        "The role of DATABASE_URL is not least-privilege (ROLE_OWNS_OBJECT); use the app role.\n",
    )


def test_serving_as_a_superuser_is_refused(run: Run, database: Database) -> None:
    admin_on_database = str(make_url(database.admin_url).set(database=database.target.database))
    env = database.environment(PORT=str(free_port()), DATABASE_URL=admin_on_database)
    code, out, err = run(["serve"], **env)
    assert (code, out) == (1, "")
    assert err == "The role of DATABASE_URL is not least-privilege (ROLE_IS_SUPERUSER); use the app role.\n"


# SPEC_DECISIONS 43.3: ordinary mistakes get their own sentence, never the last resort one.


def test_provisioning_with_an_admin_url_that_is_not_a_superuser_says_so(run: Run, database: Database) -> None:
    code, out, err = run(["db", "provision"], **database.environment(PG_ADMIN_URL=database.owner_url))
    assert (code, out) == (1, "")
    assert err == "PG_ADMIN_URL connects as a role that is not a superuser; db provision needs one.\n"


@pytest.mark.parametrize(
    ("variable", "value", "sentence"),
    [
        (
            "BIND_HOST",
            "999.1.1.1",
            BIND_HOST_REFUSED,
        ),
        (
            "BIND_HOST",
            "localhost",
            BIND_HOST_REFUSED,
        ),
        (
            "PORT",
            "\u00b2",
            "Configuration error: PORT must be a whole number written with the digits 0 to 9.\n",
        ),
        (
            "PORT",
            "9" * 5000,
            "Configuration error: PORT is out of range (1 to 65,535).\n",
        ),
        (
            "PORT",
            "+80",
            "Configuration error: PORT must be a whole number written with the digits 0 to 9.\n",
        ),
        (
            "PORT",
            "65536",
            "Configuration error: PORT is out of range (1 to 65,535).\n",
        ),
    ],
)
def test_a_malformed_serving_setting_is_one_sentence_naming_it(
    run: Run, database: Database, variable: str, value: str, sentence: str
) -> None:
    env = database.environment(PORT=str(free_port()))
    assert run(["serve"], **{**env, variable: value}) == (1, "", sentence)


def test_a_bind_host_that_is_not_local_is_one_sentence(run: Run, database: Database) -> None:
    env = database.environment(PORT=str(free_port()), BIND_HOST="192.0.2.77")
    assert run(["serve"], **env) == (
        1,
        "",
        "BIND_HOST is not an address of this machine. Set BIND_HOST to 127.0.0.1 or 0.0.0.0.\n",
    )


def test_an_empty_data_dir_is_a_usage_error(run: Run, capsys: pytest.CaptureFixture[str]) -> None:
    for value in ("", "  "):
        with pytest.raises(SystemExit) as raised:
            run(["seed", "--data-dir", value])
        assert raised.value.code == 2
    assert "--data-dir: must name a directory, not be empty" in capsys.readouterr().err


# SPEC_DECISIONS 43.6: Alembic's own INFO lines are dropped; migrate writes one record per revision.


def test_migrate_writes_one_record_per_applied_revision(run: Run, provisioned_database: Database) -> None:
    code, _, err = run(["db", "migrate"], **provisioned_database.environment(LOG_LEVEL="INFO"))
    assert code == 0
    lines = json_lines(err)
    applied = [line for line in lines if line["event"] == "schema_migration_applied"]
    assert [line["revision"] for line in applied] == ["0001_schema", "0002_grants"]
    assert not [line for line in lines if line["event"] == "library_log"]
    assert all(set(line) <= {"ts", "level", "event", "service", "revision", "logger"} for line in lines)
    code, _, err = run(["db", "migrate"], **provisioned_database.environment(LOG_LEVEL="INFO"))
    assert code == 0 and not [line for line in json_lines(err) if line["event"] == "schema_migration_applied"]


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


# SPEC_DECISIONS 66: a migration failure is one fixed sentence; its log record carries
# exception_type and the revision being applied; --debug shows the frames.
def test_a_failed_migration_logs_its_revision_and_debug_shows_the_frames(
    run: Run, provisioned_database: Database
) -> None:
    env = provisioned_database.environment(DB_APP_ROLE="optest_no_such_role", LOG_LEVEL="INFO")
    code, out, err = run(["db", "migrate"], **env)
    assert (code, out) == (1, "")
    assert err.splitlines()[-1] == "The role named by DB_APP_ROLE does not exist; run db provision first."
    failed = [line for line in json_lines("\n".join(err.splitlines()[:-1])) if line["level"] == "error"]
    assert [(r["event"], r["revision"], r["exception_type"]) for r in failed] == [
        ("schema_migration_failed", "0002_grants", "CommandError")
    ]
    code, _, err = run(["db", "migrate", "--debug"], **env)
    assert code == 1 and "CommandError, raised at:\n" in err and "0002_grants.py:" in err


def test_an_unknown_target_revision_is_logged_with_that_revision(
    provisioned_database: Database, capsys: pytest.CaptureFixture[str]
) -> None:

    configure_logging("outside_records", "INFO")
    with pytest.raises(OperatorError):
        MigrationRunner(provisioned_database.owner_url, provisioned_database.app_role).upgrade("9999_unknown")
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")]
    assert [(r["event"], r["revision"]) for r in lines if r["level"] == "error"] == [
        ("schema_migration_failed", "9999_unknown")
    ]


# SPEC_DECISIONS 73.6: the real probe with each kind of address. This machine may have no IPv6,
# so `::` and `::1` may give the IPv6 sentence; never the last resort one.
NO_IPV6_SENTENCE = (
    "BIND_HOST is an IPv6 address and this machine has no IPv6. Set BIND_HOST to 127.0.0.1 or 0.0.0.0."
)
NOT_LOCAL_SENTENCE = "BIND_HOST is not an address of this machine. Set BIND_HOST to 127.0.0.1 or 0.0.0.0."


def serve_outcome(env: dict[str, str]) -> tuple[str, str]:
    """Start serve as a process: ("started", "") once it logs service_started, else its stderr."""
    script = "from onepledge_outside_records.interface.cli import main; raise SystemExit(main(['serve']))"
    process = subprocess.Popen(  # noqa: S603 - fixed interpreter and arguments
        [sys.executable, "-c", script], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    deadline = time.monotonic() + 20
    try:
        assert process.stdout is not None
        while time.monotonic() < deadline and process.poll() is None:
            ready, _, _ = select.select([process.stdout], [], [], 0.2)
            if ready and "service_started" in process.stdout.readline():
                return "started", ""
        _, err = process.communicate(timeout=10)
        return "refused", err
    finally:
        if process.poll() is None:
            process.terminate()
            process.communicate(timeout=10)


@pytest.mark.timeout(120)
@pytest.mark.parametrize("host", ["127.0.0.1", "0.0.0.0", "8.8.8.8", "::", "::1"])  # noqa: S104 - decision 73.6
def test_the_real_probe_on_each_bind_host(database: Database, host: str) -> None:
    env = {
        **{k: v for k, v in os.environ.items() if k not in VARIABLES},
        "DATABASE_URL": database.app_url,
        "PORT": str(free_port()),
        "BIND_HOST": host,
    }
    outcome, err = serve_outcome(env)
    if host == "8.8.8.8":
        assert (outcome, err) == ("refused", NOT_LOCAL_SENTENCE + "\n")
    elif host in ("::", "::1"):
        assert outcome == "started" or err == NO_IPV6_SENTENCE + "\n", err
    else:
        assert outcome == "started", err
