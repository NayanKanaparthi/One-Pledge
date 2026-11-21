"""Loading contract documents: YAML for the OpenAPI files, JSON for examples and vectors."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]  # PyYAML ships no type information; only safe_load is used


class DocumentError(Exception):
    """A contract document cannot be read or parsed. Carries the file path."""

    def __init__(self, path: Path, problem: str) -> None:
        super().__init__(problem)
        self.path = path
        self.problem = problem


def load_yaml_mapping(path: Path) -> Mapping[str, Any]:
    """Parse a YAML file whose top level is a mapping."""
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise DocumentError(path, f"cannot be parsed as YAML ({type(error).__name__})") from None
    if not isinstance(document, dict):
        raise DocumentError(path, "does not hold a YAML mapping")
    return document


def load_json(path: Path) -> object:
    """Parse a JSON file."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as error:
        raise DocumentError(path, f"cannot be parsed as JSON ({type(error).__name__})") from None
