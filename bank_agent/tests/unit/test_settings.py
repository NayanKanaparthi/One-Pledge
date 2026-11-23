"""Scoped settings (SPEC 3.3, SPEC_DECISIONS 22 finding 5): parsing, one sentence errors, masked secrets."""

from __future__ import annotations

import pytest

from onepledge_bank_agent.infrastructure.operator_errors import OperatorError
from onepledge_bank_agent.infrastructure.settings import (
    AdminSettings,
    RuntimeSettings,
    ServeSettings,
    Tunables,
)
from onepledge_testtools.network import WILDCARD_IPV4

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


IDENTITY = {"BANK_CODE": "HARBOR", "BANK_NAME": "Harbor Bank"}


def test_runtime_settings_read_the_runtime_variables() -> None:
    settings = RuntimeSettings.from_env(FULL)
    assert (settings.bank_code, settings.bank_name) == ("HARBOR", "Harbor Bank")
    assert settings.require_database_url() == APP_URL
    assert settings.require_outside_records() == ("http://127.0.0.1:8200", "opk_" + "k" * 43)
    assert settings.log_level == "WARNING"
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


@pytest.mark.parametrize("name", ["BANK_CODE", "BANK_NAME"])
def test_every_runtime_command_requires_the_bank_code_and_name(name: str) -> None:
    """SPEC_DECISIONS 93, item 11: a log record must say which bank it belongs to."""
    with pytest.raises(OperatorError) as raised:
        RuntimeSettings.from_env({k: v for k, v in FULL.items() if k != name})
    assert raised.value.sentence == f"Configuration error: {name} is required but not set."


@pytest.mark.parametrize(("name", "value"), [("BANK_NAME", "x" * 101), ("BANK_NAME", "Bank" + chr(0xE9))])
def test_a_bank_name_is_printable_ascii_of_at_most_100_characters(name: str, value: str) -> None:
    with pytest.raises(OperatorError) as raised:
        RuntimeSettings.from_env({**FULL, name: value})
    assert (
        raised.value.sentence == "Configuration error: BANK_NAME must be 1 to 100 printable ASCII characters."
    )


def test_no_runtime_command_reads_bind_host_or_port() -> None:
    """SPEC_DECISIONS 73, point 1: a wrong BIND_HOST or PORT cannot stop a worker or a one-shot command."""
    settings = RuntimeSettings.from_env({**FULL, "BIND_HOST": "localhost", "PORT": "http"})
    assert not {"bind_host", "port"} & set(RuntimeSettings.model_fields)
    assert settings.bank_code == "HARBOR"


def test_serve_settings_read_bind_host_and_port_beside_the_runtime_settings() -> None:
    serve = ServeSettings.from_env(FULL)
    assert (serve.bind_host, serve.port, serve.runtime) == ("127.0.0.1", 8101, RuntimeSettings.from_env(FULL))
    with pytest.raises(OperatorError) as raised:
        ServeSettings.from_env({k: v for k, v in FULL.items() if k != "PORT"})
    assert raised.value.sentence == "Configuration error: PORT is required but not set."


