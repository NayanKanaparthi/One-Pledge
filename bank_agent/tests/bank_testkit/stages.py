"""A stage double for tests of stage dispatch and the worker."""

from __future__ import annotations

import uuid
from collections.abc import Callable

from bank_testkit.world import World
from onepledge_bank_agent.domain.enums import TapeStatus


class FakeStage:
    """A stage that runs `behaviour` (which may raise) and then advances the tape by one status."""

    def __init__(
        self, world: World, reached: TapeStatus, behaviour: Callable[[], None] | None = None
    ) -> None:
        self._world = world
        self.reached = reached
        self.behaviour = behaviour
        self.runs = 0

    def run(self, submission_id: uuid.UUID) -> None:
        self.runs += 1
        if self.behaviour is not None:
            self.behaviour()
        with self._world.units() as unit:
            tape = unit.tapes.get(submission_id)
            assert tape is not None
            marks = {TapeStatus.CLEANED: tape.mark_cleaned, TapeStatus.CHECKED: tape.mark_checked}
            marks[self.reached](self._world.clock.now())
            unit.tapes.save(tape)
            unit.commit()


def raising(error: BaseException) -> Callable[[], None]:
    def behaviour() -> None:
        raise error

    return behaviour
