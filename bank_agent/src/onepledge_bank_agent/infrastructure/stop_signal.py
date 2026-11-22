"""`EventStopSignal`: the `StopSignal` port on a `threading.Event`, set by SIGTERM or SIGINT."""

from __future__ import annotations

import threading


class EventStopSignal:
    """A stop request shared between a signal handler and the worker loop."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def request_stop(self) -> None:
        """Ask the worker to stop after the unit in progress (SPEC_DECISIONS 51)."""
        self._event.set()

    def is_set(self) -> bool:
        """True once a stop was requested."""
        return self._event.is_set()

    def wait(self, seconds: float) -> bool:
        """Sleep up to `seconds`, waking early on a stop."""
        return self._event.wait(seconds)
