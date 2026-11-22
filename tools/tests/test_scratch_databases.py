"""Scratch names and the sweep (SPEC_DECISIONS 76): only `optest_` names, only by age, on real PostgreSQL."""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from psycopg import sql

from onepledge_testtools import scratch_databases
from onepledge_testtools.scratch_databases import (
    SESSION_SWEEP_AGE,
    SweepResult,
    admin_url,
    created_at,
    is_stale,
    new_name,
    sweep,
)

NOW = datetime(2026, 11, 22, 12, 0, 0, tzinfo=UTC)
NAME = re.compile(r"^optest_[0-9]{14}_[0-9]+_[0-9a-f]{8}_outside$")


@pytest.mark.unit
def test_a_new_name_carries_the_utc_time_the_pid_and_eight_hex_characters() -> None:
    name = new_name("outside", NOW)
    assert NAME.match(name)
    assert name.startswith(f"optest_20261122120000_{os.getpid()}_")
    assert created_at(name) == NOW


@pytest.mark.unit
def test_two_new_names_in_the_same_second_differ() -> None:
    assert new_name("x", NOW) != new_name("x", NOW)


@pytest.mark.unit
def test_a_name_postgresql_would_cut_is_refused() -> None:
    with pytest.raises(ValueError, match="longer than 63 bytes"):
        new_name("s" * 40, NOW)


@pytest.mark.unit
@pytest.mark.parametrize(
    "name",
    [
        "optest_abc_outside",
        "optest_2026112212000_1_0123abcd_x",
        "postgres",
        "optest_20261122120000_1_0123ABCD",
    ],
)
def test_a_name_without_the_timestamp_form_has_no_creation_time(name: str) -> None:
    assert created_at(name) is None


@pytest.mark.unit
def test_the_session_sweep_drops_only_timestamped_names_older_than_two_hours() -> None:
    stamp = (NOW - timedelta(hours=2, seconds=1)).strftime("%Y%m%d%H%M%S")
    fresh = (NOW - timedelta(hours=1, minutes=59)).strftime("%Y%m%d%H%M%S")
    assert timedelta(hours=2) == SESSION_SWEEP_AGE
    assert is_stale(f"optest_{stamp}_12_0123abcd_bank", NOW, SESSION_SWEEP_AGE)
    assert is_stale(f"optest_{stamp}_12_0123abcd", NOW, SESSION_SWEEP_AGE)
    assert not is_stale(f"optest_{fresh}_12_0123abcd_bank", NOW, SESSION_SWEEP_AGE)
    assert not is_stale("optest_0123456789_outside", NOW, SESSION_SWEEP_AGE)
    assert not is_stale(f"production_{stamp}_12_0123abcd", NOW, SESSION_SWEEP_AGE)


@pytest.mark.unit
def test_the_full_sweep_takes_every_optest_name_and_nothing_else() -> None:
    assert is_stale("optest_0123456789_outside", NOW, None)
    assert is_stale(new_name("bank", NOW), NOW, None)
    assert not is_stale("postgres", NOW, None)
    assert not is_stale("OPTEST_20261122120000_1_0123abcd", NOW, None)


def _exists(admin: psycopg.Connection[tuple[object, ...]], kind: str, name: str) -> bool:
    query = (
        "SELECT 1 FROM pg_database WHERE datname = %s"
        if kind == "database"
        else "SELECT 1 FROM pg_roles WHERE rolname = %s"
    )
    return admin.execute(query, (name,)).fetchone() is not None


@pytest.fixture
def admin() -> Iterator[psycopg.Connection[tuple[object, ...]]]:
    with psycopg.connect(admin_url(), autocommit=True) as connection:
        yield connection


@pytest.mark.integration
def test_the_sweep_drops_stale_databases_and_roles_and_leaves_every_other_name(
    admin: psycopg.Connection[tuple[object, ...]],
) -> None:
    now = datetime.now(UTC)
    stale = new_name("sweep", now - timedelta(hours=3))
    recent = new_name("sweep", now - timedelta(minutes=5))
    untimed = f"optest_{os.getpid()}untimed_sweep"
    other = f"sweepcheck_{os.getpid()}_{now.strftime('%H%M%S')}"
    created = [(kind, name) for name in (stale, recent, untimed, other) for kind in ("database", "role")]
    try:
        for kind, name in created:
            statement = "CREATE DATABASE {}" if kind == "database" else "CREATE ROLE {}"
            admin.execute(sql.SQL(statement).format(sql.Identifier(name)))
        result = sweep(admin_url(), SESSION_SWEEP_AGE, now)
        assert result.dropped >= 2
        assert not _exists(admin, "database", stale)
        assert not _exists(admin, "role", stale)
        for kind, name in created:
            if name != stale:
                assert _exists(admin, kind, name), (kind, name)
    finally:
        for kind, name in created:
            statement = (
                "DROP DATABASE IF EXISTS {} WITH (FORCE)" if kind == "database" else "DROP ROLE IF EXISTS {}"
            )
            admin.execute(sql.SQL(statement).format(sql.Identifier(name)))


@pytest.mark.integration
def test_a_role_that_still_holds_a_privilege_elsewhere_is_reported_and_the_sweep_goes_on(
    admin: psycopg.Connection[tuple[object, ...]],
) -> None:
    now = datetime.now(UTC)
    held = new_name("held", now - timedelta(hours=3))
    loose = new_name("loose", now - timedelta(hours=3))
    host = f"sweephost_{os.getpid()}"
    try:
        admin.execute(sql.SQL("CREATE ROLE {}").format(sql.Identifier(held)))
        admin.execute(sql.SQL("CREATE ROLE {}").format(sql.Identifier(loose)))
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(host)))
        grant = sql.SQL("GRANT CONNECT ON DATABASE {} TO {}")
        admin.execute(grant.format(sql.Identifier(host), sql.Identifier(held)))
        result = sweep(admin_url(), SESSION_SWEEP_AGE, now)
        assert held in result.failed
        assert _exists(admin, "role", held)
        assert not _exists(admin, "role", loose)
    finally:
        admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(host)))
        admin.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(held)))
        admin.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(loose)))


@pytest.mark.unit
def test_test_sweep_drops_everything_and_prints_the_count(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[timedelta | None] = []

    def fake_sweep(url: str, older_than: timedelta | None) -> SweepResult:
        del url
        calls.append(older_than)
        return SweepResult(7, ())

    monkeypatch.setattr(scratch_databases, "sweep", fake_sweep)
    assert scratch_databases.main() == 0
    assert calls == [None]
    assert capsys.readouterr().out == "test-sweep: dropped 7 optest_ databases and roles\n"


@pytest.mark.unit
def test_test_sweep_names_a_role_it_could_not_drop_and_fails(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(scratch_databases, "sweep", lambda url, older_than: SweepResult(1, ("optest_r",)))
    assert scratch_databases.main() == 1
    err = capsys.readouterr().err
    assert err == "test-sweep: role optest_r still holds privileges in another database; not dropped\n"


@pytest.mark.integration
def test_test_sweep_says_so_when_the_server_is_not_reachable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PG_ADMIN_URL", "postgresql://postgres@127.0.0.1:1/postgres")
    assert scratch_databases.main() == 1
    assert "not reachable" in capsys.readouterr().err
