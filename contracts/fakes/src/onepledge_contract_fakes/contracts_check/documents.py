"""Loading contract documents with bounds: YAML for the OpenAPI files, JSON for examples and vectors.

Part of the contract checker in the TEST DOUBLES package `onepledge_contract_fakes`. Every file
is hostile input (SPEC_DECISIONS 74): JSON goes through the bounded parser of decision 38
(depth 32, number literals of at most 30 characters, no duplicate key); YAML through the safe
loader with the same depth bound, at most 5 MiB and no alias. A failure is one problem of that
file, named: "FILE: not valid JSON", "FILE: nests too deeply" and the like. A path read from
inside a file must stay inside the directory it names a file of.

Every path opened, named in a file or fixed by convention, must be a regular file (or a real
directory on the way to one) inside the contracts directory with no symbolic link anywhere on
the way (SPEC_DECISIONS 110). Each step is examined with `lstat` before anything is opened, and
the file is opened without following a link and without blocking, so a FIFO, a device or a
socket is refused by name and never read: "FILE is not a regular file", "FILE is a link out of
the directory", "FILE is a directory where a file is expected".
"""

from __future__ import annotations

import os
import stat
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


def _refused(shown: str, problem: str) -> DocumentError:
    return DocumentError(shown, problem, f"{shown} {problem}")


def _link(root: Path, step: Path, shown: str) -> DocumentError:
    try:
        inside = Path(os.path.realpath(step)).is_relative_to(os.path.realpath(root))
    except (OSError, ValueError):
        inside = False
    return _refused(shown, "is not a regular file" if inside else "is a link out of the directory")


def examine(root: Path, path: Path, directory: bool = False) -> os.stat_result | None:
    """The status of the regular file (or, with `directory`, the real directory) `path` below
    `root`, or None when it does not exist.

    Every step from `root` down is examined without following links: a link anywhere on the
    way, a step that is not a real directory, a directory where a file is expected, or
    anything else that is not a regular file raises DocumentError naming that step.
    """
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        raise _refused(path.name, "names a path outside the directory") from None
    step = root
    status: os.stat_result | None = None
    for index, part in enumerate(parts):
        step = step / part
        shown = PurePosixPath(*parts[: index + 1]).as_posix()
        try:
            status = os.lstat(step)
        except (FileNotFoundError, NotADirectoryError):
            return None
        except OSError:
            raise DocumentError(shown, "cannot be read") from None
        last = index == len(parts) - 1
        if stat.S_ISLNK(status.st_mode):
            raise _link(root, step, shown)
        if (not last or directory) and not stat.S_ISDIR(status.st_mode):
            raise _refused(shown, "is not a directory")
        if last and not directory and stat.S_ISDIR(status.st_mode):
            raise _refused(shown, "is a directory where a file is expected")
        if last and not directory and not stat.S_ISREG(status.st_mode):
            raise _refused(shown, "is not a regular file")
    return status


_OPEN_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC


def read_regular(root: Path, path: Path, name: str, limit: int = MAX_DOCUMENT_BYTES) -> bytes:
    """The bytes of the regular file `path` below `root`, at most `limit` (SPEC_DECISIONS 110)."""
    if examine(root, path) is None:
        raise DocumentError(name, "is missing", f"{name} is missing")
    try:
        descriptor = os.open(path, _OPEN_FLAGS)
    except OSError:
        raise DocumentError(name, "cannot be read") from None
    with os.fdopen(descriptor, "rb") as handle:
        # The file may have been replaced between the examination and the open.
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise _refused(name, "is not a regular file")
        try:
            data = handle.read(limit + 1)
        except OSError:
            raise DocumentError(name, "cannot be read") from None
    if len(data) > limit:
        raise DocumentError(name, f"larger than {limit // (1024 * 1024)} MiB")
    return data


def _text(root: Path, path: Path, name: str) -> str:
    data = read_regular(root, path, name)
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        raise DocumentError(name, "not UTF-8 text") from None


def load_yaml_mapping(root: Path, path: Path, name: str) -> Mapping[str, Any]:
    """Parse a YAML file below `root` whose top level is a mapping."""
    shown = name
    text = _text(root, path, shown)
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


def load_json(root: Path, path: Path, name: str) -> object:
    """Parse a JSON file below `root` with the bounded parser."""
    shown = name
    text = _text(root, path, shown)
    try:
        return parse_bounded_json(text)
    except NestingTooDeepError:
        raise DocumentError(shown, "nests too deeply") from None
    except MalformedJsonError:
        raise DocumentError(shown, "not valid JSON") from None


def confined(directory: Path, relative: object, named_in: str) -> Path:
    """The file `relative` names inside `directory`, or DocumentError naming the file it came from.

    The path must be relative and must not contain `..`; the links and file kinds on the way
    are examined when the file is opened (`examine`).
    """
    sentence = f"{named_in} names a path outside the directory."
    outside = DocumentError(named_in, "names a path outside the directory", sentence)
    if not isinstance(relative, str) or not relative or "\\" in relative or "\0" in relative:
        raise outside
    parts = PurePosixPath(relative)
    if parts.is_absolute() or ".." in parts.parts:
        raise outside
    return directory / relative
