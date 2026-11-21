"""Settings of both doubles: required variables, validation, secret masking and vector refusal."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from onepledge_contract_fakes.errors import ConfigurationError
from onepledge_contract_fakes.key_holder.settings import KeyHolderSettings
from onepledge_contract_fakes.members import api_key_sha256
from onepledge_contract_fakes.registry.settings import RegistrySettings
from onepledge_contract_fakes.settings import EnvironmentReader
from onepledge_contract_fakes.testing import new_api_key, new_members, new_scalar

pytestmark = pytest.mark.unit

CONTROL_KEY = "c" * 40


def _members_json() -> str:
    members, _ = new_members(2)
    return json.dumps([{"member_id": m.member_id, "api_key_sha256": m.api_key_sha256} for m in members])


def key_holder_env(**overrides: str) -> dict[str, str]:
    env = {
        "HOLDER_ID": "kh1",
        "PORT": "18301",
        "FAKE_KH_KEYS": json.dumps({"1": base64.b64encode(new_scalar()).decode()}),
        "FAKE_KH_ACTIVE_VERSION": "1",
        "FAKE_KH_MEMBERS": _members_json(),
    }
    env.update(overrides)
    return env


def registry_env(**overrides: str) -> dict[str, str]:
    env = {
        "PORT": "18400",
        "FAKE_REGISTRY_SIGNING_SEED_B64": base64.b64encode(new_scalar()).decode(),
        "REGISTRY_SIGNING_KEY_ID": "registry-local-1",
        "FAKE_REGISTRY_MEMBERS": _members_json(),
        "FAKE_REGISTRY_AUDITOR_KEY_SHA256": api_key_sha256(new_api_key()),
    }
    env.update(overrides)
    return env


def test_key_holder_settings_with_defaults() -> None:
    settings = KeyHolderSettings.from_env(key_holder_env())
    assert settings.holder_id == "kh1"
    assert settings.bind_host == "127.0.0.1"
    assert settings.daily_element_cap == 1_000_000
    assert settings.test_controls is False
    assert settings.log_level == "INFO"
    config = settings.to_config()
    assert config.active_version == 1 and len(config.scalars[1]) == 32


def test_registry_settings_with_defaults() -> None:
    settings = RegistrySettings.from_env(registry_env())
    assert settings.key_version == "1.1"
    assert settings.daily_item_cap == 1_000_000
    assert settings.to_config().signing_key_id == "registry-local-1"


@pytest.mark.parametrize(
    "variable", ["HOLDER_ID", "PORT", "FAKE_KH_KEYS", "FAKE_KH_ACTIVE_VERSION", "FAKE_KH_MEMBERS"]
)
def test_key_holder_missing_variable_is_named(variable: str) -> None:
    env = key_holder_env()
    del env[variable]
    with pytest.raises(ConfigurationError) as raised:
        KeyHolderSettings.from_env(env)
    assert raised.value.variable == variable
    assert raised.value.sentence().startswith(f"Configuration error: {variable} ")


@pytest.mark.parametrize(
    "variable",
    [
        "PORT",
        "FAKE_REGISTRY_SIGNING_SEED_B64",
        "REGISTRY_SIGNING_KEY_ID",
        "FAKE_REGISTRY_MEMBERS",
        "FAKE_REGISTRY_AUDITOR_KEY_SHA256",
    ],
)
def test_registry_missing_variable_is_named(variable: str) -> None:
    env = registry_env()
    del env[variable]
    with pytest.raises(ConfigurationError) as raised:
        RegistrySettings.from_env(env)
    assert raised.value.variable == variable


@pytest.mark.parametrize(
    ("variable", "value"),
    [
        ("HOLDER_ID", "kh3"),
        ("PORT", "70000"),
        ("PORT", "eighty"),
        ("FAKE_KH_KEYS", "not json"),
        ("FAKE_KH_KEYS", json.dumps({"1": "short"})),
        ("FAKE_KH_KEYS", json.dumps({"0": base64.b64encode(new_scalar()).decode()})),
        ("FAKE_KH_KEYS", json.dumps({"1": base64.b64encode(bytes(32)).decode()})),
        ("FAKE_KH_ACTIVE_VERSION", "2"),
        ("FAKE_KH_MEMBERS", "[]"),
        ("FAKE_KH_MEMBERS", json.dumps([{"member_id": "x", "api_key_sha256": "0" * 64}])),
        ("FAKE_KH_MEMBERS", json.dumps([{"member_id": "6a1f7f8e-1b0a-4c4e-9a52-1d7f0c9c2b11"}])),
        ("FAKE_TEST_CONTROLS", "yes"),
        ("LOG_LEVEL", "LOUD"),
    ],
)
def test_key_holder_malformed_values_are_refused(variable: str, value: str) -> None:
    with pytest.raises(ConfigurationError) as raised:
        KeyHolderSettings.from_env(key_holder_env(**{variable: value}))
    assert raised.value.variable == variable
    assert value not in raised.value.sentence()


def test_duplicate_members_are_refused() -> None:
    members, _ = new_members(1)
    entry = {"member_id": members[0].member_id, "api_key_sha256": members[0].api_key_sha256}
    with pytest.raises(ConfigurationError):
        KeyHolderSettings.from_env(key_holder_env(FAKE_KH_MEMBERS=json.dumps([entry, entry])))


def test_test_controls_need_a_long_key() -> None:
    with pytest.raises(ConfigurationError) as raised:
        KeyHolderSettings.from_env(key_holder_env(FAKE_TEST_CONTROLS="true"))
    assert raised.value.variable == "FAKE_TEST_CONTROL_KEY"
    with pytest.raises(ConfigurationError):
        KeyHolderSettings.from_env(key_holder_env(FAKE_TEST_CONTROLS="true", FAKE_TEST_CONTROL_KEY="short"))
    settings = KeyHolderSettings.from_env(
        key_holder_env(FAKE_TEST_CONTROLS="true", FAKE_TEST_CONTROL_KEY=CONTROL_KEY)
    )
    assert settings.to_config().test_controls is True


def test_secrets_never_appear_in_repr() -> None:
    env = key_holder_env(FAKE_TEST_CONTROLS="true", FAKE_TEST_CONTROL_KEY=CONTROL_KEY)
    settings = KeyHolderSettings.from_env(env)
    scalar_b64 = json.loads(env["FAKE_KH_KEYS"])["1"]
    for text in (repr(settings), str(settings), repr(settings.to_config())):
        assert scalar_b64 not in text and CONTROL_KEY not in text
    renv = registry_env(FAKE_TEST_CONTROLS="true", FAKE_TEST_CONTROL_KEY=CONTROL_KEY)
    registry = RegistrySettings.from_env(renv)
    for text in (repr(registry), repr(registry.to_config())):
        assert renv["FAKE_REGISTRY_SIGNING_SEED_B64"] not in text and CONTROL_KEY not in text


def test_published_vector_secrets_are_refused(contracts_dir: Path) -> None:
    vectors = contracts_dir / "examples" / "vectors"
    tokenization = json.loads((vectors / "tokenization.json").read_text())
    signatures = json.loads((vectors / "signatures.json").read_text())
    for name in ("holder_1_scalar_b64", "holder_2_scalar_b64", "blind_scalar_b64"):
        with pytest.raises(ConfigurationError) as raised:
            KeyHolderSettings.from_env(key_holder_env(FAKE_KH_KEYS=json.dumps({"1": tokenization[name]})))
        assert "published test vector" in raised.value.problem
    with pytest.raises(ConfigurationError) as raised:
        RegistrySettings.from_env(registry_env(FAKE_REGISTRY_SIGNING_SEED_B64=signatures["signing_seed_b64"]))
    assert "published test vector" in raised.value.problem


@pytest.mark.parametrize(
    ("variable", "value"),
    [
        ("REGISTRY_SIGNING_KEY_ID", "Registry Key"),
        ("FAKE_REGISTRY_KEY_VERSION", "1"),
        ("FAKE_REGISTRY_AUDITOR_KEY_SHA256", "abc"),
        ("FAKE_REGISTRY_DAILY_ITEM_CAP", "0"),
        ("FAKE_REGISTRY_SIGNING_SEED_B64", "!!!"),
    ],
)
def test_registry_malformed_values_are_refused(variable: str, value: str) -> None:
    with pytest.raises(ConfigurationError) as raised:
        RegistrySettings.from_env(registry_env(**{variable: value}))
    assert raised.value.variable == variable


def test_environment_reader_optional_and_integer_defaults() -> None:
    env = EnvironmentReader({"A": "  ", "N": "7"})
    assert env.optional("A", "fallback") == "fallback"
    assert env.integer("N", minimum=1, maximum=9) == 7
    assert env.integer("MISSING", 3) == 3
    with pytest.raises(ConfigurationError):
        env.integer("N", minimum=8)