def test_defaults_are_the_spec_tunables() -> None:
    assert RuntimeSettings.from_env(IDENTITY).tunables == Tunables(
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
        getattr(scope.from_env(IDENTITY if scope is RuntimeSettings else {}), required)()
    assert raised.value.sentence.startswith("Configuration error: ") and raised.value.sentence.endswith(
        "is required but not set."
    )
    assert raised.value.code == "CONFIGURATION_ERROR"


@pytest.mark.parametrize(
    ("name", "value"),
    [("BANK_CODE", "harbor"), ("LOG_LEVEL", "LOUD"),
     ("OUTSIDE_RECORDS_URL", "ftp://x"), ("CHECK_CHUNK_LOANS", "0"),
     ("PIPELINE_MAX_ATTEMPTS", "-1"), ("PIPELINE_POLL_SECONDS", "never"), ("HTTP_READ_TIMEOUT_SECONDS", "0"),
     ("PIPELINE_WORKER_ENABLED", "yes")],
)  # fmt: skip
def test_a_malformed_runtime_value_names_the_variable_and_not_the_value(name: str, value: str) -> None:
    with pytest.raises(OperatorError) as raised:
        RuntimeSettings.from_env({**IDENTITY, name: value})
    assert name in raised.value.sentence and (value not in raised.value.sentence or value in ("0",))


@pytest.mark.parametrize(
    ("name", "value"), [("DB_APP_ROLE", "Bad-Role"), ("BANK_CODE", "x"), ("LOG_LEVEL", "?")]
)
def test_a_malformed_admin_value_names_the_variable(name: str, value: str) -> None:
    with pytest.raises(OperatorError) as raised:
        AdminSettings.from_env({name: value})
    assert name in raised.value.sentence


def test_each_scope_ignores_the_other_scopes_variables_even_when_malformed() -> None:
    RuntimeSettings.from_env({**IDENTITY, "DB_APP_ROLE": "Bad-Role", "MIGRATION_DATABASE_URL": "nonsense"})
    AdminSettings.from_env({"PORT": "http", "CHECK_CHUNK_LOANS": "0", "OUTSIDE_RECORDS_URL": "ftp://x"})


@pytest.mark.parametrize(
    ("value", "bound"),
    [
        ("127.0.0.1", "127.0.0.1"),
        (WILDCARD_IPV4, WILDCARD_IPV4),
        ("::", "::"),
        ("::1", "::1"),
        (" 10.0.0.7\n", "10.0.0.7"),
        ("", "127.0.0.1"),
    ],
)
def test_bind_host_accepts_an_ip_literal_or_a_wildcard(value: str, bound: str) -> None:
    """SPEC_DECISIONS 66: the address a socket binds to, never a name."""
    assert ServeSettings.from_env({**FULL, "BIND_HOST": value}).bind_host == bound


BIND_SENTENCE = (
    "Configuration error: BIND_HOST must be an IPv4 or IPv6 address; set it to 127.0.0.1 or 0.0.0.0."
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
        ServeSettings.from_env({**FULL, "BIND_HOST": value})
    assert raised.value.sentence == BIND_SENTENCE
    assert value not in raised.value.sentence


@pytest.mark.parametrize("value", ["0", "70000", "http"])
def test_a_malformed_port_names_the_variable(value: str) -> None:
    with pytest.raises(OperatorError) as raised:
        ServeSettings.from_env({**FULL, "PORT": value})
    assert (
        raised.value.sentence.startswith("Configuration error: PORT ") and value not in raised.value.sentence
    )


DATABASE_URL_SENTENCE = (
    "Configuration error: DATABASE_URL must be a postgresql://USER:PASSWORD@HOST:PORT/DATABASE URL."
)
SHARED = {
    ("DATABASE_URL", "nonsense"): DATABASE_URL_SENTENCE,
    ("DATABASE_URL", "mysql://app:secret@localhost:5432/bank"): DATABASE_URL_SENTENCE,
    ("DATABASE_URL", "postgresql://app:secret@localhost:99999/bank"): DATABASE_URL_SENTENCE,
    ("DATABASE_URL", "postgresql://app:secret@localhost:5432"): DATABASE_URL_SENTENCE,
    ("LOG_LEVEL", "IN\u2028FO"): (
        "Configuration error: LOG_LEVEL contains whitespace other than a space, or a control character."
    ),
    ("PORT", "+80"): "Configuration error: PORT must be a whole number written with the digits 0 to 9.",
    ("PORT", "70000"): "Configuration error: PORT is out of range (1 to 65,535).",
    ("LOG_LEVEL", "LOUD"): "Configuration error: LOG_LEVEL must be DEBUG, INFO, WARNING, ERROR or CRITICAL.",
    ("BIND_HOST", "localhost"): BIND_SENTENCE,
    ("DATABASE_URL", APP_URL + "?options=-c%20session_replication_role%3Dreplica"): (
        "Configuration error: DATABASE_URL must not carry query parameters; remove the part from ? on."
    ),
    ("DATABASE_URL", APP_URL + "?"): (
        "Configuration error: DATABASE_URL must not carry query parameters; remove the part from ? on."
    ),
}
"""The sentences every service words the same way (SPEC_DECISIONS 78), as outside records and the doubles."""


@pytest.mark.parametrize(("setting", "sentence"), list(SHARED.items()), ids=[" ".join(k) for k in SHARED])
def test_a_shared_setting_has_the_sentence_of_every_service(setting: tuple[str, str], sentence: str) -> None:
    name, value = setting
    with pytest.raises(OperatorError) as raised:
        ServeSettings.from_env({**FULL, name: value})
    assert raised.value.sentence == sentence and value not in sentence.removeprefix("Configuration error: ")
    assert not sentence.endswith(":")


@pytest.mark.parametrize(
    ("name", "value", "sentence"),
    [
        ("CHECK_CHUNK_LOANS", "1001", "Configuration error: CHECK_CHUNK_LOANS is out of range (1 to 1,000)."),
        ("PIPELINE_MAX_ATTEMPTS", "1" + "0" * 18,
         "Configuration error: PIPELINE_MAX_ATTEMPTS is out of range (1 to 100)."),
        ("PIPELINE_POLL_SECONDS", "4000", "Configuration error: PIPELINE_POLL_SECONDS is out of range "
         "(0.001 to 3,600 seconds)."),
        ("PIPELINE_POLL_SECONDS", "0", "Configuration error: PIPELINE_POLL_SECONDS is out of range "
         "(0.001 to 3,600 seconds)."),
        ("OUTBOX_POLL_SECONDS", "9" * 40, "Configuration error: OUTBOX_POLL_SECONDS is out of range "
         "(0.001 to 3,600 seconds)."),
        ("OUTBOX_POLL_SECONDS", "0003600.000", None),
        ("HTTP_READ_TIMEOUT_SECONDS", "1.2345", "Configuration error: HTTP_READ_TIMEOUT_SECONDS must be a "
         "number of seconds written with the digits 0 to 9, with at most three decimals."),
        ("HTTP_READ_TIMEOUT_SECONDS", "\u0661", "Configuration error: HTTP_READ_TIMEOUT_SECONDS must be a "
         "number of seconds written with the digits 0 to 9, with at most three decimals."),
        ("PIPELINE_MAX_ATTEMPTS", "1.5", "Configuration error: PIPELINE_MAX_ATTEMPTS must be a whole number "
         "written with the digits 0 to 9."),
    ],
)  # fmt: skip
def test_a_well_formed_number_out_of_range_says_out_of_range(
    name: str, value: str, sentence: str | None
) -> None:
    """Review 2, quality finding 12; SPEC_DECISIONS 78: not "must be a whole number"."""
    if sentence is None:
        assert RuntimeSettings.from_env({**FULL, name: value}).tunables.outbox_poll_seconds == 3600
        return
    with pytest.raises(OperatorError) as raised:
        RuntimeSettings.from_env({**FULL, name: value})
    assert raised.value.sentence == sentence


@pytest.mark.parametrize("value", UNPRINTABLE_KEYS)
def test_a_value_with_a_control_character_or_other_whitespace_names_its_variable(value: str) -> None:
    """SPEC_DECISIONS 66: only ASCII whitespace at the ends is trimmed; secrets included."""
    with pytest.raises(OperatorError) as raised:
        RuntimeSettings.from_env({**FULL, "OUTSIDE_RECORDS_API_KEY": value})
    assert raised.value.sentence == (
        "Configuration error: OUTSIDE_RECORDS_API_KEY contains whitespace other than a space, "
        "or a control character."
    ), "the sentence outside records and the doubles use (SPEC_DECISIONS 78)"
    assert value not in raised.value.sentence


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("MIGRATION_DATABASE_URL", "postgres:/nowhere"),
        ("MIGRATION_DATABASE_URL", "postgresql://owner:secret@localhost:5432"),
        ("PG_ADMIN_URL", "http://postgres@localhost:5432/postgres"),
        ("PG_ADMIN_URL", "postgresql://postgres@localhost:0"),
    ],
)
def test_an_administrative_database_url_is_checked_in_the_loader(name: str, value: str) -> None:
    """SPEC_DECISIONS 43.3 and 78: the URL's form is checked where it is read, with the sentence of outside
    records; the superuser URL needs no database name, the owner URL does."""
    with pytest.raises(OperatorError) as raised:
        AdminSettings.from_env({**FULL, name: value})
    assert raised.value.sentence == (
        f"Configuration error: {name} must be a postgresql://USER:PASSWORD@HOST:PORT/DATABASE URL."
    )
    assert AdminSettings.from_env({**FULL, "PG_ADMIN_URL": "postgresql://postgres@localhost:55432"})


