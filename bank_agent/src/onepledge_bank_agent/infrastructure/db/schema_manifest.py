"""The schema manifest: the bank database's schema as the migrations leave it at head (SPEC_DECISIONS 105).

`catalogue` renders the live catalogue of the `public` schema as one entry per object, the
object's kind and name mapped to its definition:

- `table`: whether row security is enabled and forced;
- `column`: type, nullability and default;
- `constraint`: `pg_get_constraintdef`;
- `index`: `pg_get_indexdef`;
- `trigger`: `tgtype`, `tgenabled` and the function it calls;
- `function`: whether it is SECURITY DEFINER, and the SHA-256 of its source;
- `rule`: every `pg_rewrite` entry that is not a view's own (none is expected);
- `policy`: every row security policy (none is expected);
- `relation`: every other relation (views, sequences, foreign tables), by kind;
- `type`: every type defined in the schema other than a table's row type.

`schema_manifest.json` beside this module holds the expected entries; an integration test
migrates a fresh database to head and requires its catalogue to equal the file, so the file
cannot drift from the migrations. Control `SCHEMA_MATCHES_MANIFEST` is true when the SHA-256 of
the canonical JSON of the live catalogue equals that of the file; when it is false, `differences`
names every object added, missing or changed.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from importlib import resources

from sqlalchemy import Connection, text

MANIFEST_FILE = "schema_manifest.json"
QUERIES: tuple[tuple[str, str], ...] = (
    (
        "table",
        "SELECT c.relname, CASE WHEN c.relrowsecurity THEN 'row security' ELSE 'no row security' END || "
        "CASE WHEN c.relforcerowsecurity THEN ', forced' ELSE '' END "
        "FROM pg_class c WHERE c.relnamespace = 'public'::regnamespace AND c.relkind IN ('r', 'p')",
    ),
    (
        "column",
        "SELECT c.relname || '.' || a.attname, format_type(a.atttypid, a.atttypmod) "
        "|| CASE WHEN a.attnotnull THEN ' NOT NULL' ELSE '' END "
        "|| coalesce(' DEFAULT ' || pg_get_expr(d.adbin, d.adrelid), '') "
        "|| CASE WHEN a.attidentity <> '' THEN ' IDENTITY ' || a.attidentity::text ELSE '' END "
        "FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid "
        "LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum "
        "WHERE c.relnamespace = 'public'::regnamespace AND c.relkind IN ('r', 'p') "
        "AND a.attnum > 0 AND NOT a.attisdropped",
    ),
    (
        "constraint",
        "SELECT c.relname || '.' || k.conname, pg_get_constraintdef(k.oid) FROM pg_constraint k "
        "JOIN pg_class c ON c.oid = k.conrelid WHERE k.connamespace = 'public'::regnamespace",
    ),
    (
        "index",
        "SELECT i.relname, pg_get_indexdef(i.oid) FROM pg_index x JOIN pg_class i ON i.oid = x.indexrelid "
        "WHERE i.relnamespace = 'public'::regnamespace",
    ),
    (
        "trigger",
        "SELECT c.relname || '.' || t.tgname, t.tgtype::text || ' ' || t.tgenabled::text || ' ' "
        "|| p.pronamespace::regnamespace::text || '.' || p.proname::text "
        "FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid JOIN pg_proc p ON p.oid = t.tgfoid "
        "WHERE NOT t.tgisinternal AND c.relnamespace = 'public'::regnamespace",
    ),
    (
        "function",
        "SELECT p.proname || '(' || pg_get_function_identity_arguments(p.oid) || ')', "
        "CASE WHEN p.prosecdef THEN 'SECURITY DEFINER ' ELSE '' END || encode(sha256(convert_to(p.prosrc, "
        "'UTF8')), 'hex') FROM pg_proc p WHERE p.pronamespace = 'public'::regnamespace",
    ),
    (
        "rule",
        "SELECT c.relname || '.' || r.rulename, pg_get_ruledef(r.oid) FROM pg_rewrite r "
        "JOIN pg_class c ON c.oid = r.ev_class WHERE c.relnamespace = 'public'::regnamespace "
        "AND (c.relkind <> 'v' OR r.rulename <> '_RETURN')",
    ),
    (
        "policy",
        "SELECT c.relname || '.' || p.polname, pg_get_expr(p.polqual, p.polrelid) FROM pg_policy p "
        "JOIN pg_class c ON c.oid = p.polrelid WHERE c.relnamespace = 'public'::regnamespace",
    ),
    (
        "relation",
        "SELECT c.relname::text, c.relkind::text FROM pg_class c "
        "WHERE c.relnamespace = 'public'::regnamespace AND c.relkind NOT IN ('r', 'p', 'i')",
    ),
    (
        "type",
        "SELECT t.typname::text, t.typtype::text FROM pg_type t "
        "WHERE t.typnamespace = 'public'::regnamespace AND t.typrelid = 0 AND t.typelem = 0",
    ),
)


def catalogue(connection: Connection) -> dict[str, str]:
    """Every object of the `public` schema, `kind name` to its definition."""
    found: dict[str, str] = {}
    for kind, query in QUERIES:
        for name, definition in connection.execute(text(query)):
            found[f"{kind} {name}"] = str(definition)
    return found


def expected() -> dict[str, str]:
    """The catalogue the migrations leave at head, as committed."""
    content = resources.files(__package__).joinpath(MANIFEST_FILE).read_text(encoding="utf-8")
    document = json.loads(content)
    return {str(key): str(value) for key, value in document.items()}


def digest(entries: Mapping[str, str]) -> str:
    """The SHA-256 of the canonical rendering of a catalogue."""
    rendered = json.dumps(dict(sorted(entries.items())), ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(rendered.encode("ascii")).hexdigest()


def differences(live: Mapping[str, str], wanted: Mapping[str, str]) -> list[str]:
    """Every object added, missing or changed, by name, sorted."""
    return sorted(name for name in set(live) | set(wanted) if live.get(name) != wanted.get(name))
