"""Settings of `serve` from an environment mapping, and the one sentence refusals (no I/O)."""

from __future__ import annotations

import base64
import json

import pytest
from onepledge_key_holder.domain.identity import HolderId, InvalidKeyVersionError, KeyVersion
from onepledge_key_holder.infrastructure.settings import (
    ConfigurationError,
    MigrateSettings,
    ProvisionSettings,
    ServeSettings,
)

pytestmark = pytest.mark.unit

SCALAR = bytes(range(1, 33))
SCALAR_B64 = base64.b64encode(SCALAR).decode()
DATABASE_URL = "postgresql://kh1_app:app-secret@localhost:55432/kh1"
OWNER_URL = "postgresql://kh1_owner:owner-secret@localhost:55432/kh1"
MEMBER = "5d6f3c0e-8a7b-4c1d-9e2f-0a1b2c3d4e5f"
KEY_HASH = "ab" * 32


def env(**values: str) -> dict[str, str]:
    return {
        "HOLDER_ID": "kh1",
        "ACTIVE_KEY_VERSION": "1",
        "DATABASE_URL": DATABASE_URL,
        "OPRF_SCALAR_B64": SCALAR_B64,
        **values,
    }


def test_kh1_defaults_to_port_8301_on_the_loopback_address() -> None:
    settings = ServeSettings.from_env(env())
    assert settings == ServeSettings(
        HolderId.KH1, KeyVersion(1), "127.0.0.1", 8301, DATABASE_URL, SCALAR, 1_000_000
    )


def test_kh2_defaults_to_port_8302() -> None:
    settings = ServeSettings.from_env(env(HOLDER_ID="kh2", ACTIVE_KEY_VERSION="7"))
    assert (settings.holder_id, settings.active_key_version, settings.port) == (
        HolderId.KH2,
        KeyVersion(7),
        8302,
    )


def test_explicit_values_are_read_and_trimmed() -> None:
    settings = ServeSettings.from_env(
        env(HOLDER_ID=" kh2\n", ACTIVE_KEY_VERSION="\t2147483647 ", PORT=" 9001 ", BIND_HOST="::1")
    )
    assert settings == ServeSettings(
        HolderId.KH2, KeyVersion(2_147_483_647), "::1", 9001, DATABASE_URL, SCALAR, 1_000_000
    )


def test_an_empty_optional_value_counts_as_absent() -> None:
    settings = ServeSettings.from_env(env(PORT="  ", BIND_HOST=""))
    assert (settings.bind_host, settings.port) == ("127.0.0.1", 8301)


@pytest.mark.parametrize(
    ("values", "sentence"),
    [
        ({"HOLDER_ID": ""}, "HOLDER_ID is required."),
        ({"HOLDER_ID": "kh3"}, "HOLDER_ID must be kh1 or kh2."),
        ({"HOLDER_ID": "KH1"}, "HOLDER_ID must be kh1 or kh2."),
        ({"ACTIVE_KEY_VERSION": " "}, "ACTIVE_KEY_VERSION is required."),
        *[
            (
                {"ACTIVE_KEY_VERSION": text},
                "ACTIVE_KEY_VERSION must be a whole number from 1 to 2,147,483,647",
            )
            for text in ("0", "2147483648", "+1", "-1", "1.0", "1e3", "\u0663", "one", "99999999999")
        ],
        *[
            ({"PORT": text}, "PORT must be a whole number from 1 to 65535")
            for text in ("0", "65536", "80a", "+80", "\uff18\uff10")
        ],
        *[
            ({"BIND_HOST": text}, "BIND_HOST must be an IPv4 or IPv6 literal")
            for text in ("localhost", "example.org", "127.0.0.1:80", "999.1.1.1")
        ],
    ],
)
def test_a_bad_value_is_refused_by_name_without_echoing_it(values: dict[str, str], sentence: str) -> None:
    with pytest.raises(ConfigurationError) as raised:
        ServeSettings.from_env(env(**values))
    assert raised.value.sentence.startswith(sentence)
    for value in values.values():
        if value.strip():
            assert value.strip() not in raised.value.sentence


def test_a_missing_holder_id_is_refused() -> None:
    with pytest.raises(ConfigurationError, match=r"^HOLDER_ID is required\.$"):
        ServeSettings.from_env({"ACTIVE_KEY_VERSION": "1"})


@pytest.mark.parametrize("number", [0, -1, 2_147_483_648])
def test_a_key_version_outside_the_contract_range_is_refused(number: int) -> None:
    with pytest.raises(InvalidKeyVersionError):
        KeyVersion(number)


def test_a_bool_is_not_a_key_version() -> None:
    with pytest.raises(InvalidKeyVersionError):
        KeyVersion(True)


def test_the_scalar_the_database_url_and_the_cap_are_read() -> None:
    settings = ServeSettings.from_env(env(DAILY_ELEMENT_CAP=" 250 ", OPRF_SCALAR_B64=f" {SCALAR_B64}\n"))
    assert (settings.scalar, settings.database_url, settings.daily_element_cap) == (SCALAR, DATABASE_URL, 250)