OUTBOUND_KEYS = (
    "OUTSIDE_RECORDS_API_KEY",
    "REGISTRY_API_KEY",
    "KEY_HOLDER_1_API_KEY",
    "KEY_HOLDER_2_API_KEY",
)


@pytest.mark.parametrize(
    ("first", "second"),
    [(a, b) for i, a in enumerate(OUTBOUND_KEYS) for b in OUTBOUND_KEYS[i + 1 :]],
)
def test_two_outbound_api_keys_that_are_equal_are_refused(first: str, second: str) -> None:
    """SPEC 16.4 and SPEC_DECISIONS 112.12: each of the four outbound keys is distinct."""
    keys = {name: f"key-{index}-" + "k" * 30 for index, name in enumerate(OUTBOUND_KEYS)}
    keys[second] = keys[first]
    with pytest.raises(OperatorError) as raised:
        RuntimeSettings.from_env({**IDENTITY, **keys})
    assert raised.value.sentence == f"Configuration error: {second} must differ from {first}."
    assert keys[first] not in raised.value.sentence
    assert RuntimeSettings.from_env({**IDENTITY, **{n: f"key-{i}" for i, n in enumerate(OUTBOUND_KEYS)}})


def test_the_owner_url_may_not_carry_query_parameters_either() -> None:
    """SPEC_DECISIONS 103: the bank agent sets its own session parameters; a URL cannot change them."""
    with pytest.raises(OperatorError) as raised:
        AdminSettings.from_env({**IDENTITY, "MIGRATION_DATABASE_URL": OWNER_URL + "?sslmode=disable"})
    assert raised.value.sentence == (
        "Configuration error: MIGRATION_DATABASE_URL must not carry query parameters; "
        "remove the part from ? on."
    )
