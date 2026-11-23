"""Scratch names, registration and sweeps (SPEC_DECISIONS 76, 115): injected names and real PostgreSQL.

Every name a test here creates is an `optest_` name made by `new_name`, so a killed run leaves
nothing a sweep cannot find (decision 115.4).
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from psycopg import sql

from onepledge_testtools import processes, scratch_databases
from onepledge_testtools.scratch_databases import (
    SESSION_SWEEP_AGE,
    SweepResult,
    admin_url,
    created_at,
    new_name,
    registered,
    select_stale,
    session_stem,
    sweep,
)

NOW = datetime(2026, 11, 22, 12, 0, 0, tzinfo=UTC)


def stamp(moment: datetime) -> str:
    return moment.strftime("%Y%m%d%H%M%S")


@pytest.mark.unit
def test_a_new_name_carries_the_session_stem_and_is_registered_before_use() -> None:
    name = new_name("outside")
    assert re.fullmatch(r"optest_[0-9]{14}_[0-9]+_[0-9a-f]{8}_outside", name)
    assert name.startswith(session_stem()) and session_stem().endswith(f"_{os.getpid()}_")
    assert registered()[-1] == name
    assert new_name("outside") != name


@pytest.mark.unit
def test_a_name_of_another_age_keeps_the_pid_and_is_registered() -> None:
    old = new_name("old", NOW - timedelta(hours=3))
    assert old.startswith(f"optest_{stamp(NOW - timedelta(hours=3))}_{os.getpid()}_")
    assert old in registered()


@pytest.mark.unit
def test_a_name_postgresql_would_cut_is_refused() -> None:
    with pytest.raises(ValueError, match="longer than 63 bytes"):
        new_name("s" * 40)


# Decision 115.4: the Python side of "never touches any other name", pinned on injected listings.
LISTING = [
    "postgres",
    "template1",
    "outside_records",
    "my_optest_db",
    "OPTEST_20261122110000_1_0123abcd_x",
    "xoptest_20261122110000_1_0123abcd_x",
    f"optest_{stamp(NOW - timedelta(hours=3))}_12_0123abcd_bank",
    f"optest_{stamp(NOW - timedelta(minutes=5))}_12_0123abcd_bank",
    f"optest_{stamp(NOW - timedelta(hours=2, seconds=1))}_12_0123abcd",
    "optest_99999999999999_1_abcdef12_x",
    "optest_20261322000000_1_abcdef12_x",
    "optest_00000000000000_1_abcdef12_x",
    "optest_99991231235959_1_abcdef12_x",
    "optest_0123456789_outside",
    "optest_",
]


@pytest.mark.unit
def test_the_session_sweep_selects_old_and_unreadable_optest_names_only() -> None:
    assert timedelta(hours=2) == SESSION_SWEEP_AGE
    assert select_stale(LISTING, NOW, SESSION_SWEEP_AGE) == [
        f"optest_{stamp(NOW - timedelta(hours=3))}_12_0123abcd_bank",
        f"optest_{stamp(NOW - timedelta(hours=2, seconds=1))}_12_0123abcd",
        "optest_99999999999999_1_abcdef12_x",
        "optest_20261322000000_1_abcdef12_x",
        "optest_00000000000000_1_abcdef12_x",
        "optest_99991231235959_1_abcdef12_x",
        "optest_0123456789_outside",
        "optest_",
    ]


@pytest.mark.unit
def test_the_full_sweep_selects_every_optest_name_and_nothing_else() -> None:
    assert select_stale(LISTING, NOW, None) == [name for name in LISTING if name.startswith("optest_")]
    assert all(
        not name.startswith("optest_") for name in set(LISTING) - set(select_stale(LISTING, NOW, None))
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "name",
    [
        "optest_99999999999999_1_abcdef12_x",
        "optest_20261322000000_1_abcdef12_x",
        "optest_abc_outside",
        "postgres",
    ],
)
def test_a_timestamp_that_cannot_be_read_gives_no_creation_time_and_no_error(name: str) -> None:
    assert created_at(name) is None


@pytest.fixture
def admin() -> Iterator[psycopg.Connection[tuple[object, ...]]]:
    with psycopg.connect(admin_url(), autocommit=True) as connection:
        yield connection


def exists(admin: psycopg.Connection[tuple[object, ...]], kind: str, name: str) -> bool:
    query = (
        "SELECT 1 FROM pg_database WHERE datname = %s"
        if kind == "database"
        else "SELECT 1 FROM pg_roles WHERE rolname = %s"
    )
    return admin.execute(query, (name,)).fetchone() is not None


def create(admin: psycopg.Connection[tuple[object, ...]], kind: str, name: str) -> None:
    statement = "CREATE DATABASE {}" if kind == "database" else "CREATE ROLE {}"
    admin.execute(sql.SQL(statement).format(sql.Identifier(name)))


@pytest.mark.integration
def test_the_sweep_drops_old_optest_objects_and_leaves_recent_ones(
    admin: psycopg.Connection[tuple[object, ...]],
) -> None:
    now = datetime.now(UTC)
    stale = new_name("stale", now - timedelta(hours=3))
    recent = new_name("recent", now - timedelta(minutes=5))
    for name in (stale, recent):
        create(admin, "database", name)
        create(admin, "role", name)
    result = sweep(admin_url(), SESSION_SWEEP_AGE, now)
    assert result.dropped >= 2
    assert not exists(admin, "database", stale) and not exists(admin, "role", stale)
    assert exists(admin, "database", recent) and exists(admin, "role", recent)


@pytest.mark.integration
def test_an_object_that_cannot_be_dropped_is_one_warning_and_the_sweep_goes_on(
    admin: psycopg.Connection[tuple[object, ...]],
) -> None:
    now = datetime.now(UTC)
    held = new_name("held", now - timedelta(hours=3))
    loose = new_name("loose", now - timedelta(hours=3))
    host = new_name("host")
    create(admin, "role", held)
    create(admin, "role", loose)
    create(admin, "database", host)
    grant = sql.SQL("GRANT CONNECT ON DATABASE {} TO {}")
    admin.execute(grant.format(sql.Identifier(host), sql.Identifier(held)))
    warnings: list[str] = []
    result = sweep(admin_url(), SESSION_SWEEP_AGE, now, warn=warnings.append)
    assert held in result.failed and exists(admin, "role", held)
    assert not exists(admin, "role", loose)
    assert f"could not drop {held}: DependentObjectsStillExist" in warnings


# In a child process: drop_session drops everything of the session that calls it, so it must not
# run inside this test session, whose own fixtures hold databases under its stem.
SESSION_SCRIPT = """
import psycopg
from datetime import UTC, datetime, timedelta
from psycopg import sql
from onepledge_testtools.scratch_databases import admin_url, drop_session, new_name

