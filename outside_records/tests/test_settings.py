"""Settings from the environment, database URLs and the one sentence errors (no I/O)."""

from __future__ import annotations

import pytest

from onepledge_outside_records.domain.errors import OperatorError
from onepledge_outside_records.infrastructure.db.engines import DatabaseTarget, sqlalchemy_url
from onepledge_outside_records.infrastructure.settings import Settings, read_secret_variable

pytestmark = pytest.mark.unit

OWNER = "postgresql://opx_outside_owner:owner-secret-1@localhost:55432/opx_outside"
APP = "postgresql://opx_outside_app:app-secret-2@localhost:55432/opx_outside"
ADMIN = "postgresql://postgres:admin-secret-3@localhost:55432/postgres"


def full() -> dict[str, str]:
    return {
        "DATABASE_URL": APP,
        "MIGRATION_DATABASE_URL": OWNER,
        "PG_ADMIN_URL": ADMIN,
        "DB_APP_ROLE": "opx_outside_app",
        "PORT": "8200",
        "BIND_HOST": "192.0.2.10",
        "LOG_LEVEL": "debug",
    }


def test_every_variable_is_read() -> None:
    settings = Settings.from_env(full())
    assert settings.require_database_url() == APP
    assert settings.require_migration_url() == OWNER
    assert settings.require_admin_url() == ADMIN
    assert settings.require_app_role() == "opx_outside_app"
    assert settings.require_port() == 8200
    assert (settings.bind_host, settings.log_level) == ("192.0.2.10", "DEBUG")


def test_defaults_apply_when_optional_values_are_absent_or_blank() -> None:
    settings = Settings.from_env({"BIND_HOST": "  ", "LOG_LEVEL": ""})
    assert (settings.bind_host, settings.log_level) == ("127.0.0.1", "INFO")


@pytest.mark.parametrize(
    ("method", "variable"),
    [
        ("require_database_url", "DATABASE_URL"),
        ("require_migration_url", "MIGRATION_DATABASE_URL"),
        ("require_admin_url", "PG_ADMIN_URL"),
        ("require_app_role", "DB_APP_ROLE"),
        ("require_port", "PORT"),
    ],
)
def test_a_missing_value_is_named_in_one_sentence(method: str, variable: str) -> None:
    with pytest.raises(OperatorError) as raised:
        getattr(Settings.from_env({}), method)()
    assert raised.value.sentence == f"Configuration error: {variable} is required but not set."


@pytest.mark.parametrize(
    ("variable", "value", "fragment"),
    [
        ("PORT", "0", "PORT"),
        ("PORT", "65536", "PORT"),
        ("PORT", "80a", "PORT"),
        ("PORT", "-1", "PORT"),
        ("LOG_LEVEL", "LOUD", "LOG_LEVEL"),
        ("DB_APP_ROLE", "App Role", "DB_APP_ROLE"),
        ("DB_APP_ROLE", 'x"; DROP ROLE postgres; --', "DB_APP_ROLE"),
    ],
)
def test_a_malformed_value_is_named_but_never_shown(variable: str, value: str, fragment: str) -> None:
    with pytest.raises(OperatorError) as raised:
        Settings.from_env({variable: value})
    assert fragment in raised.value.sentence
    assert value not in raised.value.sentence


def test_secrets_never_appear_in_a_repr_or_a_dump() -> None:
    settings = Settings.from_env(full())
    shown = f"{settings!r} {settings!s} {settings.model_dump()} {settings.model_dump_json()}"
    for secret in ("owner-secret-1", "app-secret-2", "admin-secret-3"):
        assert secret not in shown


def test_a_secret_variable_is_read_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OUTSIDE_TEST_KEY", "opk_value")
    assert read_secret_variable("OUTSIDE_TEST_KEY") == "opk_value"
    monkeypatch.delenv("OUTSIDE_TEST_KEY")
    with pytest.raises(OperatorError, match="OUTSIDE_TEST_KEY is not set"):
        read_secret_variable("OUTSIDE_TEST_KEY")


def test_the_target_takes_names_and_passwords_from_the_two_role_urls() -> None:
    target = DatabaseTarget.from_urls(OWNER, APP)
    assert (target.database, target.owner_role, target.app_role) == (
        "opx_outside",
        "opx_outside_owner",
        "opx_outside_app",
    )
    assert (target.owner_password, target.app_password) == ("owner-secret-1", "app-secret-2")
    assert "secret" not in repr(target)


@pytest.mark.parametrize(
    ("owner", "app", "fragment"),
    [
        (OWNER, APP.rsplit("/", 1)[0] + "/other_outside", "same database"),
        (OWNER, OWNER, "must be named"),
        (OWNER.replace("opx_outside_owner", "someone"), APP, "must be named"),
        (OWNER, APP.replace(":app-secret-2", ""), "password"),
        (OWNER.rsplit("/", 1)[0] + "/", APP, "same database"),
    ],
)
def test_inconsistent_urls_are_refused(owner: str, app: str, fragment: str) -> None:
    with pytest.raises(OperatorError, match=fragment) as raised:
        DatabaseTarget.from_urls(owner, app)
    assert "secret" not in raised.value.sentence


@pytest.mark.parametrize(
    ("url", "fragment"),
    [
        ("not a url", "malformed"),
        ("mysql://u:p@h/db", "postgresql://"),
        ("mssql://u:p@h/db", "postgresql://"),
    ],
)
def test_only_postgresql_urls_are_accepted(url: str, fragment: str) -> None:
    with pytest.raises(OperatorError, match=fragment):
        sqlalchemy_url(url)


def test_a_postgresql_url_uses_the_psycopg_driver() -> None:
    assert sqlalchemy_url(OWNER).drivername == "postgresql+psycopg"
    assert sqlalchemy_url(OWNER.replace("postgresql://", "postgresql+psycopg://")).database == "opx_outside"
