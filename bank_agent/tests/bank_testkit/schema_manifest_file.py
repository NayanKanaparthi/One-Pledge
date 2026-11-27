"""Writes `schema_manifest.json` from a freshly migrated scratch database (SPEC_DECISIONS 105, 137).

Run from `bank_agent/tests` with `PG_ADMIN_URL` naming a PostgreSQL 16 superuser:

    python -m bank_testkit.schema_manifest_file

It provisions a scratch bank database, migrates it to head, reads its catalogue through the bank
agent's own engine and `catalogue()` (so with `TimeZone` UTC, whatever the server's default),
writes the file in its one canonical form, drops the database and prints each entry that changed.
`render` is the canonical form; the manifest test requires the committed file to equal it byte
for byte, so a file written by hand or on another machine cannot differ from this one.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from importlib import resources
from pathlib import Path

from bank_testkit.database import drop, new_bank_database, provision_and_migrate
from onepledge_bank_agent.infrastructure.db import schema_manifest
from onepledge_bank_agent.infrastructure.db.engines import make_engine


def render(entries: Mapping[str, str]) -> str:
    """The manifest file's text: one entry per line, sorted by name, ASCII only, a final newline."""
    return json.dumps(dict(sorted(entries.items())), indent=1, ensure_ascii=True) + "\n"


def manifest_path() -> Path:
    """The committed manifest file of the installed (editable) bank agent."""
    return Path(str(resources.files(schema_manifest.__package__).joinpath(schema_manifest.MANIFEST_FILE)))


def migrated_catalogue() -> dict[str, str]:
    """The catalogue of a scratch database migrated to head, read as the bank agent reads it."""
    database = new_bank_database("manifest")
    try:
        provision_and_migrate(database)
        engine = make_engine(database.owner_url)
        try:
            with engine.connect() as connection:
                return schema_manifest.catalogue(connection)
        finally:
            engine.dispose()
    finally:
        drop(database)


def main() -> int:
    """Write the manifest and print what changed."""
    path = manifest_path()
    before = schema_manifest.expected()
    live = migrated_catalogue()
    path.write_text(render(live), encoding="utf-8")
    changed = sorted(name for name in set(before) | set(live) if before.get(name) != live.get(name))
    sys.stdout.write(f"{path}: {len(live)} entries, {len(changed)} changed\n")
    for name in changed:
        sys.stdout.write(f"  {name}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
