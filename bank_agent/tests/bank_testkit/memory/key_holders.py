"""A fake `Blinder` and scripted `KeyHolderClient`s that agree with each other, no curve arithmetic.

`FakeBlinder.blind` turns an OPRF input into an element that names it; each holder appends
its own mark with its key version; `unblind` accepts exactly an element that passed
holder 1 then holder 2 and returns a point that depends on the input and both versions,
so the same loan always gets the same tokens at the same key versions and different
ones after a key change. A holder can be scripted to raise (`failures`), to answer with
another key version (`version`, or `rotate_after` calls), or to corrupt its elements
(`corrupt`).
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, field

from onepledge_bank_agent.application.registry_ports import Blinded
from onepledge_bank_agent.domain.errors import PipelineStageError
from onepledge_bank_agent.domain.registry_records import Evaluation


@dataclass(frozen=True)
class FakeBlinded:
    """The element sent for one input, and the input it came from."""

    element: str
    oprf_input: bytes


class FakeBlinder:
    """Blinds by naming the input; unblinds only what both holders evaluated, in order."""

    def __init__(self) -> None:
        self.blinded = 0
        self._inputs: dict[str, bytes] = {}

    def blind(self, oprf_input: bytes) -> FakeBlinded:
        """An element carrying the input's digest and a fresh counter (a new blind each time)."""
        self.blinded += 1
        element = f"e{self.blinded}:{hashlib.sha256(oprf_input).hexdigest()}"
        self._inputs[element] = oprf_input
        return FakeBlinded(element, oprf_input)

    def unblind(self, blinded: Blinded, evaluated: str) -> bytes | None:
        """The point of the input under both holders' key versions, or None for a bad element."""
        oprf_input = self._inputs[blinded.element]
        prefix = blinded.element + "|h1v"
        if not evaluated.startswith(prefix):
            return None
        first, marker, second = evaluated[len(prefix) :].partition("|h2v")
        if not marker or not first or not second:
            return None
        return hashlib.sha256(oprf_input + f"/{first}/{second}".encode()).digest()


@dataclass
class ScriptedKeyHolder:
    """One key holder: appends `|h{number}v{version}` to every element it is sent."""

    number: int
    version: int = 1
    failures: list[PipelineStageError] = field(default_factory=list)
    corrupt: bool = False
    rotate_after: int | None = None
    calls: list[int] = field(default_factory=list)

    @property
    def dependency(self) -> str:
        """`key_holder_1` or `key_holder_2`."""
        return f"key_holder_{self.number}"

    def evaluate(self, elements: Sequence[str]) -> Evaluation:
        """The evaluated elements in request order, or the next scripted failure."""
        self.calls.append(len(elements))
        if self.rotate_after is not None and len(self.calls) > self.rotate_after:
            self.version, self.rotate_after = self.version + 1, None
        if self.failures:
            raise self.failures.pop(0)
        mark = "|bad" if self.corrupt else f"|h{self.number}v{self.version}"
        return Evaluation(self.version, tuple(element + mark for element in elements))
