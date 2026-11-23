"""Settings from the environment, database URLs and the one sentence errors (no I/O)."""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from onepledge_outside_records.domain.errors import OperatorError
from onepledge_outside_records.infrastructure.db.engines import DatabaseTarget, sqlalchemy_url
from onepledge_outside_records.infrastructure.settings import (
    AdminSettings,
    ServeSettings,
    read_secret_variable,
)

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


def test_serve_reads_its_four_variables() -> None:
    settings = ServeSettings.from_env(full())
    assert settings.require_database_url() == APP
    assert settings.require_port() == 8200
    assert (settings.bind_host, settings.log_level) == ("192.0.2.10", "DEBUG")


@pytest.mark.parametrize(
    ("command", "methods"),
    [
        (
            "provision",
            {"require_admin_url": ADMIN, "require_migration_url": OWNER, "require_database_url": APP},
        ),
        ("migrate", {"require_migration_url": OWNER, "require_app_role": "opx_outside_app"}),
        ("seed", {"require_migration_url": OWNER}),
        ("clients", {"require_migration_url": OWNER}),
    ],
)
def test_each_admin_command_reads_only_the_variables_it_needs(command: str, methods: dict[str, str]) -> None:
    settings = AdminSettings.from_env(full(), command)
    for method, value in methods.items():
        assert getattr(settings, method)() == value
    for method, variable in ADMIN_REQUIREMENTS.items():
        if method not in methods:
            with pytest.raises(OperatorError, match=variable):
                getattr(settings, method)()


def test_the_serving_settings_hold_no_owner_or_admin_credential() -> None:
    settings = ServeSettings.from_env(full())
    assert set(ServeSettings.model_fields) == {"bind_host", "port", "database_url", "log_level"}
    reachable = [repr(value) for value in vars(settings).values()]
    reachable += [
        value.get_secret_value() for value in vars(settings).values() if isinstance(value, SecretStr)
    ]
    text = " ".join(reachable) + repr(settings) + settings.model_dump_json()
    for secret in ("owner-secret-1", "admin-secret-3", OWNER, ADMIN):
        assert secret not in text


def test_defaults_apply_when_optional_values_are_absent_or_blank() -> None:
    settings = ServeSettings.from_env({"BIND_HOST": "  ", "LOG_LEVEL": ""})
    assert (settings.bind_host, settings.log_level) == ("127.0.0.1", "INFO")


@pytest.mark.parametrize(
    ("method", "variable"), [("require_database_url", "DATABASE_URL"), ("require_port", "PORT")]
)
def test_a_missing_serving_value_is_named_in_one_sentence(method: str, variable: str) -> None:
    with pytest.raises(OperatorError) as raised:
        getattr(ServeSettings.from_env({}), method)()
    assert raised.value.sentence == f"Configuration error: {variable} is required but not set."
    assert raised.value.code == "CONFIGURATION_ERROR"


ADMIN_REQUIREMENTS = {
    "require_database_url": "DATABASE_URL",
    "require_migration_url": "MIGRATION_DATABASE_URL",
    "require_admin_url": "PG_ADMIN_URL",
    "require_app_role": "DB_APP_ROLE",
}


@pytest.mark.parametrize(("method", "variable"), ADMIN_REQUIREMENTS.items())
def test_a_missing_admin_value_is_named_in_one_sentence(method: str, variable: str) -> None:
    with pytest.raises(OperatorError) as raised:
        getattr(AdminSettings.from_env({}, "provision"), method)()
    assert raised.value.sentence == f"Configuration error: {variable} is required but not set."


@pytest.mark.parametrize(
    ("variable", "value", "fragment"),
    [
        ("PORT", "0", "PORT"),
        ("PORT", "65536", "PORT"),
        ("PORT", "80a", "PORT"),
        ("PORT", "-1", "PORT"),
        ("PORT", "\u0668\u0660", "PORT"),
        ("LOG_LEVEL", "LOUD", "LOG_LEVEL"),
    ],
)
def test_a_malformed_serving_value_is_named_but_never_shown(variable: str, value: str, fragment: str) -> None:
    with pytest.raises(OperatorError) as raised:
        ServeSettings.from_env({variable: value})
    assert fragment in raised.value.sentence
    assert value not in raised.value.sentence


@pytest.mark.parametrize("value", ["App Role", 'x"; DROP ROLE postgres; --'])
def test_a_malformed_role_is_named_but_never_shown(value: str) -> None:
    with pytest.raises(OperatorError) as raised:
        AdminSettings.from_env({"DB_APP_ROLE": value}, "migrate")
    assert "DB_APP_ROLE" in raised.value.sentence and value not in raised.value.sentence


