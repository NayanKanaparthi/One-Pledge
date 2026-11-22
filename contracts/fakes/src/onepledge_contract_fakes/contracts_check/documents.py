"""Loading contract documents: YAML for the OpenAPI files, JSON for examples and vectors.

Part of the contract checker in the TEST DOUBLES package `onepledge_contract_fakes`.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml


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
    except OSError:
        raise DocumentError(path, "cannot be read") from None
    except UnicodeDecodeError:
        raise DocumentError(path, "is not UTF-8 text") from None
    except yaml.YAMLError:
        raise DocumentError(path, "is not valid YAML") from None
    if not isinstance(document, dict):
        raise DocumentError(path, "does not hold a YAML mapping")
    return document


def load_json(path: Path) -> object:
    """Parse a JSON file."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError:
        raise DocumentError(path, "cannot be read") from None
    except UnicodeDecodeError:
        raise DocumentError(path, "is not UTF-8 text") from None
    except json.JSONDecodeError as error:
        raise DocumentError(path, f"is not valid JSON at line {error.lineno}, column {error.colno}") from None
