"""Shared vocabulary of the planters: expectations per SPEC 5.7 and the planter interface."""

from __future__ import annotations

from typing import Protocol

from onepledge_datagen.application.kit import PlantingKit
from onepledge_datagen.application.workspace import Expectation

ALL_KINDS: tuple[str, ...] = (
    "ASSET",
    "VIN",
    "BORROWER",
    "AMOUNT_EXACT",
    "AMOUNT_100",
    "AMOUNT_1000",
    "DATE_DAY",
    "DATE_MONTH",
)
FRAUD, BLIND_SPOT, HONEST_LOOKALIKE = "FRAUD", "BLIND_SPOT", "HONEST_LOOKALIKE"
HOLDER, CLAIMANT = "HOLDER", "CLAIMANT"
EXACT, LIKELY = "EXACT", "LIKELY"


class CasePlanter(Protocol):
    """Plants every case of one case type into the workspace."""

    case_type: str

    def plant(self, kit: PlantingKit) -> None:
        """Add the case loans, rows, record plans and the case entries."""
        ...


def locked(alert: tuple[str, str] | None = None) -> Expectation:
    """Accepted, locked, with or without one alert."""
    return Expectation("LOCKED", alert=alert)


def conflict(kind: str, basis: str, include: tuple[str, ...], exclude: tuple[str, ...] = ()) -> Expectation:
    """Accepted, registry conflict, claimant alert of the same match kind."""
    return Expectation(
        "CONFLICT",
        match_kind=kind,
        match_basis=basis,
        kinds_matched_include=include,
        kinds_matched_exclude=exclude,
        alert=(CLAIMANT, kind),
    )


def held(**failed: str) -> Expectation:
    """Accepted, held by local checks with exactly these failed checks and reasons."""
    reasons = tuple(sorted(failed.items()))
    return Expectation(
        "HELD_LOCAL", failed_checks=tuple(name for name, _ in reasons), failed_check_reasons=reasons
    )


def rejected(*problems: str) -> Expectation:
    """Rejected in cleaning with at least these problems."""
    return Expectation("REJECTED", disposition="REJECTED", cleaning_problems=problems)


RELEASED = Expectation("RELEASED")
LOCKED_OR_CONFLICT = Expectation("LOCKED_OR_CONFLICT")
