"""The request id of the work in progress, carried in a context variable (SPEC 4).

The HTTP layer binds the caller's request id; logging adds it to every record and the
outbound clients forward it in `X-Request-ID`. Pipeline work has none.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator
from contextvars import ContextVar

_REQUEST_ID: ContextVar[str | None] = ContextVar("onepledge_request_id", default=None)


def current_request_id() -> str | None:
    """The bound request id, if any."""
    return _REQUEST_ID.get()


@contextlib.contextmanager
def bound_request_id(request_id: uuid.UUID) -> Iterator[str]:
    """Bind a request id for the duration of a block."""
    token = _REQUEST_ID.set(str(request_id))
    try:
        yield str(request_id)
    finally:
        _REQUEST_ID.reset(token)
