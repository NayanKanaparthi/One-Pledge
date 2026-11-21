"""Seeded random streams (SPEC 5.2).

Every random choice of the generator comes from `random.Random(f"{seed}:{name}")`,
one stream per concern, so a change in one part does not shift the others and the
same seed always gives the same bytes.
"""

from __future__ import annotations

import random
import uuid
from collections.abc import Sequence
from datetime import date, timedelta
from math import isqrt


class RandomStream:
    """One named, seeded stream with the helpers the generator needs."""

    def __init__(self, seed: int, name: str) -> None:
        self._rng = random.Random(f"{seed}:{name}")  # noqa: S311 - deterministic synthetic data, not security

    def integer(self, low: int, high: int) -> int:
        """Uniform integer in [low, high]."""
        return self._rng.randint(low, high)

    def pick[T](self, options: Sequence[T]) -> T:
        """One element of a non-empty sequence."""
        return options[self._rng.randrange(len(options))]

    def weighted[T](self, options: tuple[T, ...], weights: tuple[int, ...]) -> T:
        """One element with integer weights."""
        total = sum(weights)
        point = self._rng.randrange(total)
        for option, weight in zip(options, weights, strict=True):
            if point < weight:
                return option
            point -= weight
        return options[-1]

    def sample[T](self, options: list[T], count: int) -> list[T]:
        """`count` distinct elements, in random order."""
        return self._rng.sample(options, count)

    def shuffled[T](self, items: list[T]) -> list[T]:
        """A shuffled copy."""
        copy = list(items)
        self._rng.shuffle(copy)
        return copy

    def chance(self, numerator: int, denominator: int) -> bool:
        """True with probability numerator / denominator."""
        return self._rng.randrange(denominator) < numerator

    def digits(self, count: int) -> str:
        """A string of `count` random decimal digits."""
        return "".join(str(self._rng.randrange(10)) for _ in range(count))

    def date_between(self, first: date, last: date) -> date:
        """Uniform date in [first, last]."""
        return first + timedelta(days=self._rng.randint(0, (last - first).days))

    def new_uuid(self) -> str:
        """A UUID version 4 drawn from this stream."""
        return str(uuid.UUID(int=self._rng.getrandbits(128), version=4))

    def triangular_int(self, low: int, mode: int, high: int) -> int:
        """Integer from a triangular distribution, computed with integers only."""
        resolution = 1 << 32
        point = self._rng.getrandbits(32)
        span = high - low
        left = mode - low
        if point * span < left * resolution:
            return low + _isqrt_floor(point * span * left, resolution)
        return high - _isqrt_floor((resolution - point) * span * (high - mode), resolution)


def _isqrt_floor(numerator: int, denominator: int) -> int:
    return isqrt(numerator // denominator)


class SeededStreams:
    """Factory of named streams for one seed."""

    def __init__(self, seed: int) -> None:
        self.seed = seed

    def stream(self, name: str) -> RandomStream:
        """The stream for one concern."""
        return RandomStream(self.seed, name)
