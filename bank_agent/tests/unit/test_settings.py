"""Scoped settings (SPEC 3.3, SPEC_DECISIONS 22 finding 5): parsing, one sentence errors, masked secrets."""

from __future__ import annotations

import pytest

from onepledge_bank_agent.infrastructure.operator_errors import OperatorError
from onepledge_bank_agent.infrastructure.settings import AdminSettings, RuntimeSettings, Tunables

pytestmark = pytest.mark.unit

APP_URL = "postgresql://bank_app:" + "s3cret-pass" + "@localhost:55432/bank"
OWNER_URL = "postgresql://bank_owner:" + "0wner-pass" + "@localhost:55432/bank"
ADMIN_URL = "postgresql://postgres:" + "adm1n-pass" + "@localhost:55432/postgres"
FULL = {
    "BANK_CODE": "HARBOR",
    "BANK_NAME": "Harbor Bank",
    "PORT": "8101",
    "DATABASE_URL": APP_URL,
    "MIGRATION_DATABASE_URL": OWNER_URL,
    "PG_ADMIN_URL": ADMIN_URL,
    "DB_APP_ROLE": "bank_app",
    "OUTSIDE_RECORDS_URL": "http://127.0.0.1:8200",
    "OUTSIDE_RECORDS_API_KEY": "opk_" + "k" * 43,
    "CHECK_CHUNK_LOANS": "25",
    "PIPELINE_POLL_SECONDS": "0.5",
    "PIPELINE_WORKER_ENABLED": "false",
    "LOG_LEVEL": "warning",
}


def test_runtime_settings_read_the_runtime_variables() -> None:
    settings = RuntimeSettings.from_env(FULL)
    assert settings.require_bank_code() == "HARBOR" and settings.require_port() == 8101
    assert settings.require_database_url() == APP_URL
    assert settings.require_outside_records() == ("http://127.0.0.1:8200", "opk_" + "k" * 43)
    assert settings.log_level == "WARNING" and settings.bind_host == "127.0.0.1"
    assert settings.tunables.check_chunk_loans == 25 and settings.tunables.pipeline_poll_seconds == 0.5
    assert settings.tunables.pipeline_worker_enabled is False


def test_runtime_settings_cannot_hold_an_owner_or_superuser_credential() -> None:
    """The worker (and later serve) is built from an environment that holds both; neither is reachable."""
    settings = RuntimeSettings.from_env(FULL)
    assert not {"migration_database_url", "admin_url", "db_app_role"} & set(RuntimeSettings.model_fields)
    dumped = repr(settings.model_dump()) + repr(settings)
    for value in ("0wner-pass", "adm1n-pass", "bank_owner", OWNER_URL, ADMIN_URL):
        assert value not in dumped
    assert not any(hasattr(settings, name) for name in ("require_migration_url", "require_admin_url"))


def test_admin_settings_read_the_administrative_variables_only() -> None:
    settings = AdminSettings.from_env(FULL)
    assert settings.require_migration_url() == OWNER_URL and settings.require_admin_url() == ADMIN_URL
    assert settings.require_database_url() == APP_URL and settings.require_app_role() == "bank_app"
    assert settings.require_bank_code() == "HARBOR" and settings.log_level == "WARNING"
    assert not {"outside_records_api_key", "tunables", "port"} & set(AdminSettings.model_fields)


def test_defaults_are_the_spec_tunables() -> None:
    assert RuntimeSettings.from_env({}).tunables == Tunables(
        pipeline_worker_enabled=True, pipeline_poll_seconds=1.0, pipeline_max_attempts=8,
        check_chunk_loans=100,
        http_connect_timeout_seconds=2.0, http_read_timeout_seconds=10.0, title_pending_window_days=30,
        first_payment_grace_days=45, payment_window_days=90, contract_date_tolerance_days=3,
    )  # fmt: skip


def test_secrets_never_appear_in_the_repr() -> None:
    text = "".join(repr(s) + str(s) for s in (RuntimeSettings.from_env(FULL), AdminSettings.from_env(FULL)))
    for secret in ("s3cret-pass", "0wner-pass", "adm1n-pass", "k" * 43):
        assert secret not in text


REQUIRED: list[tuple[type[RuntimeSettings] | type[AdminSettings], str]] = [
    (RuntimeSettings, "require_bank_code"),
    (RuntimeSettings, "require_port"),
    (RuntimeSettings, "require_database_url"),
    (RuntimeSettings, "require_outside_records"),
    (AdminSettings, "require_bank_code"),
    (AdminSettings, "require_database_url"),
    (AdminSettings, "require_migration_url"),
    (AdminSettings, "require_admin_url"),
    (AdminSettings, "require_app_role"),
]


