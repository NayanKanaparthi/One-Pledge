"""Startup is a list of named checks, and none ends in the last resort sentence (SPEC_DECISIONS 104).

The three reproductions of WP1 review 4, failure 2, on every runtime command of the bank agent
(`worker --once`, `registry sync`, `integrity verify`, `integrity report`): a `DATABASE_URL`
carrying a query parameter, and the two role settings DBAs use (`statement_timeout`,
`search_path`). Before decisions 103 and 104 each reached the last resort sentence in outside
records. Now the first is the settings sentence, and the role settings are named by the role
inspection with their advice; `integrity report`, which does not refuse a role, runs because the
bank agent sets its own session parameters. A statement the database refuses inside a check is
that check's failure with its SQLSTATE.
"""

from __future__ import annotations

import io
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import LiteralString

import pytest
from psycopg import sql

from bank_testkit.database import BankDatabase
from bank_testkit.doubles import InProcessDoubles
from onepledge_bank_agent.interface.cli.main import UNEXPECTED, main
from onepledge_bank_agent.interface.cli.output import Output

pytestmark = pytest.mark.integration

UNREACHABLE = "http://127.0.0.1:9"
COMMANDS = (("worker", "--once"), ("registry", "sync"), ("integrity", "verify"))
SETTING = "The role of DATABASE_URL is not least-privilege (ROLE_HAS_SETTING); remove the role's settings."


@dataclass(frozen=True)
class Run:
    code: int
    out: str
    err: str


type Cli = Callable[..., Run]


@pytest.fixture
def cli(bank_db: BankDatabase, doubles: InProcessDoubles, monkeypatch: pytest.MonkeyPatch) -> Iterator[Cli]:
    variables = {**bank_db.environment(), **doubles.environment("HARBOR"), "BANK_CODE": "HARBOR",
                 "BANK_NAME": "Harbor Bank", "OUTSIDE_RECORDS_URL": UNREACHABLE,
                 "OUTSIDE_RECORDS_API_KEY": "opk_" + "k" * 43, "REGISTRY_URL": UNREACHABLE,
                 "KEY_HOLDER_1_URL": UNREACHABLE, "KEY_HOLDER_2_URL": UNREACHABLE}  # fmt: skip
    for name, value in variables.items():
        monkeypatch.setenv(name, value)

    def invoke(*argv: str) -> Run:
        out, err = io.StringIO(), io.StringIO()
        code = main(list(argv), Output(out, err), None)
        return Run(code, out.getvalue(), " ".join(err.getvalue().split()))

    yield invoke


def as_superuser(database: BankDatabase, statement: LiteralString) -> None:
    with database.connect("admin") as connection:
        connection.execute(
            sql.SQL(statement).format(app=sql.Identifier(database.app_role), db=sql.Identifier(database.name))
        )


def test_a_database_url_with_a_query_parameter_is_the_settings_sentence(
    cli: Cli, bank_db: BankDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATABASE_URL", bank_db.app_url + "?options=-csearch_path%3Dpg_temp")
    for command in (*COMMANDS, ("integrity", "report")):
        run = cli(*command)
        assert (run.code, run.err.strip()) == (
            1,
            "Configuration error: DATABASE_URL must not carry query parameters; remove the part from ? on.",
        ), command


@pytest.mark.parametrize(
    "setting",
    ["ALTER ROLE {app} SET statement_timeout = '1ms'", "ALTER ROLE {app} SET search_path = pg_temp, public"],
    ids=["statement_timeout", "search_path"],
)
def test_a_role_setting_is_named_by_the_role_inspection_and_never_the_last_resort(
    cli: Cli, bank_db: BankDatabase, setting: LiteralString
) -> None:
    as_superuser(bank_db, setting)
    for command in COMMANDS:
        run = cli(*command)
        assert (run.code, run.err.strip()) == (1, SETTING), command
        assert UNEXPECTED not in run.err
    report = cli("integrity", "report")
    assert UNEXPECTED not in report.err and "TRIGGERS_FIRE_FOR_APP_ROLE" in report.out, (
        "the report runs with the bank agent's own session parameters and shows the control false"
    )


def test_a_statement_refused_inside_a_check_is_that_checks_failure_with_its_sqlstate(
    cli: Cli, bank_db: BankDatabase
) -> None:
    as_superuser(bank_db, "REVOKE EXECUTE ON FUNCTION pg_catalog.aclexplode(aclitem[]) FROM PUBLIC")
    try:
        for command in COMMANDS:
            run = cli(*command)
            assert (run.code, run.err.strip()) == (
                1,
                "The database refused a statement of the startup check role inspection (SQLSTATE 42501); "
                "check the settings of the role of DATABASE_URL and of its database.",
            ), command
    finally:
        as_superuser(bank_db, "GRANT EXECUTE ON FUNCTION pg_catalog.aclexplode(aclitem[]) TO PUBLIC")
