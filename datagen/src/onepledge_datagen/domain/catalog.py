"""Fixed reference data of the synthetic world (SPEC 5.4 and 5.6)."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from types import MappingProxyType

from onepledge_datagen.domain.entities import Bank, Facility, Lender, StepDefinition, TapeKind

TIMELINE_END = date(2026, 11, 16)
ORIGINATION_FLOOR = date(2024, 1, 2)
FACILITY_OPENED_ON = date(2025, 3, 1)
CREDIT_LIMIT_CENTS = 7_500_000_000
OTHER_LIENHOLDER = "Crestline Auto Credit Corp"
RESERVED_VINS = frozenset({"1HGCM82633A004352"})

BANKS: tuple[Bank, ...] = (
    Bank("HARBOR", "Harbor Bank"),
    Bank("NORTHGATE", "Northgate Bank"),
    Bank("CALDER", "Calder Credit Fund"),
)

SUMMIT = Lender(
    code="SUMMIT",
    legal_name="Summit Auto Finance LLC",
    lei="00000ONEPLEDGESUM134",
    title_spellings=(
        "Summit Auto Finance LLC",
        "SUMMIT AUTO FINANCE, L.L.C.",
        "Summit Auto Finance, LLC",
        "Summit Auto Finance LLC ",
    ),
    contract_spellings=("Summit Auto Finance LLC", "SUMMIT AUTO FINANCE LLC"),
    borrower_prefix="C",
    borrower_digits=7,
)
LAKESIDE = Lender(
    code="LAKESIDE",
    legal_name="Lakeside Motor Credit Inc",
    lei="00000ONEPLEDGELAK198",
    title_spellings=(
        "Lakeside Motor Credit Inc",
        "LAKESIDE MOTOR CREDIT, INC.",
        "Lakeside Motor Credit, Inc",
        "Lakeside Motor Credit Inc ",
    ),
    contract_spellings=("Lakeside Motor Credit Inc", "LAKESIDE MOTOR CREDIT INC"),
    borrower_prefix="LK-",
    borrower_digits=6,
)
LENDERS: tuple[Lender, ...] = (SUMMIT, LAKESIDE)

FACILITIES: tuple[Facility, ...] = tuple(
    Facility(
        code=f"{bank}-{lender.code}",
        bank_code=bank,
        lender_code=lender.code,
        name=f"{lender.legal_name} warehouse line",
        credit_limit_cents=CREDIT_LIMIT_CENTS,
        advance_rate_bps=8500 if lender is SUMMIT else 9000,
        opened_on=FACILITY_OPENED_ON,
    )
    for bank, lender in (
        ("HARBOR", SUMMIT),
        ("HARBOR", LAKESIDE),
        ("NORTHGATE", SUMMIT),
        ("NORTHGATE", LAKESIDE),
        ("CALDER", SUMMIT),
    )
)

_BB, _PL, _RE = TapeKind.BACK_BOOK, TapeKind.PLEDGE, TapeKind.RELEASE
STEPS: tuple[StepDefinition, ...] = (
    StepDefinition("S01", 1, "HARBOR", "HARBOR-SUMMIT", _BB, date(2026, 9, 30), 1050, 88),
    StepDefinition("S02", 1, "HARBOR", "HARBOR-LAKESIDE", _BB, date(2026, 9, 30), 780, 65),
    StepDefinition("S03", 1, "NORTHGATE", "NORTHGATE-SUMMIT", _BB, date(2026, 9, 30), 780, 65),
    StepDefinition("S04", 1, "NORTHGATE", "NORTHGATE-LAKESIDE", _BB, date(2026, 9, 30), 780, 65),
    StepDefinition("S05", 1, "CALDER", "CALDER-SUMMIT", _BB, date(2026, 9, 30), 560, 47),
    StepDefinition("S06", 2, "HARBOR", "HARBOR-LAKESIDE", _PL, date(2026, 10, 15), 200, 17),
    StepDefinition("S07", 2, "HARBOR", "HARBOR-SUMMIT", _PL, date(2026, 10, 15), 150, 13),
    StepDefinition("S08", 3, "NORTHGATE", "NORTHGATE-SUMMIT", _PL, date(2026, 10, 22), 110, 10),
    StepDefinition("S09", 4, "CALDER", "CALDER-SUMMIT", _PL, date(2026, 10, 29), 90, 8),
    StepDefinition("S10", 5, "HARBOR", "HARBOR-SUMMIT", _PL, date(2026, 11, 5), 90, 8),
    StepDefinition("S11", 7, "HARBOR", "HARBOR-SUMMIT", _RE, date(2026, 11, 12), 0, 0),
    StepDefinition("S12", 7, "NORTHGATE", "NORTHGATE-SUMMIT", _PL, date(2026, 11, 13), 40, 4),
    StepDefinition("S13", 7, "HARBOR", "HARBOR-SUMMIT", _PL, date(2026, 11, 13), 30, 3),
    StepDefinition("S14", 8, "HARBOR", "HARBOR-SUMMIT", _PL, date(2026, 11, 16), 20, 2, "RACE"),
    StepDefinition("S15", 8, "CALDER", "CALDER-SUMMIT", _PL, date(2026, 11, 16), 20, 2, "RACE"),
)
EXAMPLE_STEP = StepDefinition("S01", None, "HARBOR", "HARBOR-SUMMIT", _PL, date(2026, 10, 15), 12, 12)

# World manufacturer identifiers with the make they belong to, and models per make.
MANUFACTURERS: tuple[tuple[str, str], ...] = (
    ("1HG", "Honda"), ("2HG", "Honda"), ("5FN", "Honda"), ("1FT", "Ford"), ("1FA", "Ford"),
    ("3FA", "Ford"), ("2T1", "Toyota"), ("4T1", "Toyota"), ("JTD", "Toyota"), ("3VW", "Volkswagen"),
    ("WVW", "Volkswagen"), ("5YJ", "Tesla"), ("KMH", "Hyundai"), ("5NP", "Hyundai"), ("WBA", "BMW"),
    ("1G1", "Chevrolet"), ("1GC", "Chevrolet"), ("1C4", "Jeep"), ("JN1", "Nissan"), ("1N4", "Nissan"),
    ("KNA", "Kia"), ("WDD", "Mercedes-Benz"), ("2C3", "Chrysler"),
)  # fmt: skip
MODELS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "Honda": ("Civic", "Accord", "CR-V", "Pilot"),
        "Ford": ("F-150", "Escape", "Explorer", "Fusion"),
        "Toyota": ("Camry", "Corolla", "RAV4", "Highlander"),
        "Volkswagen": ("Jetta", "Passat", "Tiguan"),
        "Tesla": ("Model 3", "Model Y"),
        "Hyundai": ("Elantra", "Sonata", "Tucson"),
        "BMW": ("330i", "X3", "X5"),
        "Chevrolet": ("Malibu", "Equinox", "Silverado"),
        "Jeep": ("Cherokee", "Wrangler", "Compass"),
        "Nissan": ("Altima", "Sentra", "Rogue"),
        "Kia": ("Forte", "Sorento", "Sportage"),
        "Mercedes-Benz": ("C300", "GLC 300", "E350"),
        "Chrysler": ("300", "Pacifica"),
    }
)
TITLE_STATES: tuple[str, ...] = ("NJ", "NY", "PA", "CT", "FL", "TX", "GA", "OH")
TITLE_STATE_WEIGHTS: tuple[int, ...] = (18, 22, 16, 8, 14, 12, 6, 4)
TERMS: tuple[int, ...] = (36, 48, 60, 72, 84)
APR_TENTHS: Mapping[str, tuple[int, int]] = MappingProxyType({"SUMMIT": (119, 249), "LAKESIDE": (49, 109)})

# Words that must never appear in a tape, the manifest, the reference data or the outside records.
LABEL_WORDS: tuple[str, ...] = (
    "FRAUD", "COPY", "INVENT", "PLANTED", "LOOKALIKE", "BLIND_SPOT", "RACE_PAIR", "MESSI", "EXACT", "LIKELY",
)  # fmt: skip


def lender(code: str) -> Lender:
    """The lender with this code."""
    return next(item for item in LENDERS if item.code == code)


def facility(code: str) -> Facility:
    """The facility with this code."""
    return next(item for item in FACILITIES if item.code == code)


def step(step_id: str) -> StepDefinition:
    """The full timeline step with this id."""
    return next(item for item in STEPS if item.step_id == step_id)
