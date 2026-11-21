"""Shared fixtures of the generator tests: generated data sets, built once per session."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from onepledge_datagen.application.generate import GenerateDataSet
from onepledge_datagen.application.invariants import InvariantChecker
from onepledge_datagen.domain.entities import Profile
from onepledge_datagen.infrastructure.codec import FileCodec
from onepledge_datagen.infrastructure.directory import LocalDataDirectory

REPOSITORY = Path(__file__).resolve().parents[2]
DEFAULT_SEED = 20261117


def generate(target: Path, profile: Profile, seed: int = DEFAULT_SEED) -> Path:
    """Generate one data set into `target`."""
    GenerateDataSet(FileCodec(), LocalDataDirectory(), InvariantChecker()).run(
        seed, profile, target, force=False
    )
    return target


@pytest.fixture(scope="session")
def generator() -> Callable[[Path, Profile, int], Path]:
    """Generates a data set: (target, profile, seed) to the target directory."""
    return generate


@pytest.fixture(scope="session")
def repository() -> Path:
    """The repository root."""
    return REPOSITORY


@pytest.fixture(scope="session")
def small_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The small profile with the default seed."""
    return generate(tmp_path_factory.mktemp("small") / "data", Profile.SMALL)


@pytest.fixture(scope="session")
def full_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The full profile with the default seed."""
    return generate(tmp_path_factory.mktemp("full") / "data", Profile.FULL)


@pytest.fixture(scope="session")
def example_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The example profile with the default seed."""
    return generate(tmp_path_factory.mktemp("example") / "data", Profile.EXAMPLE)
