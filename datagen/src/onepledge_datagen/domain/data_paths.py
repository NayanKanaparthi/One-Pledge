"""Paths read from inside a data set file must stay inside the data set (SPEC_DECISIONS 74).

A manifest step's `file` and a `checksums.json` entry are relative POSIX paths with no `..`
segment; anything else names a path outside the directory and is refused as such.
"""

from __future__ import annotations

from pathlib import PurePosixPath

from onepledge_datagen.domain.errors import DataSetUnreadableError


def is_inside(path: str) -> bool:
    """Whether a path read from a file is a relative path that cannot leave the data set."""
    if not path or "\\" in path or "\0" in path:
        return False
    pure = PurePosixPath(path)
    return not pure.is_absolute() and ".." not in pure.parts


def outside(named_in: str) -> DataSetUnreadableError:
    """The refusal for a file that names a path outside the directory."""
    return DataSetUnreadableError(f"{named_in} names a path outside the directory.")
