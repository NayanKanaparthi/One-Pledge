"""Interfaces the use cases depend on; implemented in `infrastructure`."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Protocol

from onepledge_datagen.application.model import DataSetModel
from onepledge_datagen.application.view import DataSetView


class DataSetCodec(Protocol):
    """Turns a data set model into file bytes and file bytes into a typed view."""

    def render(self, model: DataSetModel) -> dict[str, bytes]:
        """Every output file except the contract example and the checksums."""
        ...

    def render_json(self, document: object) -> bytes:
        """A JSON document in the data set's JSON style."""
        ...

    def parse(self, files: Mapping[str, bytes]) -> DataSetView:
        """The typed view of a set of files, or DataSetUnreadableError."""
        ...


class DataSetDirectory(Protocol):
    """Reads and writes a data directory."""

    def write(self, files: Mapping[str, bytes], target: Path, force: bool) -> None:
        """Write every file below `target`, refusing a non-empty directory unless forced."""
        ...

    def read(self, source: Path) -> dict[str, bytes]:
        """Every file below `source`, keyed by relative path."""
        ...
