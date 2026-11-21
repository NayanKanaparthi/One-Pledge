"""JSON Schema 2020-12 validation against the schemas of an OpenAPI 3.1 contract.

Used by the checker for the examples and by contract tests for live responses.
Formats `date`, `date-time` and `uuid` are checked by this module itself, so the
result does not depend on optional format packages being installed.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from datetime import date, datetime
from typing import Any

import jsonschema  # type: ignore[import-untyped]  # jsonschema ships no type information

_DATE_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


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
    if not isinstance(value, str):
        return True
    if not _UUID.fullmatch(value):
        return False
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True


def format_checker() -> object:
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
            location = "/".join(str(part) for part in error.absolute_path) or "(root)"
            problems.append(f"{location}: {error.message}")
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
