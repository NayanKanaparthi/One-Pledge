"""Lender name normalisation (SPEC 11.1)."""

from __future__ import annotations

import pytest

from onepledge_bank_agent.domain.lender_names import lender_names_match, normalise_lender_name

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("name", "normalised"),
    [
        ("Summit Auto Finance LLC", "SUMMIT AUTO FINANCE"),
        ("SUMMIT AUTO FINANCE, L.L.C.", "SUMMIT AUTO FINANCE"),
        ("Summit Auto Finance, LLC", "SUMMIT AUTO FINANCE"),
        ("Summit Auto Finance LLC ", "SUMMIT AUTO FINANCE"),
        ("Lakeside Motor Credit, INC.", "LAKESIDE MOTOR CREDIT"),
        ("Lakeside Motor Credit Incorporated", "LAKESIDE MOTOR CREDIT"),
        ("Crestline Auto Credit Corp", "CRESTLINE AUTO CREDIT"),
        ("Smith & Sons Co. Ltd", "SMITH AND SONS"),
        ("Acme L.P.", "ACME"),
        ("Acme Company Limited LP", "ACME"),
        ("  multiple   spaces  inc ", "MULTIPLE SPACES"),
        ("LLC", ""),
        (None, ""),
    ],
)
def test_normalisation(name: str | None, normalised: str) -> None:
    assert normalise_lender_name(name) == normalised


def test_names_match_when_equal_and_not_empty() -> None:
    assert lender_names_match("SUMMIT AUTO FINANCE, L.L.C.", "Summit Auto Finance LLC")
    assert not lender_names_match("Crestline Auto Credit Corp", "Summit Auto Finance LLC")
    assert not lender_names_match("LLC", "Inc")
    assert not lender_names_match(None, None)
