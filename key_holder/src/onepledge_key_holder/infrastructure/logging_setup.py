"""Log lines of the key holder: one JSON object per line on stdout.

A record holds an event name and a few named facts: the method, the route template, the
status, the duration, the request id, an exception type. It never holds a path, a query
string, a header value, an element or an API key (decision 6, CONTEXT "Boundaries").
"""

from __future__ import annotations

import structlog
from structlog.typing import FilteringBoundLogger

HTTP_REQUEST = "http_request"
SERVICE_STARTED = "service_started"
UNHANDLED_EXCEPTION = "unhandled_exception"


def configure_logging() -> None:
    """JSON lines with a UTC timestamp and the level on every record."""
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.JSONRenderer(sort_keys=True),
        ],
        cache_logger_on_first_use=False,
    )


def get_logger(service: str, holder_id: str) -> FilteringBoundLogger:
    """The logger of one holder: every record names the service and the holder."""
    logger: FilteringBoundLogger = structlog.get_logger().bind(service=service, holder_id=holder_id)
    return logger
