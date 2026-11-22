"""Reading and writing a data directory safely.

Files are written into a fresh sibling directory first and moved into place, so
a failed run never leaves half a data set. `--force` replaces only a directory
that already holds a data set (it has a manifest or checksums file), never an
arbitrary folder.
"""

from __future__ import annotations

import errno
import shutil
import tempfile
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType

from onepledge_datagen.application.model import CHECKSUMS, MANIFEST
from onepledge_datagen.domain.errors import DataSetUnreadableError, OutputDirectoryError

# What an OSError means, in words an operator can act on (SPEC_DECISIONS 23 d).
_PROBLEMS: Mapping[int, str] = MappingProxyType(
    {
        errno.EEXIST: "a part of the path is a file",
        errno.ENOTDIR: "a part of the path is a file",
        errno.EACCES: "permission denied",
        errno.EPERM: "permission denied",
        errno.EROFS: "the file system is read-only",
        errno.ENOSPC: "no space is left on the device",
        errno.EDQUOT: "the disk quota is exhausted",
        errno.ENOENT: "a parent directory cannot be created",
        errno.ENAMETOOLONG: "the path is too long",
    }
)


def describe(error: OSError) -> str:
    """The problem an OSError reports, as a few plain words."""
    if error.errno is not None and error.errno in _PROBLEMS:
        return _PROBLEMS[error.errno]
    return error.strerror.lower() if error.strerror else "the operating system refused it"


class LocalDataDirectory:
    """A data directory on the local file system."""

    def write(self, files: Mapping[str, bytes], target: Path, force: bool) -> None:
        """Write the files under `target`; any file system refusal is one sentence naming the path."""
        try:
            self._write(files, target, force)
        except OSError as error:
            raise OutputDirectoryError(
                f"Cannot write the data set to {target}: {describe(error)}; choose another --out."
            ) from None

    @staticmethod
    def _write(files: Mapping[str, bytes], target: Path, force: bool) -> None:
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
        try:
            root = source.resolve()
            files: dict[str, bytes] = {}
            for path in sorted(source.rglob("*")):
                if not path.is_file():
                    continue
                relative = path.relative_to(source).as_posix()
                if not path.resolve().is_relative_to(root):
                    # A symbolic link out of the data set is never followed (SPEC_DECISIONS 74).
                    raise DataSetUnreadableError(f"{relative} links to a path outside the directory.")
                files[relative] = path.read_bytes()
            return files
        except OSError as error:
            raise DataSetUnreadableError(
                f"Cannot read the data set in {source}: {describe(error)}."
            ) from None
