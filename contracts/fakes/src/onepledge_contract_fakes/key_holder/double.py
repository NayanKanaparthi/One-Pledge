"""In-memory key holder double (TEST DOUBLE of `contracts/key_holder.yaml`).

Each element is a blinded ristretto255 point. The double multiplies it by the
secret scalar of the requested key version, exactly as a real holder would, so a
bank that blinds, sends to holder 1, then holder 2, and unblinds obtains
`k2 * k1 * P`. Elements are never stored or logged.
"""

from __future__ import annotations

import base64
import binascii
import threading
from dataclasses import dataclass
from datetime import datetime

import oprf

from onepledge_contract_fakes.clock import Clock
from onepledge_contract_fakes.daily_cap import DailyCap
from onepledge_contract_fakes.errors import (
    ConfigurationError,
    ErrorDetail,
    InvalidElementError,
    KeyVersionUnavailableError,
)
from onepledge_contract_fakes.key_holder.config import KeyHolderConfig
from onepledge_contract_fakes.members import MemberDirectory

POINT_BYTES = 32
_IDENTITY = bytes(POINT_BYTES)


@dataclass(frozen=True)
class EvaluationRequest:
    """A validated evaluate call: request id, optional pinned version, base64 elements."""

    request_id: str
    key_version: int | None
    elements: tuple[str, ...]


@dataclass(frozen=True)
class EvaluationResult:
    """The evaluated elements in request order and the version used."""

    request_id: str
    holder_id: str
    key_version: int
    elements: tuple[str, ...]


@dataclass(frozen=True)
class EvaluationRecord:
    """One accepted request in the evaluation log. Holds no element."""

    member_id: str
    request_id: str
    element_count: int
    at: datetime


class KeyHolderDouble:
    """All behaviour and state of one key holder, behind one re-entrant lock."""

    def __init__(self, config: KeyHolderConfig, clock: Clock) -> None:
        if config.active_version not in config.scalars:
            raise ConfigurationError("FAKE_KH_ACTIVE_VERSION", "must name a configured key version")
        self._masks: dict[int, oprf.mask] = {}
        for version, scalar in config.scalars.items():
            if len(scalar) != POINT_BYTES or scalar == _IDENTITY:
                raise ConfigurationError("FAKE_KH_KEYS", "must hold 32 byte scalars that are not zero")
            self._masks[version] = oprf.mask(scalar)
        self._config = config
        self._clock = clock
        self._members = MemberDirectory(config.members)
        self._cap = DailyCap(config.daily_element_cap, clock)
        self._log: list[EvaluationRecord] = []
        self._lock = threading.RLock()

    @property
    def holder_id(self) -> str:
        """`kh1` or `kh2`."""
        return self._config.holder_id

    @property
    def active_version(self) -> int:
        """The key version used when a request does not pin one."""
        return self._config.active_version

    def authenticate(self, api_key: str | None) -> str:
        """The member id of an API key, or UnauthenticatedError."""
        return self._members.authenticate(api_key)

    def identify(self, api_key: str | None) -> str | None:
        """The member id of an API key, or None. Used only to label received bodies."""
        return self._members.identify(api_key)

    def evaluate(self, member_id: str, request: EvaluationRequest) -> EvaluationResult:
        """Evaluate every element with one key version, or reject the whole request."""
        with self._lock:
            version = self._config.active_version if request.key_version is None else request.key_version
            mask = self._masks.get(version)
            if mask is None:
                raise KeyVersionUnavailableError
            evaluated = _evaluate_all(mask, request.elements)
            self._cap.consume(member_id, len(request.elements))
            self._log.append(
                EvaluationRecord(member_id, request.request_id, len(request.elements), self._clock.now())
            )
            return EvaluationResult(request.request_id, self.holder_id, version, evaluated)

    def evaluation_log(self) -> tuple[EvaluationRecord, ...]:
        """Every accepted request, oldest first."""
        with self._lock:
            return tuple(self._log)

    def stats(self) -> dict[str, object]:
        """Totals and per member counts of requests and elements."""
        with self._lock:
            by_member: dict[str, dict[str, int]] = {}
            for record in self._log:
                entry = by_member.setdefault(record.member_id, {"requests": 0, "elements": 0})
                entry["requests"] += 1
                entry["elements"] += record.element_count
            total = {
                "requests": len(self._log),
                "elements": sum(record.element_count for record in self._log),
            }
            return {"total": total, "by_member": by_member}


def _evaluate_all(mask: oprf.mask, elements: tuple[str, ...]) -> tuple[str, ...]:
    results: list[str] = []
    failing: list[int] = []
    for index, encoded in enumerate(elements):
        evaluated = _evaluate_one(mask, encoded)
        if evaluated is None:
            failing.append(index)
        else:
            results.append(evaluated)
    if failing:
        details = tuple(ErrorDetail(f"elements[{index}]", "NOT_A_VALID_POINT") for index in failing)
        raise InvalidElementError(details=details)
    return tuple(results)


def _evaluate_one(mask: oprf.mask, encoded: str) -> str | None:
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        return None
    if len(raw) != POINT_BYTES or raw == _IDENTITY:
        return None
    try:
        result = bytes(mask.mask(oprf.data(raw)))
    except RuntimeError:
        return None
    if result == _IDENTITY:
        return None
    return base64.b64encode(result).decode("ascii")
