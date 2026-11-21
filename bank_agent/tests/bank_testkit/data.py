"""Test data: repository paths, datagen data sets, and reference seeding through the product's use case."""

from __future__ import annotations

from pathlib import Path

import pytest

from bank_testkit.database import BankDatabase
from bank_testkit.programs import run_program
from onepledge_bank_agent.application.seed_reference import SeedReferenceData
from onepledge_bank_agent.infrastructure.audit import HashChainedAuditWriter
from onepledge_bank_agent.infrastructure.clock import SystemClock
from onepledge_bank_agent.infrastructure.db.engines import make_engine
from onepledge_bank_agent.infrastructure.db.session import SqlUnitOfWorkFactory
from onepledge_bank_agent.infrastructure.ids import RandomIds
from onepledge_bank_agent.infrastructure.reference_file import ReferenceFileSource

REPOSITORY = Path(__file__).resolve().parents[3]
CONTRACTS = REPOSITORY / "contracts"
EXAMPLE_TAPES = CONTRACTS / "examples" / "tapes"
SMALL_SEED = 4711


def generate(profile: str, seed: int, out: Path) -> Path:
    """A data set written by the installed datagen command (`make setup` installs it)."""
    arguments = ["generate", "--seed", str(seed), "--profile", profile, "--out", str(out)]
    finished = run_program("onepledge-datagen", arguments)
    if finished.returncode != 0:
        pytest.fail(f"onepledge-datagen failed: {finished.stderr.strip()}")
    return out


def seed_reference(database: BankDatabase, data_dir: Path, bank_code: str = "HARBOR") -> None:
    """Lenders and facilities of one bank, through the product's seeding use case as the owner role."""
    engine = make_engine(database.owner_url)
    try:
        units = SqlUnitOfWorkFactory(engine, HashChainedAuditWriter(SystemClock(), RandomIds()), RandomIds())
        SeedReferenceData(units, ReferenceFileSource()).run(data_dir, bank_code)
    finally:
        engine.dispose()