mine = new_name("mine")
old = new_name("old", datetime.now(UTC) - timedelta(days=1))
with psycopg.connect(admin_url(), autocommit=True) as admin:
    for name in (mine, old, mine + "_owner"):
        admin.execute(sql.SQL("CREATE ROLE {}").format(sql.Identifier(name)))
    admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(mine)))
print(mine, old)
drop_session(admin_url())
"""


@pytest.mark.integration
def test_a_session_drops_everything_under_its_stem_and_everything_it_registered(
    admin: psycopg.Connection[tuple[object, ...]],
) -> None:
    result = processes.run([processes.PYTHON, "-c", SESSION_SCRIPT])
    assert result.returncode == 0, result.stderr
    mine, old = result.stdout.split()
    for kind, name in (("database", mine), ("role", mine), ("role", old), ("role", f"{mine}_owner")):
        assert not exists(admin, kind, name), (kind, name)


@pytest.mark.unit
def test_test_sweep_drops_everything_and_prints_the_count(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[timedelta | None] = []

    def fake_sweep(url: str, older_than: timedelta | None, warn: object) -> SweepResult:
        del url, warn
        calls.append(older_than)
        return SweepResult(7, ())

    monkeypatch.setattr(scratch_databases, "sweep", fake_sweep)
    assert scratch_databases.main() == 0
    assert calls == [None]
    assert capsys.readouterr().out == "test-sweep: dropped 7 optest_ databases and roles\n"


@pytest.mark.integration
def test_test_sweep_says_so_when_the_server_is_not_reachable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PG_ADMIN_URL", "postgresql://postgres@127.0.0.1:1/postgres")
    assert scratch_databases.main() == 1
    assert "not reachable" in capsys.readouterr().err
