"""Environment settings of the key holder double (TEST DOUBLE, SPEC 3.3)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, SecretBytes, SecretStr

from onepledge_contract_fakes.errors import ConfigurationError
from onepledge_contract_fakes.key_holder.config import KeyHolderConfig
from onepledge_contract_fakes.settings import EnvironmentReader, MemberCredential, read_environment

DEFAULT_DAILY_ELEMENT_CAP = 1_000_000


class KeyHolderSettings(BaseModel):
    """Immutable settings of one key holder double process."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    holder_id: Literal["kh1", "kh2"]
    bind_host: str
    port: int
    scalars: dict[int, SecretBytes]
    active_version: int
    members: tuple[MemberCredential, ...]
    daily_element_cap: int
    test_controls: bool
    test_control_key: SecretStr | None
    log_level: str

    @classmethod
    def from_env(cls, environ: Mapping[str, str]) -> Self:
        """Build and validate the settings from an environment mapping."""
        env = EnvironmentReader(environ)
        holder_id = env.required("HOLDER_ID")
        if holder_id not in ("kh1", "kh2"):
            raise ConfigurationError("HOLDER_ID", "must be kh1 or kh2")
        holder: Literal["kh1", "kh2"] = "kh1" if holder_id == "kh1" else "kh2"
        scalars = _scalars(env)
        active = env.integer("FAKE_KH_ACTIVE_VERSION", minimum=1)
        if active not in scalars:
            raise ConfigurationError("FAKE_KH_ACTIVE_VERSION", "must name a version present in FAKE_KH_KEYS")
        controls = env.boolean("FAKE_TEST_CONTROLS", default=False)
        control_key = env.test_control_key(controls)
        return cls(
            holder_id=holder,
            bind_host=env.optional("BIND_HOST", "127.0.0.1"),
            port=env.port(),
            scalars={version: SecretBytes(value) for version, value in scalars.items()},
            active_version=active,
            members=env.members("FAKE_KH_MEMBERS"),
            daily_element_cap=env.integer("FAKE_KH_DAILY_ELEMENT_CAP", DEFAULT_DAILY_ELEMENT_CAP, minimum=1),
            test_controls=controls,
            test_control_key=SecretStr(control_key) if control_key is not None else None,
            log_level=env.log_level(),
        )

    def to_config(self) -> KeyHolderConfig:
        """The configuration of the in-memory double."""
        return KeyHolderConfig(
            holder_id=self.holder_id,
            scalars={version: secret.get_secret_value() for version, secret in self.scalars.items()},
            active_version=self.active_version,
            members=self.members,
            daily_element_cap=self.daily_element_cap,
            test_control_key=self.test_control_key.get_secret_value() if self.test_control_key else None,
        )


def _scalars(env: EnvironmentReader) -> dict[int, bytes]:
    document = env.json_value("FAKE_KH_KEYS")
    if not isinstance(document, dict) or not document:
        raise ConfigurationError("FAKE_KH_KEYS", "must be a non-empty JSON object of versions to scalars")
    scalars: dict[int, bytes] = {}
    for version_text, value in document.items():
        if not version_text.isdigit() or int(version_text) < 1 or not isinstance(value, str):
            raise ConfigurationError("FAKE_KH_KEYS", "must map positive integer versions to base64 strings")
        scalar = EnvironmentReader({"FAKE_KH_KEYS": value}).secret_base64_32("FAKE_KH_KEYS")
        if scalar == bytes(32):
            raise ConfigurationError("FAKE_KH_KEYS", "must not contain a zero scalar")
        scalars[int(version_text)] = scalar
    return scalars


def load_settings() -> KeyHolderSettings:
    """Settings from the process environment."""
    return KeyHolderSettings.from_env(read_environment())
