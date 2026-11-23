"""The longest legal form of a contract document (SPEC_DECISIONS 125).

Built from the schema itself, so a change to the contract changes the document: every optional
property is present, every array holds its `maxItems` (an array of distinct enum values holds
every value), every string has its longest form, every integer without a maximum is the largest
the bank stores (`bigint`, SPEC_DECISIONS 100), and of an enum or a `oneOf` the longest choice is
taken. A string's longest form comes from its format or pattern in `LONGEST`; a pattern not in
the table, or an array or string without a bound, raises, so nothing unbounded is passed off as
the longest. Values that must differ (identifiers, tokens) differ: each carries a counter.
"""

from __future__ import annotations

import itertools
import json
from collections.abc import Callable, Iterator, Mapping
from typing import Any

import yaml
from jsonschema import Draft202012Validator

from bank_testkit.data import CONTRACTS
from onepledge_bank_agent.domain.storable import BIGINT_MAX, INT_MAX

type Schema = Mapping[str, Any]
type Json = Any

INSTANT = "2099-12-31T23:59:59.999999+23:59"
"""The longest RFC 3339 time the bank stores: six fraction digits, an offset, inside the range."""


def _uuid(n: int) -> str:
    return f"{n:08x}-ffff-4fff-bfff-ffffffffffff"


def _hex64(n: int) -> str:
    return f"{n:064x}"


LONGEST: Mapping[str, Callable[[int], str]] = {
    "uuid": _uuid,
    "date-time": lambda _: INSTANT,
    "^[0-9a-f]{64}$": _hex64,
    "^[A-Za-z0-9+/]{86}==$": lambda _: "A" * 86 + "==",
    "^[a-z0-9-]{1,64}$": lambda _: "a" * 64,
    "^[1-9][0-9]*\\.[1-9][0-9]*$": lambda _: f"{INT_MAX}.{INT_MAX}",  # two key versions, each an int
}
"""The longest legal string for each format or pattern the registry contract uses."""


class Longest:
    """Builds the longest legal instance of a named schema of one contract file."""

    def __init__(self, contract: str) -> None:
        document = yaml.safe_load((CONTRACTS / contract).read_text(encoding="utf-8"))
        self._components: Schema = document["components"]
        self._counter: Iterator[int] = itertools.count(1)

    def validator(self, name: str) -> Draft202012Validator:
        """A validator of the named schema, formats included."""
        schema = {"$ref": f"#/components/schemas/{name}", "components": self._components}
        return Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)

    def of(self, name: str) -> Json:
        """The longest instance of `components/schemas/<name>`."""
        return self._build(self._components["schemas"][name])

    def _resolve(self, schema: Schema) -> Schema:
        while "$ref" in schema:
            schema = self._components["schemas"][schema["$ref"].rsplit("/", 1)[1]]
        return schema

    def _build(self, schema: Schema) -> Json:
        schema = self._resolve(schema)
        if "const" in schema or "oneOf" in schema or "enum" in schema:
            return self._choice(schema)
        declared = schema.get("type")
        kind = str(next(k for k in declared if k != "null") if isinstance(declared, list) else declared)
        builders: Mapping[str, Callable[[Schema], Json]] = {
            "object": lambda s: {key: self._build(value) for key, value in s.get("properties", {}).items()},
            "array": self._array,
            "integer": lambda s: s.get("maximum", BIGINT_MAX),
            "boolean": lambda _: False,
            "string": self._string,
        }
        if kind not in builders:
            raise ValueError(f"no longest form for {json.dumps(schema)[:120]}")
        return builders[kind](schema)

    def _choice(self, schema: Schema) -> Json:
        """A constant, or the longest of the choices of a `oneOf` or an enum (never null)."""
        if "const" in schema:
            return schema["const"]
        if "oneOf" in schema:
            choices = [choice for choice in schema["oneOf"] if self._resolve(choice).get("type") != "null"]
            return max((self._build(choice) for choice in choices), key=_size)
        return max((value for value in schema["enum"] if value is not None), key=_size)

    def _array(self, schema: Schema) -> list[Json]:
        items = self._resolve(schema["items"])
        if schema.get("uniqueItems") and "enum" in items and "maxItems" not in schema:
            return [value for value in items["enum"] if value is not None]
        if "maxItems" not in schema:
            raise ValueError(f"an array without maxItems: {json.dumps(schema)[:120]}")
        return [self._build(items) for _ in range(schema["maxItems"])]

    def _string(self, schema: Schema) -> str:
        key = str(schema.get("format") if schema.get("format") in LONGEST else schema.get("pattern"))
        if key in LONGEST:
            return LONGEST[key](next(self._counter))
        if "maxLength" in schema:
            return "x" * int(schema["maxLength"])
        raise ValueError(f"a string with no known longest form: {json.dumps(schema)[:120]}")


def _size(value: Json) -> int:
    return len(json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
