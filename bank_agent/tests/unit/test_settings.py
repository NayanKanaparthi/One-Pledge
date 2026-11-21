"""`Settings.from_env` (SPEC 3.1, 3.3): parsing, one sentence errors naming the variable, masked secrets."""

from __future__ import annotations

import pytest

from onepledge_bank_agent.infrastructure.operator_errors import OperatorError
from onepledge_bank_agent.infrastructure.settings import Settings, Tunables

pytestmark = pytest.mark.unit

APP_URL = "postgresql://bank_app:" + "s3cret-pass" + "@localhost:55432/bank"
FULL = {
    "BANK_CODE": "HARBOR",
    "BANK_NAME": "Harbor Bank",
    "PORT": "8101",
    "DATABASE_URL": APP_URL,
    "MIGRATION_DATABASE_URL": "postgresql://bank_owner:0wner-pass@localhost:55432/bank",
    "PG_ADMIN_URL": "postgresql://postgres@localhost:55432/postgres",
    "DB_APP_ROLE": "bank_app",
    "OUTSIDE_RECORDS_URL": "http://127.0.0.1:8200",
    "OUTSIDE_RECORDS_API_KEY": "opk_" + "k" * 43,
    "CHECK_CHUNK_LOANS": "25",
    "PIPELINE_POLL_SECONDS": "0.5",
    "PIPELINE_WORKER_ENABLED": "false",
    "LOG_LEVEL": "warning",
}


def test_every_variable_is_read_and_required_values_are_returned() -> None:
    settings = Settings.from_env(FULL)
    assert settings.require_bank_code() == "HARBOR" and settings.require_port() == 8101
    assert settings.require_database_url() == APP_URL
    assert settings.require_app_role() == "bank_app"
    assert settings.require_outside_records() == ("http://127.0.0.1:8200", "opk_" + "k" * 43)
    assert settings.log_level == "WARNING" and settings.bind_host == "127.0.0.1"
    assert settings.tunables.check_chunk_loans == 25 and settings.tunables.pipeline_poll_seconds == 0.5
    assert settings.tunables.pipeline_worker_enabled is False


def test_defaults_are_the_spec_tunables() -> None:
    assert Settings.from_env({}).tunables == Tunables(
        pipeline_worker_enabled=True, pipeline_poll_seconds=1.0, pipeline_max_attempts=8,
        check_chunk_loans=100,
        http_connect_timeout_seconds=2.0, http_read_timeout_seconds=10.0, title_pending_window_days=30,
        first_payment_grace_days=45, payment_window_days=90, contract_date_tolerance_days=3,
    )  # fmt: skip


def test_secrets_never_appear_in_the_repr() -> None:
    text = repr(Settings.from_env(FULL)) + str(Settings.from_env(FULL))
    for secret in ("s3cret-pass", "0wner-pass", "k" * 43):
        assert secret not in text


@pytest.mark.parametrize(
    "required",
    [
        "require_bank_code",
        "require_port",
        "require_database_url",
        "require_migration_url",
        "require_admin_url",
        "require_app_role",
        "require_outside_records",
    ],
)
def test_a_missing_value_is_one_sentence_naming_the_variable(required: str) -> None:
    with pytest.raises(OperatorError) as raised:
        getattr(Settings.from_env({}), required)()
    assert raised.value.sentence.startswith("Configuration error: ") and raised.value.sentence.endswith(
        "is required but not set."
    )


@pytest.mark.parametrize(
    ("name", "value"),
    [("BANK_CODE", "harbor"), ("PORT", "0"), ("PORT", "70000"), ("PORT", "http"), ("LOG_LEVEL", "LOUD"),
     ("DB_APP_ROLE", "Bad-Role"), ("OUTSIDE_RECORDS_URL", "ftp://x"), ("CHECK_CHUNK_LOANS", "0"),
     ("PIPELINE_MAX_ATTEMPTS", "-1"), ("PIPELINE_POLL_SECONDS", "never"), ("HTTP_READ_TIMEOUT_SECONDS", "0"),
     ("PIPELINE_WORKER_ENABLED", "yes")],
)  # fmt: skip
def test_a_malformed_value_names_the_variable_and_not_the_value(name: str, value: str) -> None:
    with pytest.raises(OperatorError) as raised:
        Settings.from_env({name: value})
    assert name in raised.value.sentence and (value not in raised.value.sentence or value in ("0",))
