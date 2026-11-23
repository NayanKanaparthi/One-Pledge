"""Which repository port methods a parity case covers (SPEC_DECISIONS 63, 127).

Every test of `tests/ports/` runs against the in-memory store and PostgreSQL through the `ports`
fixture. `@parity("Port.method", ...)` names the methods a case writes through and reads back;
`tests/unit/test_port_parity.py` reads these names from the test sources and fails when a public
method of a repository port has no case. The decorator changes nothing about the test.
"""

from __future__ import annotations

from collections.abc import Callable


def parity[F: Callable[..., None]](*methods: str) -> Callable[[F], F]:
    """Name the port methods (`Port.method`) the decorated parity case covers."""
    del methods  # read from the source by the coverage test

    def mark(test: F) -> F:
        return test

    return mark
