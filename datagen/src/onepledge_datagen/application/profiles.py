"""The three profiles of SPEC 5.6 and 5.7: steps, baseline sizes, planters and data quality rows."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from onepledge_datagen.application.messiness import FIXABLE, UNFIXABLE, MessinessPlan
from onepledge_datagen.application.planters.altered import (
    AlteredCheckDigitPlanter,
    AlteredNoTitlePlanter,
    AlteredTitlePendingPlanter,
)
from onepledge_datagen.application.planters.base import CasePlanter
from onepledge_datagen.application.planters.copies import (
    AmountChangedNoContractPlanter,
    AmountChangedPlanter,
    DateChangedPlanter,
    ExactCopyPlanter,
)
from onepledge_datagen.application.planters.invented import BlindSpotPlanter, InventedNoTitlePlanter
from onepledge_datagen.application.planters.local_failures import NoPaymentsPlanter, OtherLienholderPlanter
from onepledge_datagen.application.planters.lookalikes import (
    MovedAfterReleasePlanter,
    RefinancedPlanter,
    TitlePendingHonestPlanter,
    TwoCarsPlanter,
)
from onepledge_datagen.application.planters.race import RacePairPlanter
from onepledge_datagen.domain import catalog
from onepledge_datagen.domain.entities import Profile, StepDefinition, TapeKind

# SPEC 5.7 counts, full and small.
CASE_COUNTS: Mapping[str, tuple[int, int]] = MappingProxyType(
    {
        "EXACT_COPY": (40, 6),
        "COPY_AMOUNT_CHANGED": (25, 4),
        "COPY_AMOUNT_CHANGED_NO_CONTRACT": (10, 2),
        "COPY_DATE_CHANGED": (25, 4),
        "COPY_VIN_ALTERED_CHECK_DIGIT_INVALID": (15, 3),
        "COPY_VIN_ALTERED_NO_TITLE": (15, 3),
        "COPY_VIN_ALTERED_TITLE_PENDING": (10, 2),
        "INVENTED_NO_TITLE": (30, 4),
        "TITLE_OTHER_LIENHOLDER": (20, 3),
        "NO_PAYMENTS_90D": (25, 4),
        "RACE_PAIR": (10, 2),
        "BLIND_SPOT_INVENTED_IN_PENDING_WINDOW": (5, 1),
        "TWO_CARS_ONE_BORROWER": (40, 5),
        "MOVED_AFTER_RELEASE": (30, 4),
        "REFINANCED": (30, 4),
        "TITLE_PENDING_HONEST": (60, 6),
    }
)
EXACT_COPY_INTO_BACK_BOOK = (15, 2)
FIXABLE_PER_KIND = (20, 2)
UNFIXABLE_TOTAL_FULL = 48
UNFIXABLE_EXCLUDED_STEPS = frozenset({"S06", "S14", "S15"})


@dataclass(frozen=True)
class ProfilePlan:
    """Everything that differs between profiles."""

    profile: Profile
    steps: tuple[StepDefinition, ...]
    baseline: dict[str, int]
    planters: tuple[CasePlanter, ...]
    messiness: MessinessPlan
    totals_rows: bool
    writes_contract_example: bool


def _count(case_type: str, full: bool) -> int:
    full_count, small_count = CASE_COUNTS[case_type]
    return full_count if full else small_count


def _timeline_planters(full: bool) -> tuple[CasePlanter, ...]:
    exact = _count("EXACT_COPY", full)
    into_back_book = EXACT_COPY_INTO_BACK_BOOK[0 if full else 1]
    return (
        ExactCopyPlanter(into_back_book, exact - into_back_book),
        AmountChangedPlanter(_count("COPY_AMOUNT_CHANGED", full)),
        AmountChangedNoContractPlanter(_count("COPY_AMOUNT_CHANGED_NO_CONTRACT", full)),
        DateChangedPlanter(_count("COPY_DATE_CHANGED", full)),
        AlteredCheckDigitPlanter(_count("COPY_VIN_ALTERED_CHECK_DIGIT_INVALID", full)),
        AlteredNoTitlePlanter(_count("COPY_VIN_ALTERED_NO_TITLE", full)),
        AlteredTitlePendingPlanter(_count("COPY_VIN_ALTERED_TITLE_PENDING", full)),
        InventedNoTitlePlanter(_count("INVENTED_NO_TITLE", full)),
        OtherLienholderPlanter(_count("TITLE_OTHER_LIENHOLDER", full)),
        NoPaymentsPlanter(_count("NO_PAYMENTS_90D", full)),
        RacePairPlanter(_count("RACE_PAIR", full)),
        BlindSpotPlanter(_count("BLIND_SPOT_INVENTED_IN_PENDING_WINDOW", full)),
        TwoCarsPlanter(_count("TWO_CARS_ONE_BORROWER", full)),
        MovedAfterReleasePlanter(_count("MOVED_AFTER_RELEASE", full)),
        RefinancedPlanter(_count("REFINANCED", full)),
        TitlePendingHonestPlanter(_count("TITLE_PENDING_HONEST", full)),
    )


def _unfixable_counts(full: bool) -> dict[str, int]:
    kinds = list(UNFIXABLE)
    if not full:
        return dict.fromkeys(kinds, 1)
    base, extra = divmod(UNFIXABLE_TOTAL_FULL, len(kinds))
    return {kind: base + (1 if index < extra else 0) for index, kind in enumerate(kinds)}


def _timeline_plan(profile: Profile) -> ProfilePlan:
    full = profile == Profile.FULL
    steps = catalog.STEPS
    summit_steps = tuple(
        s.step_id
        for s in steps
        if s.kind != TapeKind.RELEASE and catalog.facility(s.facility_code).lender_code == "SUMMIT"
    )
    unfixable_steps = tuple(
        s.step_id for s in steps if s.kind != TapeKind.RELEASE and s.step_id not in UNFIXABLE_EXCLUDED_STEPS
    )
    messiness = MessinessPlan(
        fixable=dict.fromkeys(FIXABLE, FIXABLE_PER_KIND[0 if full else 1]),
        unfixable=_unfixable_counts(full),
        fixable_steps=summit_steps,
        unfixable_steps=unfixable_steps,
    )
    baseline = {s.step_id: s.baseline_full if full else s.baseline_small for s in steps}
    return ProfilePlan(profile, steps, baseline, _timeline_planters(full), messiness, True, False)


def _example_plan() -> ProfilePlan:
    step = catalog.EXAMPLE_STEP
    messiness = MessinessPlan(
        fixable={"M_VIN_LOWERCASE": 1, "M_VIN_SEPARATORS": 1, "M_DATE_ALT_FORMAT": 1},
        unfixable={"M_VIN_TYPO": 1, "M_AMOUNT_GARBAGE": 1},
        fixable_steps=(step.step_id,),
        unfixable_steps=(step.step_id,),
    )
    planters: tuple[CasePlanter, ...] = (
        InventedNoTitlePlanter(1, step.step_id),
        OtherLienholderPlanter(1, step.step_id),
        NoPaymentsPlanter(1, step.step_id),
    )
    baseline = {step.step_id: step.baseline_full + len(messiness.fixable)}
    return ProfilePlan(Profile.EXAMPLE, (step,), baseline, planters, messiness, False, True)


def plan_for(profile: Profile) -> ProfilePlan:
    """The plan of one profile."""
    return _example_plan() if profile == Profile.EXAMPLE else _timeline_plan(profile)