def test_the_settings_never_show_the_scalar_or_a_database_password() -> None:
    shown = repr(ServeSettings.from_env(env()))
    for secret in (SCALAR_B64, SCALAR.hex(), repr(SCALAR), "app-secret", DATABASE_URL):
        assert secret not in shown
    admin = "postgresql://postgres:admin-secret@localhost:55432/postgres"
    provision = ProvisionSettings.from_env(
        {"PG_ADMIN_URL": admin, "MIGRATION_DATABASE_URL": OWNER_URL, "DATABASE_URL": DATABASE_URL}
    )
    assert "secret" not in repr(provision)
    assert (provision.admin_url, provision.migration_url, provision.database_url) == (
        admin,
        OWNER_URL,
        DATABASE_URL,
    )


SCALAR_SENTENCE = "OPRF_SCALAR_B64 must be the standard base64 of 32 bytes that are not all zeros."


@pytest.mark.parametrize(
    "text",
    [
        base64.b64encode(bytes(32)).decode(),
        base64.b64encode(bytes(31) + b"\x01").decode()[:-4],
        base64.b64encode(b"\x01" * 31).decode(),
        base64.b64encode(b"\x01" * 33).decode(),
        "not base64",
        SCALAR_B64[:-1],
        SCALAR_B64[:42] + "B=",
        base64.urlsafe_b64encode(bytes([251]) * 32).decode(),
        SCALAR.hex(),
    ],
)
def test_startup_refuses_a_scalar_that_is_not_32_bytes_or_is_all_zeros(text: str) -> None:
    with pytest.raises(ConfigurationError) as raised:
        ServeSettings.from_env(env(OPRF_SCALAR_B64=text))
    assert raised.value.sentence == SCALAR_SENTENCE
    assert text not in raised.value.sentence


@pytest.mark.parametrize(
    ("values", "sentence"),
    [
        ({"OPRF_SCALAR_B64": ""}, "OPRF_SCALAR_B64 is required."),
        ({"DATABASE_URL": " "}, "DATABASE_URL is required."),
        ({"DATABASE_URL": "mysql://u:p@localhost/kh1"}, "DATABASE_URL must be a postgresql://"),
        ({"DATABASE_URL": "postgresql://u:p@localhost:55432"}, "DATABASE_URL must be a postgresql://"),
        ({"DATABASE_URL": DATABASE_URL + "?options=-c%20x"}, "DATABASE_URL must be a postgresql://"),
        ({"DATABASE_URL": "kh1"}, "DATABASE_URL must be a postgresql://"),
        *[
            ({"DAILY_ELEMENT_CAP": text}, "DAILY_ELEMENT_CAP must be a whole number from 1 to 2,147,483,647")
            for text in ("0", "-1", "2147483648", "1e3", "ten")
        ],
    ],
)
def test_a_bad_database_url_scalar_or_cap_is_refused_by_name_without_echoing_it(
    values: dict[str, str], sentence: str
) -> None:
    with pytest.raises(ConfigurationError) as raised:
        ServeSettings.from_env(env(**values))
    assert raised.value.sentence.startswith(sentence)
    for value in values.values():
        if value.strip():
            assert value.strip() not in raised.value.sentence


def migrate_env(**values: str) -> dict[str, str]:
    return {
        "MIGRATION_DATABASE_URL": OWNER_URL,
        "ACTIVE_KEY_VERSION": "1",
        "MEMBER_KEY_HASHES": json.dumps({MEMBER: KEY_HASH}),
        **values,
    }


def test_db_migrate_reads_the_owner_url_the_key_version_and_the_member_key_hashes() -> None:
    settings = MigrateSettings.from_env(migrate_env())
    assert (settings.migration_url, settings.active_key_version) == (OWNER_URL, KeyVersion(1))
    assert dict(settings.member_key_hashes) == {MEMBER: KEY_HASH}
    assert "owner-secret" not in repr(settings)
    assert dict(MigrateSettings.from_env(migrate_env(MEMBER_KEY_HASHES="{}")).member_key_hashes) == {}


OTHER = "6e7f4d1f-9b8c-4d2e-8f30-1b2c3d4e5f60"


@pytest.mark.parametrize(
    "text",
    [
        "[]",
        "nope",
        '"text"',
        json.dumps({MEMBER.upper(): KEY_HASH}),
        json.dumps({"harbor": KEY_HASH}),
        json.dumps({MEMBER: KEY_HASH.upper()}),
        json.dumps({MEMBER: KEY_HASH[:-1]}),
        json.dumps({MEMBER: 5}),
        json.dumps({MEMBER: KEY_HASH, OTHER: KEY_HASH}),
        f'{{"{MEMBER}": "{KEY_HASH}", "{MEMBER}": "{"cd" * 32}"}}',
    ],
)
def test_member_key_hashes_must_be_ids_to_sha256_values_with_nothing_repeated(text: str) -> None:
    with pytest.raises(ConfigurationError) as raised:
        MigrateSettings.from_env(migrate_env(MEMBER_KEY_HASHES=text))
    assert raised.value.sentence.startswith("MEMBER_KEY_HASHES must be a JSON object of member id")
    assert KEY_HASH not in raised.value.sentence and MEMBER not in raised.value.sentence


def test_db_migrate_needs_each_of_its_variables() -> None:
    for name in ("MIGRATION_DATABASE_URL", "ACTIVE_KEY_VERSION", "MEMBER_KEY_HASHES"):
        with pytest.raises(ConfigurationError, match=rf"^{name} is required\.$"):
            MigrateSettings.from_env({key: value for key, value in migrate_env().items() if key != name})
