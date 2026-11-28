"""Shared fixture of the ledger verifier tests: the unit isolation rule of the repository's plugin."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _unit_isolation(enforce_unit_isolation: None) -> None:
    """Every test here marked `unit` has no network, database, child process or file write."""
    del enforce_unit_isolation
