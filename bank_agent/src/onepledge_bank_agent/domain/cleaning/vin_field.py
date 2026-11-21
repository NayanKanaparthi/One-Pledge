"""The VIN rules of SPEC 10.4, applied to a trimmed, non-empty, safe cell."""

from __future__ import annotations

from onepledge_bank_agent.domain.cleaning.header import VIN
from onepledge_bank_agent.domain.cleaning.model import FieldResult, fixed
from onepledge_bank_agent.domain.enums import CleaningProblem
from onepledge_bank_agent.domain.values import CleaningIssue
from onepledge_bank_agent.domain.vin import VIN_ALPHABET, VIN_LENGTH, Vin, vin_defect

_SEPARATORS = (" ", "-")
_LOOK_ALIKES = str.maketrans({"O": "0", "Q": "0", "I": "1"})


def clean_vin(text: str) -> FieldResult[Vin]:
    """Upper case, separators removed, look-alike letters substituted when that makes it valid."""
    issues: list[CleaningIssue] = []
    value = text
    if any(char.islower() for char in value):
        value = value.upper()
        issues.append(fixed(VIN, CleaningProblem.VIN_CASE_NORMALISED))
    if any(separator in value for separator in _SEPARATORS):
        for separator in _SEPARATORS:
            value = value.replace(separator, "")
        issues.append(fixed(VIN, CleaningProblem.VIN_SEPARATORS_REMOVED))
    if len(value) != VIN_LENGTH:
        return FieldResult.refused(VIN, CleaningProblem.VIN_LENGTH_INVALID, *issues)
    substituted = value.translate(_LOOK_ALIKES)
    if substituted != value and vin_defect(substituted) is None:
        value = substituted
        issues.append(fixed(VIN, CleaningProblem.VIN_CHARACTER_SUBSTITUTED))
    if any(char not in VIN_ALPHABET for char in value):
        return FieldResult.refused(VIN, CleaningProblem.VIN_CHARACTER_INVALID, *issues)
    if vin_defect(value) is not None:
        return FieldResult.refused(VIN, CleaningProblem.VIN_CHECK_DIGIT_INVALID, *issues)
    return FieldResult(Vin(value), tuple(issues))