@pytest.mark.parametrize(("scope", "required"), REQUIRED)
def test_a_missing_value_is_one_sentence_naming_the_variable(
    scope: type[RuntimeSettings] | type[AdminSettings], required: str
) -> None:
    with pytest.raises(OperatorError) as raised:
        getattr(scope.from_env({}), required)()
    assert raised.value.sentence.startswith("Configuration error: ") and raised.value.sentence.endswith(
        "is required but not set."
    )
    assert raised.value.code == "CONFIGURATION_ERROR"


@pytest.mark.parametrize(
    ("name", "value"),
    [("BANK_CODE", "harbor"), ("PORT", "0"), ("PORT", "70000"), ("PORT", "http"), ("LOG_LEVEL", "LOUD"),
     ("OUTSIDE_RECORDS_URL", "ftp://x"), ("CHECK_CHUNK_LOANS", "0"),
     ("PIPELINE_MAX_ATTEMPTS", "-1"), ("PIPELINE_POLL_SECONDS", "never"), ("HTTP_READ_TIMEOUT_SECONDS", "0"),
     ("PIPELINE_WORKER_ENABLED", "yes")],
)  # fmt: skip
def test_a_malformed_runtime_value_names_the_variable_and_not_the_value(name: str, value: str) -> None:
    with pytest.raises(OperatorError) as raised:
        RuntimeSettings.from_env({name: value})
    assert name in raised.value.sentence and (value not in raised.value.sentence or value in ("0",))


@pytest.mark.parametrize(
    ("name", "value"), [("DB_APP_ROLE", "Bad-Role"), ("BANK_CODE", "x"), ("LOG_LEVEL", "?")]
)
def test_a_malformed_admin_value_names_the_variable(name: str, value: str) -> None:
    with pytest.raises(OperatorError) as raised:
        AdminSettings.from_env({name: value})
    assert name in raised.value.sentence


def test_each_scope_ignores_the_other_scopes_variables_even_when_malformed() -> None:
    RuntimeSettings.from_env({"DB_APP_ROLE": "Bad-Role", "MIGRATION_DATABASE_URL": "nonsense"})
    AdminSettings.from_env({"PORT": "http", "CHECK_CHUNK_LOANS": "0", "OUTSIDE_RECORDS_URL": "ftp://x"})


@pytest.mark.parametrize(
    ("value", "bound"),
    [
        ("127.0.0.1", "127.0.0.1"),
        ("0.0.0.0", "0.0.0.0"),  # noqa: S104 - the wildcard is an accepted value under test
        ("::", "::"),
        ("::1", "::1"),
        (" 10.0.0.7\n", "10.0.0.7"),
        ("", "127.0.0.1"),
    ],
)
def test_bind_host_accepts_an_ip_literal_or_a_wildcard(value: str, bound: str) -> None:
    """SPEC_DECISIONS 66: the address a socket binds to, never a name."""
    assert RuntimeSettings.from_env({**FULL, "BIND_HOST": value}).bind_host == bound


BIND_SENTENCE = (
    "Configuration error: BIND_HOST must be an IP address of this machine; "
    "set BIND_HOST to 127.0.0.1 or 0.0.0.0."
)
FULL_WIDTH_LOOPBACK = "".join(chr(0xFF10 + int(d)) for d in "127") + ".0.0.1"
UNPRINTABLE_KEYS = [
    "opk_k" + chr(0xA0) + "k",
    "opk_" + chr(9) + "kk",
    "opk_" + chr(0x200B) + "kk",
    "opk_kk" + chr(7),
]


@pytest.mark.parametrize(
    "value", ["localhost", "bank-harbor", "127.0.0.256", "1.2.3", "127.0.0.1:8101", FULL_WIDTH_LOOPBACK]
)
def test_bind_host_refuses_a_name_or_a_malformed_address_in_one_sentence(value: str) -> None:
    with pytest.raises(OperatorError) as raised:
        RuntimeSettings.from_env({**FULL, "BIND_HOST": value})
    assert raised.value.sentence == BIND_SENTENCE
    assert value not in raised.value.sentence


@pytest.mark.parametrize("value", UNPRINTABLE_KEYS)
def test_a_value_with_a_control_character_or_other_whitespace_names_its_variable(value: str) -> None:
    """SPEC_DECISIONS 66: only ASCII whitespace at the ends is trimmed; secrets included."""
    with pytest.raises(OperatorError) as raised:
        RuntimeSettings.from_env({**FULL, "OUTSIDE_RECORDS_API_KEY": value})
    assert raised.value.sentence.startswith("Configuration error: OUTSIDE_RECORDS_API_KEY must be")
    assert value not in raised.value.sentence
