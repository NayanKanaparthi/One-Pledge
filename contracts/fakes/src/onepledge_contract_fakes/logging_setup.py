"""Structured JSON logging with redaction for the test doubles (TEST DOUBLES).

Same rules as the bank agent (SPEC 16.5): one JSON object per line on stdout with
fixed keys, an allow-list of keys, a value shape rule and a VIN shaped scrub, and
standard library records (uvicorn) routed through the same processors. Exception
messages and local variables are never rendered; only the exception type and the
frame locations.
"""

from __future__ import annotations

import logging
import re
import sys
import traceback
from enum import StrEnum
from types import TracebackType
from typing import TextIO

import structlog
from structlog.typing import EventDict, Processor, WrappedLogger

ALLOWED_KEYS: frozenset[str] = frozenset(
    {
        "ts", "level", "event", "service", "bank_code", "request_id", "logger", "submission_id",
        "loan_id", "alert_id", "registry_request_id", "certificate_id", "checkpoint_seq", "user_id",
        "role", "stage", "status", "kind", "count", "rows", "attempts", "duration_ms", "http_status",
        "method", "route", "error_code", "failure_code", "check_type", "result", "reason_code",
        "batch_index", "item_count", "exception_type", "stack", "dependency", "revision",
    }
)  # fmt: skip
REDACTED = "[REDACTED]"
_SAFE_VALUE = re.compile(r"^[A-Za-z0-9_.:{}/\- ]{0,128}$")
_SAFE_STACK = re.compile(r"^[A-Za-z0-9_.:{}/\- \n<>]*$")
_VIN_SHAPE = re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b", re.IGNORECASE)
_MIN_VIN_DIGITS = 3
_ASCII_DIGITS = frozenset("0123456789")
UVICORN_APP_ERROR = "Exception in ASGI application"
_QUIET_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access", "httpx", "httpcore", "asyncio")


class LogEvent(StrEnum):
    """Every event name the doubles write."""

    SERVICE_STARTED = "service_started"
    HTTP_REQUEST = "http_request"
    UNHANDLED_EXCEPTION = "unhandled_exception"
    INTERNAL_ERROR = "internal_error"
    SERVER_ERROR = "server_error"
    LIBRARY_LOG = "library_log"


def scrub_vin_shapes(text: str) -> str:
    """Replace every VIN shaped run (17 VIN alphabet characters, at least 3 digits)."""

    def replace(match: re.Match[str]) -> str:
        run = match.group(0)
        return REDACTED if sum(ch in _ASCII_DIGITS for ch in run) >= _MIN_VIN_DIGITS else run

    return _VIN_SHAPE.sub(replace, text)


class RedactionProcessor:
    """Last processor before rendering: allow-list, value shape, VIN scrub."""

    def __call__(self, logger: WrappedLogger, method_name: str, event_dict: EventDict) -> EventDict:
        """Return a new event dict holding only allowed keys with safe values."""
        output: EventDict = {}
        # The formatter's own keys (`_record`, `_from_structlog`) are removed by name before this
        # processor runs, so every key dropped here is named, whatever its first character.
        dropped = sorted(key for key in event_dict if key not in ALLOWED_KEYS)
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
        event_dict["stack"] = "\n".join(frame_lines(exc_info[2]))
    return event_dict


def frame_lines(tb: TracebackType | None) -> list[str]:
    """`file:line function` per frame: never a message, a source line or a local variable."""
    return [
        f"{frame.f_code.co_filename}:{line} {frame.f_code.co_name}" for frame, line in traceback.walk_tb(tb)
    ]


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


class DuplicateApplicationErrorFilter(logging.Filter):
    """Drops uvicorn's own record of an exception that escaped the app.

    The app's catch-all handler has already written the one `unhandled_exception` record
    with its type and frames; Starlette then re-raises the exception and uvicorn would
    log it a second time (SPEC_DECISIONS 22, finding 3). uvicorn's other records pass.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        """False for the duplicate application error record."""
        return not (record.exc_info is not None and record.getMessage().startswith(UVICORN_APP_ERROR))


class LibraryRecordFilter(logging.Filter):
    """Drops third party records below WARNING (SPEC_DECISIONS 43.6).

    A library record carries no fact the doubles may log: it is rendered as `library_log`
    with its logger, level and exception type only. Below WARNING such a line says nothing,
    so it is not written. Records of this package (structlog event dicts) always pass.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        """False for a library record below WARNING."""
        return isinstance(record.msg, dict) or record.levelno >= logging.WARNING


def configure_logging(service: str, level: str, stream: TextIO | None = None) -> None:
    """Route structlog and the standard library through one JSON handler.

    `service` is the contract's health value (`registry`, `key_holder`). The stream is
    stdout for `serve` and stderr for every other command (SPEC_DECISIONS 22, 27).
    """
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
    handler = logging.StreamHandler(stream if stream is not None else sys.stdout)
    handler.setFormatter(formatter)
    handler.addFilter(DuplicateApplicationErrorFilter())
    handler.addFilter(LibraryRecordFilter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    for name in _QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """A structlog logger bound to a module name."""
    logger: structlog.stdlib.BoundLogger = structlog.stdlib.get_logger(name)
    return logger
