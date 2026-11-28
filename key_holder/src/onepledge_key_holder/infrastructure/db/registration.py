"""Recording the holder's configuration in its database, as the owner role (`db migrate`).

`key_versions` gets the configured active version. `api_credentials` is made equal to
MEMBER_KEY_HASHES: a listed hash is active for its member, any other stored hash is
switched off. Only SHA-256 values are written; no key and no scalar reaches the database.
Key rotation is not part of this version (SPEC N4), so a second active version is refused.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Connection, text
from sqlalchemy.exc import SQLAlchemyError

from onepledge_key_holder.domain.identity import KeyVersion
from onepledge_key_holder.infrastructure.db.engines import OperatorError, make_engine

ACTIVE = "ACTIVE"
RETIRED_READABLE = "RETIRED_READABLE"

_VERSIONS = text("SELECT key_version, status FROM key_versions ORDER BY key_version")
_ADD_VERSION = text(
    "INSERT INTO key_versions (key_version, status, created_at) VALUES (:key_version, :status, :created_at)"
)
_SWITCH_OFF = text("UPDATE api_credentials SET active = false WHERE active AND key_sha256 <> ALL(:hashes)")
_UPSERT_CREDENTIAL = text(
    "INSERT INTO api_credentials (credential_id, member_id, key_sha256, active) "
    "VALUES (:credential_id, :member_id, :key_sha256, true) "
    "ON CONFLICT (key_sha256) DO UPDATE SET member_id = EXCLUDED.member_id, active = true"
)


@dataclass(frozen=True)
class Registration:
    """What `db migrate` recorded: the active version and how many credentials are active."""

    active_key_version: KeyVersion
    active_credentials: int


class ConfigurationRegistrar:
    """Writes the active key version and the member key hashes with MIGRATION_DATABASE_URL."""

    def __init__(self, migration_url: str) -> None:
        self._migration_url = migration_url

    def register(self, version: KeyVersion, key_hashes: Mapping[str, str], now: datetime) -> Registration:
        """Record `version` as active and make the credentials equal to `key_hashes`, in one transaction."""
        engine = make_engine(self._migration_url)
        try:
            with engine.begin() as connection:
                self._register_version(connection, version, now)
                connection.execute(_SWITCH_OFF, {"hashes": list(key_hashes.values())})
                for member_id, key_hash in key_hashes.items():
                    connection.execute(
                        _UPSERT_CREDENTIAL,
                        {"credential_id": uuid.uuid4(), "member_id": member_id, "key_sha256": key_hash},
                    )
        except SQLAlchemyError:
            raise OperatorError(
                "The database of MIGRATION_DATABASE_URL refused the registration of the key version "
                "and the member key hashes; nothing was changed."
            ) from None
        finally:
            engine.dispose()
        return Registration(version, len(key_hashes))

    @staticmethod
    def _register_version(connection: Connection, version: KeyVersion, now: datetime) -> None:
        stored = {row.key_version: row.status for row in connection.execute(_VERSIONS)}
        other_active = [
            number for number, status in stored.items() if status == ACTIVE and number != version.number
        ]
        if other_active or stored.get(version.number, ACTIVE) != ACTIVE:
            raise OperatorError(
                "ACTIVE_KEY_VERSION differs from the active key version this database already records. "
                "Key rotation is not part of this version of the key holder."
            )
        if version.number not in stored:
            connection.execute(
                _ADD_VERSION, {"key_version": version.number, "status": ACTIVE, "created_at": now}
            )
