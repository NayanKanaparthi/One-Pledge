"""Settings of `serve` from an environment mapping, and the one sentence refusals (no I/O)."""

from __future__ import annotations

import pytest
from onepledge_registry.infrastructure.settings import ConfigurationError, ServeSettings

pytestmark = pytest.mark.unit


def test_the_registry_defaults_to_port_8400_on_the_loopback_address() -> None:
    assert ServeSettings.from_env({}) == ServeSettings("127.0.0.1", 8400)


def test_explicit_values_are_read_and_trimmed() -> None:
    assert ServeSettings.from_env({"PORT": " 9400\n", "BIND_HOST": "\t0.0.0.0 "}) == ServeSettings(
        "0.0.0.0",  # noqa: S104 - a value under test, nothing is bound here
        9400,
    )


def test_an_empty_value_counts_as_absent() -> None:
    assert ServeSettings.from_env({"PORT": "", "BIND_HOST": "  "}) == ServeSettings("127.0.0.1", 8400)


@pytest.mark.parametrize(
    ("values", "sentence"),
    [
        *[
            ({"PORT": text}, "PORT must be a whole number from 1 to 65535")
            for text in ("0", "65536", "80a", "+80", "-1", "99999999999", "\uff18\uff10")
        ],
        *[
            ({"BIND_HOST": text}, "BIND_HOST must be an IPv4 or IPv6 literal")
            for text in ("localhost", "registry.internal", "[::1]", "127.0.0.1:8400")
        ],
    ],
)
def test_a_bad_value_is_refused_by_name_without_echoing_it(values: dict[str, str], sentence: str) -> None:
    with pytest.raises(ConfigurationError) as raised:
        ServeSettings.from_env(values)
    assert raised.value.sentence.startswith(sentence)
    for value in values.values():
        assert value not in raised.value.sentence
