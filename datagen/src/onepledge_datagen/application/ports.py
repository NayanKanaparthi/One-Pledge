"""Interfaces the use cases depend on; implemented in `infrastructure`."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Protocol

from onepledge_datagen.application.model import DataSetFiles, DataSetModel
from onepledge_datagen.application.view import DataSetView


class DataSetCodec(Protocol):
    """Turns a data set model into file bytes and file bytes into a typed view."""

    def render(self, model: DataSetModel) -> dict[str, bytes]:
        """Every output file except the contract example and the checksums."""
        ...

    def render_json(self, document: object) -> bytes:
        """A JSON document in the data set's JSON style."""
        ...

    def parse(self, files: Mapping[str, bytes], directories: frozenset[str] = frozenset()) -> DataSetView:
        """The typed view of a set of files, or DataSetUnreadableError.

        `directories` are the data set's directories; a file the view needs that is one of them
        is named as a directory where a file is expected.
        """
        ...


class DataSetDirectory(Protocol):
    """Reads and writes a data directory."""

    def write(self, files: Mapping[str, bytes], target: Path, force: bool) -> None:
        """Write every file below `target`, refusing a non-empty directory unless forced."""
        ...

    def read(self, source: Path) -> DataSetFiles:
        """Every regular file and directory below `source`, keyed by relative path."""
        ...
