"""JSON Schema 2020-12 validation against the schemas of an OpenAPI 3.1 contract (TEST DOUBLES package).

Used by the checker for the examples and by contract tests for live responses.
Formats `date`, `date-time` and `uuid` are checked by this module itself, so the
result does not depend on optional format packages being installed.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date, datetime
from typing import Any

import jsonschema

from onepledge_contract_fakes.validation import is_canonical_uuid

_DATE_TIME = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]+)?(Z|[+-][0-9]{2}:[0-9]{2})$"
)
_DATE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")


def _is_date(value: object) -> bool:
    if not isinstance(value, str):
        return True
    if not _DATE.fullmatch(value):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _is_date_time(value: object) -> bool:
    if not isinstance(value, str):
        return True
    if not _DATE_TIME.fullmatch(value):
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def _is_uuid(value: object) -> bool:
    """`format: uuid` holds only the canonical lower case spelling (SPEC_DECISIONS 75)."""
    return not isinstance(value, str) or is_canonical_uuid(value)


# A name from the document is printed only when it is plain, so no output carries brackets,
# quotes or parentheses (SPEC_DECISIONS 43.11).
_PLAIN_NAME = re.compile(r"^[A-Za-z0-9_.$/-]{1,64}$")


def _names(values: object) -> str:
    items = values if isinstance(values, list | tuple | set | frozenset) else [values]
    shown = [str(item) for item in items if _PLAIN_NAME.fullmatch(str(item))]
    return ", ".join(sorted(shown)) or "a property"


def plain_problem(error: jsonschema.ValidationError) -> str:
    """`location: what is wrong` in plain words: never the jsonschema message, which quotes values."""
    parts = [str(part) for part in error.absolute_path]
    where = "/".join(part if _PLAIN_NAME.fullmatch(part) else "?" for part in parts) or "the document root"
    keyword = str(error.validator)
    expected: object = error.validator_value
    instance: object = error.instance
    schema: object = error.schema
    keys = set(instance) if isinstance(instance, dict) else set()
    if keyword == "required" and isinstance(expected, list):
        what = f"lacks the required property {_names([name for name in expected if name not in keys])}"
    elif keyword == "additionalProperties":
        declared = schema.get("properties", {}) if isinstance(schema, dict) else {}
        what = f"has a property the schema does not allow: {_names(keys - set(declared))}"
    elif keyword == "type":
        what = f"is not of type {_names(expected).replace(', ', ' or ')}"
    elif keyword == "format":
        what = f"is not a valid {_names(expected)}"
    else:
        what = _KEYWORD_PROBLEMS.get(keyword, f"fails the {_names(keyword)} rule of the schema")
        if keyword in _BOUNDED and isinstance(expected, int | float):
            what = f"{what} {expected}"
    return f"{where}: {what}"


_KEYWORD_PROBLEMS = {
    "enum": "is not one of the allowed values",
    "const": "is not the one allowed value",
    "pattern": "does not match the pattern of the schema",
    "oneOf": "does not match exactly one of the allowed shapes",
    "anyOf": "matches none of the allowed shapes",
    "allOf": "does not match every required shape",
    "not": "matches a shape the schema forbids",
    "minLength": "is shorter than the minimum length",
    "maxLength": "is longer than the maximum length",
    "minimum": "is below the minimum",
    "maximum": "is above the maximum",
    "exclusiveMinimum": "is not above",
    "exclusiveMaximum": "is not below",
    "minItems": "has fewer items than the minimum",
    "maxItems": "has more items than the maximum",
    "uniqueItems": "has repeated items",
    "minProperties": "has fewer properties than the minimum",
    "multipleOf": "is not a multiple of",
    "$ref": "does not resolve",
}
_BOUNDED = frozenset(
    {"minLength", "maxLength", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "minItems",
     "maxItems", "minProperties", "multipleOf"}
)  # fmt: skip


def format_checker() -> jsonschema.FormatChecker:
    """A format checker for the three formats the contracts use."""
    checker = jsonschema.FormatChecker(formats=())
    checker.checks("date")(_is_date)
    checker.checks("date-time")(_is_date_time)
    checker.checks("uuid")(_is_uuid)
    return checker


class ContractSchemas:
    """Validates instances against the components and responses of one OpenAPI document."""

    def __init__(self, spec: Mapping[str, Any], name: str) -> None:
        self._spec = spec
        self.name = name
        self._components = spec.get("components", {})
        self._checker = format_checker()

    def _errors(self, schema: Mapping[str, Any], instance: object) -> list[str]:
        wrapper = {"$schema": "https://json-schema.org/draft/2020-12/schema", **schema}
        wrapper["components"] = self._components
        validator = jsonschema.Draft202012Validator(wrapper, format_checker=self._checker)
        problems = []
        for error in sorted(validator.iter_errors(instance), key=lambda e: list(e.absolute_path)):
            problems.append(plain_problem(error))
        return problems

    def has_component(self, schema_name: str) -> bool:
        """Whether `components.schemas` defines this name."""
        return schema_name in self._components.get("schemas", {})

    def component_errors(self, schema_name: str, instance: object) -> list[str]:
        """Problems of an instance against `#/components/schemas/<schema_name>`."""
        return self._errors({"$ref": f"#/components/schemas/{schema_name}"}, instance)

    def response_errors(self, path: str, method: str, status: int, instance: object) -> list[str]:
        """Problems of a JSON response body against the operation's declared response."""
        operation = self._spec["paths"][path][method.lower()]
        responses = operation["responses"]
        response = responses.get(str(status))
        if response is None:
            return [f"status {status} is not declared for {method.upper()} {path}"]
        if "$ref" in response:
            response = self._resolve(response["$ref"])
        content = response.get("content", {}).get("application/json")
        if content is None:
            return (
                [] if instance is None else [f"status {status} of {method.upper()} {path} has no JSON body"]
            )
        return self._errors(content["schema"], instance)

    def _resolve(self, reference: str) -> Mapping[str, Any]:
        node: Any = self._spec
        for part in reference.removeprefix("#/").split("/"):
            node = node[part]
        resolved: Mapping[str, Any] = node
        return resolved
