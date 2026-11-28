"""Settings of `serve` from an environment mapping, and the one sentence refusals (no I/O)."""

from __future__ import annotations

import pytest
from onepledge_key_holder.domain.identity import HolderId, InvalidKeyVersionError, KeyVersion
from onepledge_key_holder.infrastructure.settings import ConfigurationError, ServeSettings

pytestmark = pytest.mark.unit


def env(**values: str) -> dict[str, str]:
    return {"HOLDER_ID": "kh1", "ACTIVE_KEY_VERSION": "1", **values}


def test_kh1_defaults_to_port_8301_on_the_loopback_address() -> None:
    settings = ServeSettings.from_env(env())
    assert settings == ServeSettings(HolderId.KH1, KeyVersion(1), "127.0.0.1", 8301)


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
    assert settings == ServeSettings(HolderId.KH2, KeyVersion(2_147_483_647), "::1", 9001)


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
