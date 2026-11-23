"""Reading and writing a data directory safely.

Files are written into a fresh sibling directory first and moved into place, so
a failed run never leaves half a data set. `--force` replaces only a directory
that already holds a data set (it has a manifest or checksums file), never an
arbitrary folder.
"""

from __future__ import annotations

import errno
import os
import shutil
import stat
import tempfile
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from types import MappingProxyType

from onepledge_datagen.application.model import CHECKSUMS, MANIFEST, DataSetFiles
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

    def read(self, source: Path) -> DataSetFiles:
        """Every regular file and every directory below `source`, keyed by relative POSIX path.

        Nothing is followed (SPEC_DECISIONS 110): each entry is examined with `lstat` before it
        is opened, a link, FIFO, device or socket is refused by name, and a file is opened
        without following a link and without blocking.
        """
        if not source.is_dir():
            raise DataSetUnreadableError(f"{source} is not a directory.")
        files: dict[str, bytes] = {}
        directories: set[str] = set()
        try:
            root = Path(os.path.realpath(source))
            self._walk(source, root, PurePosixPath(), files, directories)
        except OSError as error:
            raise DataSetUnreadableError(
                f"Cannot read the data set in {source}: {describe(error)}."
            ) from None
        return DataSetFiles(files, frozenset(directories))

    def _walk(
        self, source: Path, root: Path, prefix: PurePosixPath, files: dict[str, bytes], directories: set[str]
    ) -> None:
        for entry in sorted(os.scandir(source / prefix), key=lambda item: item.name):
            relative = (prefix / entry.name).as_posix()
            mode = entry.stat(follow_symlinks=False).st_mode
            if stat.S_ISLNK(mode):
                raise _link(root, Path(entry.path), relative)
            if stat.S_ISDIR(mode):
                directories.add(relative)
                self._walk(source, root, prefix / entry.name, files, directories)
            elif stat.S_ISREG(mode):
                files[relative] = _read_regular(Path(entry.path), relative)
            else:
                raise DataSetUnreadableError(f"{relative} is not a regular file.")


# JSON files are bounded at 5 MiB, every other file (the CSVs) at 256 MiB (SPEC_DECISIONS 110).
MAX_JSON_BYTES = 5 * 1024 * 1024
MAX_FILE_BYTES = 256 * 1024 * 1024
_OPEN_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC


def _link(root: Path, path: Path, relative: str) -> DataSetUnreadableError:
    inside = Path(os.path.realpath(path)).is_relative_to(root)
    problem = "is not a regular file" if inside else "is a link out of the directory"
    return DataSetUnreadableError(f"{relative} {problem}.")


def _read_regular(path: Path, relative: str) -> bytes:
    limit = MAX_JSON_BYTES if relative.endswith(".json") else MAX_FILE_BYTES
    with os.fdopen(os.open(path, _OPEN_FLAGS), "rb") as handle:
        # The entry may have been replaced between the examination and the open.
        status = os.fstat(handle.fileno())
        if not stat.S_ISREG(status.st_mode):
            raise DataSetUnreadableError(f"{relative} is not a regular file.")
        data = b"" if status.st_size > limit else handle.read(limit + 1)
    if status.st_size > limit or len(data) > limit:
        raise DataSetUnreadableError(f"{relative} is larger than {limit // (1024 * 1024)} MiB.")
    return data
