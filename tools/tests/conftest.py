"""Fixtures of the test tooling's own tests."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _unit_isolation(enforce_unit_isolation: None) -> None:
    """Every test here marked `unit` is held to SPEC_DECISIONS 77."""
    del enforce_unit_isolation
