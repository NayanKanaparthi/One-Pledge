"""Operator use cases: seeding the records and registering API clients (SPEC 6.3)."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from onepledge_outside_records.application.ports import ClientRegistry, RecordsSource, RecordsWriter
from onepledge_outside_records.domain.errors import OperatorError
from onepledge_outside_records.domain.records import ApiClient, RecordCounts

API_KEY_FORMAT = re.compile(r"^opk_[A-Za-z0-9_-]{43}$")
# The access log records the name as `client`, so it must pass the log value shape rule.
CLIENT_NAME_FORMAT = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class SeedRecords:
    """Replaces the three data tables from a data directory in one transaction."""

    def __init__(self, source: RecordsSource, writer: RecordsWriter) -> None:
        self._source = source
        self._writer = writer

    def run(self, data_dir: Path) -> RecordCounts:
        """Same input, same table contents."""
        return self._writer.replace_all(self._source.load(data_dir))


class RegisterClient:
    """Adds a client or replaces its key. The key itself is never stored."""

    def __init__(self, registry: ClientRegistry) -> None:
        self._registry = registry

    def run(self, name: str, api_key: str) -> ApiClient:
        """Validate the name and the key format, then store the key's SHA-256."""
        if not CLIENT_NAME_FORMAT.fullmatch(name):
            raise OperatorError("The client name must be 1 to 64 letters, digits, hyphens or underscores.")
        if not API_KEY_FORMAT.fullmatch(api_key):
            raise OperatorError("The API key must be opk_ followed by 43 url-safe characters.")
        return self._registry.upsert(name, hashlib.sha256(api_key.encode("utf-8")).hexdigest())
