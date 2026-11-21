"""Shared fixtures of the bank agent tests.

Databases and roles are created through the bank agent's own provisioning code with a
unique name, migrated with its own migration runner, and dropped afterwards. Data sets
come from the `onepledge-datagen` command (the bank agent never imports datagen).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from bank_testkit.data import CONTRACTS, EXAMPLE_TAPES, SMALL_SEED, generate
from bank_testkit.database import BankDatabase, drop, new_bank_database, provision_and_migrate
from bank_testkit.outside import InProcessOutsideRecords, copy_outside_records


@pytest.fixture(scope="session")
def contracts_dir() -> Path:
    """`contracts/`."""
    return CONTRACTS


@pytest.fixture(scope="session")
def example_data(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The `example` profile (seed 20261117), with the committed example outside records."""
    data = generate("example", 20261117, tmp_path_factory.mktemp("example") / "data")
    return copy_outside_records(EXAMPLE_TAPES / "example_outside_records", data)


@pytest.fixture(scope="session")
def small_data(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The `small` profile with a fixed seed."""
    return generate("small", SMALL_SEED, tmp_path_factory.mktemp("small") / "data")


@pytest.fixture
def bank_db() -> Iterator[BankDatabase]:
    """A provisioned and migrated bank database, dropped after the test."""
    database = new_bank_database()
    provision_and_migrate(database)
    yield database
    drop(database)


@pytest.fixture
def new_database() -> Iterator[Callable[[], BankDatabase]]:
    """Makes further migrated bank databases inside one test; all are dropped afterwards."""
    made: list[BankDatabase] = []

    def build() -> BankDatabase:
        database = new_bank_database()
        provision_and_migrate(database)
        made.append(database)
        return database

    yield build
    for database in made:
        drop(database)


@pytest.fixture(scope="session")
def example_outside(example_data: Path) -> Iterator[InProcessOutsideRecords]:
    """Outside records seeded with the committed example records."""
    service = InProcessOutsideRecords(example_data)
    yield service
    service.close()


@pytest.fixture(scope="session")
def small_outside(small_data: Path) -> Iterator[InProcessOutsideRecords]:
    """Outside records seeded with the small data set."""
    service = InProcessOutsideRecords(small_data)
    yield service
    service.close()
