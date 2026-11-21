"""Environment settings of the registry double (TEST DOUBLE, SPEC 3.3)."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Self

from pydantic import BaseModel, ConfigDict, SecretBytes, SecretStr

from onepledge_contract_fakes.errors import ConfigurationError
from onepledge_contract_fakes.registry.config import RegistryConfig
from onepledge_contract_fakes.registry.wire import KEY_VERSION_PATTERN
from onepledge_contract_fakes.settings import EnvironmentReader, MemberCredential, read_environment

DEFAULT_DAILY_ITEM_CAP = 1_000_000
DEFAULT_KEY_VERSION = "1.1"
_KEY_ID = re.compile(r"^[a-z0-9-]{1,64}$")


class RegistrySettings(BaseModel):
    """Immutable settings of one registry double process."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    bind_host: str
    port: int
    signing_seed: SecretBytes
    signing_key_id: str
    members: tuple[MemberCredential, ...]
    auditor_key_sha256: str
    key_version: str
    daily_item_cap: int
    test_controls: bool
    test_control_key: SecretStr | None
    log_level: str

    @classmethod
    def from_env(cls, environ: Mapping[str, str]) -> Self:
        """Build and validate the settings from an environment mapping."""
        env = EnvironmentReader(environ)
        key_id = env.required("REGISTRY_SIGNING_KEY_ID")
        if not _KEY_ID.fullmatch(key_id):
            raise ConfigurationError("REGISTRY_SIGNING_KEY_ID", "must match ^[a-z0-9-]{1,64}$")
        key_version = env.optional("FAKE_REGISTRY_KEY_VERSION", DEFAULT_KEY_VERSION)
        if not re.fullmatch(KEY_VERSION_PATTERN, key_version):
            raise ConfigurationError("FAKE_REGISTRY_KEY_VERSION", "must look like 1.1")
        controls = env.boolean("FAKE_TEST_CONTROLS", default=False)
        control_key = env.test_control_key(controls)
        return cls(
            bind_host=env.optional("BIND_HOST", "127.0.0.1"),
            port=env.port(),
            signing_seed=SecretBytes(env.secret_base64_32("FAKE_REGISTRY_SIGNING_SEED_B64")),
            signing_key_id=key_id,
            members=env.members("FAKE_REGISTRY_MEMBERS"),
            auditor_key_sha256=env.sha256_hex("FAKE_REGISTRY_AUDITOR_KEY_SHA256"),
            key_version=key_version,
            daily_item_cap=env.integer("FAKE_REGISTRY_DAILY_ITEM_CAP", DEFAULT_DAILY_ITEM_CAP, minimum=1),
            test_controls=controls,
            test_control_key=SecretStr(control_key) if control_key is not None else None,
            log_level=env.log_level(),
        )

    def to_config(self) -> RegistryConfig:
        """The configuration of the in-memory double."""
        return RegistryConfig(
            members=self.members,
            signing_seed=self.signing_seed.get_secret_value(),
            signing_key_id=self.signing_key_id,
            auditor_key_sha256=self.auditor_key_sha256,
            key_version=self.key_version,
            daily_item_cap=self.daily_item_cap,
            test_control_key=self.test_control_key.get_secret_value() if self.test_control_key else None,
        )


def load_settings() -> RegistrySettings:
    """Settings from the process environment."""
    return RegistrySettings.from_env(read_environment())
