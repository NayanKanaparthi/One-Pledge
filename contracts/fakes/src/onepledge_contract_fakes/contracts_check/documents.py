"""Loading contract documents with bounds: YAML for the OpenAPI files, JSON for examples and vectors.

Part of the contract checker in the TEST DOUBLES package `onepledge_contract_fakes`. Every file
is hostile input (SPEC_DECISIONS 74): JSON goes through the bounded parser of decision 38
(depth 32, number literals of at most 30 characters, no duplicate key); YAML through the safe
loader with the same depth bound, at most 5 MiB and no alias. A failure is one problem of that
file, named: "FILE: not valid JSON", "FILE: nests too deeply" and the like. A path read from
inside a file must stay inside the directory it names a file of.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from onepledge_contract_fakes.json_body import (
    MAX_DEPTH,
    MalformedJsonError,
    NestingTooDeepError,
    parse_bounded_json,
)

MAX_DOCUMENT_BYTES = 5 * 1024 * 1024


class DocumentError(Exception):
    """A contract document cannot be read or parsed; `message` names the file and the problem."""

    def __init__(self, name: str, problem: str, sentence: str | None = None) -> None:
        super().__init__(problem)
        self.name = name
        self.problem = problem
        self.message = sentence or f"{name}: {problem}"


class _TooDeepError(yaml.YAMLError):
    """The YAML document nests deeper than MAX_DEPTH."""


class _AliasError(yaml.YAMLError):
    """The YAML document uses an alias."""


class _BoundedLoader(yaml.SafeLoader):
    """The safe loader, refusing aliases and nesting deeper than MAX_DEPTH while composing."""

    def __init__(self, stream: str) -> None:
        super().__init__(stream)
        self._depth = 0

    def compose_node(self, parent: yaml.Node | None, index: int) -> yaml.Node | None:
        """Compose one node, counting how deep it sits."""
        if self.check_event(yaml.AliasEvent):
            raise _AliasError
        self._depth += 1
        try:
            if self._depth > MAX_DEPTH:
                raise _TooDeepError
            return super().compose_node(parent, index)
        finally:
            self._depth -= 1


def _text(path: Path, name: str) -> str:
    try:
        with path.open("rb") as handle:
            data = handle.read(MAX_DOCUMENT_BYTES + 1)
    except OSError:
        raise DocumentError(name, "cannot be read") from None
    if len(data) > MAX_DOCUMENT_BYTES:
        raise DocumentError(name, "larger than 5 MiB")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        raise DocumentError(name, "not UTF-8 text") from None


def load_yaml_mapping(path: Path, name: str | None = None) -> Mapping[str, Any]:
    """Parse a YAML file whose top level is a mapping."""
    shown = name or path.name
    text = _text(path, shown)
    loader = _BoundedLoader(text)
    try:
        document = loader.get_single_data()
    except (_TooDeepError, RecursionError):
        raise DocumentError(shown, "nests too deeply") from None
    except _AliasError:
        raise DocumentError(shown, "uses a YAML alias") from None
    except yaml.YAMLError:
        raise DocumentError(shown, "not valid YAML") from None
    finally:
        loader.dispose()
    if not isinstance(document, dict):
        raise DocumentError(shown, "not a YAML mapping")
    return document


def load_json(path: Path, name: str | None = None) -> object:
    """Parse a JSON file with the bounded parser."""
    shown = name or path.name
    text = _text(path, shown)
    try:
        return parse_bounded_json(text)
    except NestingTooDeepError:
        raise DocumentError(shown, "nests too deeply") from None
    except MalformedJsonError:
        raise DocumentError(shown, "not valid JSON") from None


def confined(directory: Path, relative: object, named_in: str) -> Path:
    """The file `relative` names inside `directory`, or DocumentError naming the file it came from.

    The path must be relative, must not contain `..`, and must resolve inside the directory
    without a symbolic link leading out of it.
    """
    sentence = f"{named_in} names a path outside the directory."
    outside = DocumentError(named_in, "names a path outside the directory", sentence)
    if not isinstance(relative, str) or not relative or "\\" in relative or "\0" in relative:
        raise outside
    parts = PurePosixPath(relative)
    if parts.is_absolute() or ".." in parts.parts:
        raise outside
    candidate = directory / relative
    try:
        inside = candidate.resolve().is_relative_to(directory.resolve())
    except (OSError, RuntimeError):
        inside = False
    if not inside:
        raise outside
    return candidate
