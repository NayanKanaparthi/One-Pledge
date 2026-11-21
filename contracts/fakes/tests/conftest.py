"""Shared fixtures of the contract test double tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from onepledge_contract_fakes.contracts_check.documents import load_yaml_mapping
from onepledge_contract_fakes.contracts_check.schemas import ContractSchemas

CONTRACTS_DIR = Path(__file__).resolve().parents[1].parent


@pytest.fixture(scope="session")
def contracts_dir() -> Path:
    """The repository's contracts/ directory."""
    return CONTRACTS_DIR


@pytest.fixture(scope="session")
def key_holder_schemas() -> ContractSchemas:
    """Schemas of contracts/key_holder.yaml."""
    return ContractSchemas(load_yaml_mapping(CONTRACTS_DIR / "key_holder.yaml"), "key_holder.yaml")


@pytest.fixture(scope="session")
def registry_schemas() -> ContractSchemas:
    """Schemas of contracts/registry.yaml."""
    return ContractSchemas(load_yaml_mapping(CONTRACTS_DIR / "registry.yaml"), "registry.yaml")
