"""Reading and writing a data directory safely.

Files are written into a fresh sibling directory first and moved into place, so
a failed run never leaves half a data set. `--force` replaces only a directory
that already holds a data set (it has a manifest or checksums file), never an
arbitrary folder.
"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Mapping
from pathlib import Path

from onepledge_datagen.application.model import CHECKSUMS, MANIFEST
from onepledge_datagen.domain.errors import DataSetUnreadableError, OutputDirectoryError


class LocalDataDirectory:
    """A data directory on the local file system."""

    def write(self, files: Mapping[str, bytes], target: Path, force: bool) -> None:
        """Write the files under `target`."""
        if target.exists() and not target.is_dir():
            raise OutputDirectoryError(f"{target} exists and is not a directory; choose another --out.")
        occupied = target.is_dir() and any(target.iterdir())
        if occupied and not force:
            raise OutputDirectoryError(f"{target} is not empty; choose an empty directory or pass --force.")
        if occupied and not ((target / MANIFEST).is_file() or (target / CHECKSUMS).is_file()):
            raise OutputDirectoryError(f"{target} does not hold a data set, so --force will not replace it.")
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=f".{target.name}.", dir=target.parent))
        try:
            for relative, data in files.items():
                path = staging / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            if target.exists():
                shutil.rmtree(target)
            staging.rename(target)
        finally:
            if staging.exists():
                shutil.rmtree(staging)

    def read(self, source: Path) -> dict[str, bytes]:
        """Every regular file below `source`, keyed by its relative POSIX path."""
        if not source.is_dir():
            raise DataSetUnreadableError(f"{source} is not a directory.")
        return {
            path.relative_to(source).as_posix(): path.read_bytes()
            for path in sorted(source.rglob("*"))
            if path.is_file()
        }
