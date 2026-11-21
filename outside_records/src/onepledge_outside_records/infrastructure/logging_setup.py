"""Structured JSON logging with redaction for outside records (SPEC 6.2, 16.5 rules).

The rules of SPEC 16.5 with the outside records allow-list (SPEC_DECISIONS 5): one
JSON object per line on stdout with fixed keys, an allow-list of keys, a value shape
rule and a VIN shaped scrub, and standard library records (uvicorn) routed through
the same processors. Exception messages and local variables are never rendered;
only the exception type and the frame locations.
"""

from __future__ import annotations

import logging
import re
import sys
import traceback
from enum import StrEnum
from types import TracebackType

import structlog
from structlog.typing import EventDict, Processor, WrappedLogger

# SPEC 16.5 is the bank agent's allow-list. Outside records keeps the keys of that list it
# writes, plus `client` (the API client's name), as SPEC_DECISIONS 5 sets out.
ALLOWED_KEYS: frozenset[str] = frozenset(
    {
        "ts", "level", "event", "service", "request_id", "logger", "method", "route",
        "http_status", "duration_ms", "exception_type", "stack", "client",
    }
)  # fmt: skip
REDACTED = "[REDACTED]"
_SAFE_VALUE = re.compile(r"^[A-Za-z0-9_.:{}/\- ]{0,128}$")
_SAFE_STACK = re.compile(r"^[A-Za-z0-9_.:{}/\- \n<>]*$")
_VIN_SHAPE = re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b", re.IGNORECASE)
_MIN_VIN_DIGITS = 3
_QUIET_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access", "httpx", "httpcore", "asyncio")


class LogEvent(StrEnum):
    """Every event name the service writes."""

    SERVICE_STARTED = "service_started"
    HTTP_REQUEST = "http_request"
    UNHANDLED_EXCEPTION = "unhandled_exception"
    DATABASE_UNAVAILABLE = "database_unavailable"
    LIBRARY_LOG = "library_log"


def scrub_vin_shapes(text: str) -> str:
    """Replace every VIN shaped run (17 VIN alphabet characters, at least 3 digits)."""

    def replace(match: re.Match[str]) -> str:
        run = match.group(0)
        return REDACTED if sum(ch.isdigit() for ch in run) >= _MIN_VIN_DIGITS else run

    return _VIN_SHAPE.sub(replace, text)


class RedactionProcessor:
    """Last processor before rendering: allow-list, value shape, VIN scrub."""

    def __call__(self, logger: WrappedLogger, method_name: str, event_dict: EventDict) -> EventDict:
        """Return a new event dict holding only allowed keys with safe values."""
        output: EventDict = {}
        dropped = sorted(key for key in event_dict if key not in ALLOWED_KEYS and not key.startswith("_"))
        for key, value in event_dict.items():
            if key in ALLOWED_KEYS:
                output[key] = _safe_value(key, value)
        if dropped:
            output["redacted_keys"] = dropped
        return output


def _safe_value(key: str, value: object) -> object:
    if value is None or isinstance(value, bool | int):
        return value
    if not isinstance(value, str):
        return REDACTED
    pattern = _SAFE_STACK if key == "stack" else _SAFE_VALUE
    if not pattern.fullmatch(value):
        return REDACTED
    return scrub_vin_shapes(value)


def _render_exception(logger: WrappedLogger, method_name: str, event_dict: EventDict) -> EventDict:
    exc_info = event_dict.pop("exc_info", None)
    if exc_info is True:
        exc_info = sys.exc_info()
    if isinstance(exc_info, BaseException):
        exc_info = (type(exc_info), exc_info, exc_info.__traceback__)
    if isinstance(exc_info, tuple) and exc_info[0] is not None:
        event_dict["exception_type"] = exc_info[0].__name__
        event_dict["stack"] = _frames(exc_info[2])
    return event_dict


def _frames(tb: TracebackType | None) -> str:
    frames = traceback.extract_tb(tb)
    return "\n".join(f"{frame.filename}:{frame.lineno} {frame.name}" for frame in frames)


def _foreign_record(logger: WrappedLogger, method_name: str, event_dict: EventDict) -> EventDict:
    record = event_dict.get("_record")
    event_dict["event"] = LogEvent.LIBRARY_LOG.value
    if isinstance(record, logging.LogRecord):
        event_dict["logger"] = record.name
    return event_dict


class _ServiceName:
    def __init__(self, service: str) -> None:
        self._service = service

    def __call__(self, logger: WrappedLogger, method_name: str, event_dict: EventDict) -> EventDict:
        event_dict["service"] = self._service
        return event_dict


def configure_logging(service: str, level: str) -> None:
    """Route structlog and the standard library through one JSON handler on stdout."""
    shared: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True, key="ts"),
        _ServiceName(service),
    ]
    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=[_foreign_record, *shared],
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            _render_exception,
            RedactionProcessor(),
            structlog.processors.JSONRenderer(sort_keys=False),
        ],
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    for name in _QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """A structlog logger bound to a module name."""
    logger: structlog.stdlib.BoundLogger = structlog.stdlib.get_logger(name)
    return logger