def test_secrets_never_appear_in_a_repr_or_a_dump() -> None:
    for settings in (ServeSettings.from_env(full()), AdminSettings.from_env(full(), "provision")):
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


# SPEC_DECISIONS 43.3: every setting is validated in the loader, so a malformed database URL is
# one sentence naming its variable, never the value and never the last resort sentence.
MALFORMED_URLS = [
    "notaurl",
    "mysql://user:secret-pw@localhost/db",
    "postgresql://user:secret-pw@localhost:99999/db",
    "postgresql://user:secret-pw@localhost:0/db",
    "postgresql://user:secret-pw@localhost:abc/db",
]


@pytest.mark.parametrize("url", [*MALFORMED_URLS, "postgresql://user:secret-pw@localhost:5432/"])
def test_a_malformed_serving_database_url_names_the_variable(url: str) -> None:
    with pytest.raises(OperatorError) as raised:
        ServeSettings.from_env({"DATABASE_URL": url})
    assert raised.value.sentence == (
        "Configuration error: DATABASE_URL must be a postgresql://USER:PASSWORD@HOST:PORT/DATABASE URL."
    )
    assert "secret-pw" not in raised.value.sentence


@pytest.mark.parametrize("variable", ["DATABASE_URL", "MIGRATION_DATABASE_URL", "PG_ADMIN_URL"])
@pytest.mark.parametrize("url", MALFORMED_URLS)
def test_a_malformed_administrative_database_url_names_the_variable(variable: str, url: str) -> None:
    with pytest.raises(OperatorError) as raised:
        AdminSettings.from_env({variable: url}, "provision")
    assert raised.value.sentence.startswith(f"Configuration error: {variable} must be a postgresql://")
    assert "secret-pw" not in raised.value.sentence


def test_an_admin_url_may_omit_the_database_name() -> None:
    settings = AdminSettings.from_env({"PG_ADMIN_URL": "postgresql://postgres@localhost:55432"}, "provision")
    assert settings.require_admin_url() == "postgresql://postgres@localhost:55432"


# SPEC_DECISIONS 66: ASCII trimming only; other whitespace or a control character is malformed.
@pytest.mark.parametrize("bad", ["\u00a0", "\u2028", "\x07", "\u200b", "\t"])
@pytest.mark.parametrize("variable", ["DATABASE_URL", "PORT", "BIND_HOST", "LOG_LEVEL"])
def test_a_serving_value_with_other_whitespace_or_a_control_character_is_named(
    variable: str, bad: str
) -> None:
    values = {"DATABASE_URL": APP, "PORT": "8200", "BIND_HOST": "127.0.0.1", "LOG_LEVEL": "INFO"}
    environ = dict(values)
    environ[variable] = values[variable][:2] + bad + values[variable][2:]
    with pytest.raises(OperatorError) as raised:
        ServeSettings.from_env(environ)
    assert raised.value.sentence == (
        f"Configuration error: {variable} contains whitespace other than a space, or a control character."
    )
    assert "secret" not in raised.value.sentence


def test_a_client_key_is_trimmed_and_checked_like_every_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OUTSIDE_TEST_KEY", "opk_abc\n")
    assert read_secret_variable("OUTSIDE_TEST_KEY") == "opk_abc"
    monkeypatch.setenv("OUTSIDE_TEST_KEY", "opk_a\u00a0bc")
    with pytest.raises(OperatorError) as raised:
        read_secret_variable("OUTSIDE_TEST_KEY")
    assert "OUTSIDE_TEST_KEY contains whitespace other than a space" in raised.value.sentence


# SPEC_DECISIONS 103: the two role URLs carry no query parameters (review 4 failure 2, first case).
@pytest.mark.parametrize("query", ["?options=-csearch_path%3Dpg_temp", "?sslmode=disable", "?x"])
@pytest.mark.parametrize("variable", ["DATABASE_URL", "MIGRATION_DATABASE_URL"])
def test_a_role_url_with_query_parameters_is_refused_naming_the_variable(variable: str, query: str) -> None:
    url = (APP if variable == "DATABASE_URL" else OWNER) + query
    with pytest.raises(OperatorError) as raised:
        if variable == "DATABASE_URL":
            ServeSettings.from_env({variable: url})
        else:
            AdminSettings.from_env({variable: url}, "seed")
    assert raised.value.sentence == (
        f"Configuration error: {variable} must not carry query parameters; remove the part from ? on."
    )
    assert "pg_temp" not in raised.value.sentence and "sslmode" not in raised.value.sentence
