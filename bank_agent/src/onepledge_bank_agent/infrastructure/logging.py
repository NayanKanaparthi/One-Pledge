"""Structured JSON logging with redaction (SPEC 16.5).

One JSON object per line on stdout with the fixed keys `ts`, `level`, `event`,
`service`, `bank_code` and, inside a request, `request_id`. Records of the standard
`logging` module (uvicorn, SQLAlchemy, httpx) are routed through the same processors.
`RedactionProcessor` runs last before rendering:

1. Key allow-list: every other key is dropped and named in `redacted_keys`.
2. Value shape: an int, a bool, null, or a short safe string; anything else is `[REDACTED]`.
3. VIN scrub: any VIN shaped run (17 VIN alphabet characters with at least 3 digits) is `[REDACTED]`.

Exceptions are rendered as their type and frame locations only, never their message.
"""

from __future__ import annotations

import logging
import re
import sys
import traceback
from types import TracebackType
from typing import TextIO

import structlog
from structlog.typing import EventDict, Processor, WrappedLogger

from onepledge_bank_agent.application.events import LogEvent
from onepledge_bank_agent.application.ports import LogValue
from onepledge_bank_agent.domain.text_rules import DIGITS
from onepledge_bank_agent.infrastructure.request_context import current_request_id

ALLOWED_KEYS: frozenset[str] = frozenset(
    {
        "ts", "level", "event", "service", "bank_code", "request_id", "logger", "submission_id",
        "loan_id", "alert_id", "registry_request_id", "certificate_id", "checkpoint_seq", "user_id",
        "role", "stage", "status", "kind", "count", "rows", "attempts", "duration_ms", "http_status",
        "method", "route", "error_code", "failure_code", "check_type", "result", "reason_code",
        "batch_index", "item_count", "exception_type", "stack", "dependency", "reason", "revision",
    }
)  # fmt: skip
REDACTED = "[REDACTED]"
SERVICE_NAME = "bank_agent"
_SAFE_VALUE = re.compile(r"^[A-Za-z0-9_.:{}/\- ]{0,128}$")
_SAFE_STACK = re.compile(r"^[A-Za-z0-9_.:{}/\- \n<>]*$")
_VIN_SHAPE = re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b", re.IGNORECASE)
_MIN_VIN_DIGITS = 3
QUIET_LOGGERS = ("httpx", "httpcore", "uvicorn.access", "uvicorn", "uvicorn.error", "asyncio")


def scrub_vin_shapes(text: str) -> str:
    """Replace every VIN shaped run (any case) by `[REDACTED]`."""

    def replace(match: re.Match[str]) -> str:
        run = match.group(0)
        return REDACTED if sum(char in DIGITS for char in run) >= _MIN_VIN_DIGITS else run

    return _VIN_SHAPE.sub(replace, text)


class RedactionProcessor:
    """Allow-list, value shape and VIN scrub, applied to every record just before rendering."""

    def __call__(self, logger: WrappedLogger, method_name: str, event_dict: EventDict) -> EventDict:
        """A new record holding only allowed keys with safe values, and the names of dropped keys."""
        output: EventDict = {}
        dropped = sorted(key for key in event_dict if key not in ALLOWED_KEYS)
        for key, value in event_dict.items():
            if key in ALLOWED_KEYS:
                output[key] = safe_value(key, value)
        if dropped:
            output["redacted_keys"] = dropped
        return output


def safe_value(key: str, value: object) -> object:
    """The value if its shape is safe (after the VIN scrub), else `[REDACTED]`."""
    if value is None or isinstance(value, bool | int):
        return value
    if not isinstance(value, str):
        return REDACTED
    pattern = _SAFE_STACK if key == "stack" else _SAFE_VALUE
    if not pattern.fullmatch(value):
        return REDACTED
    return scrub_vin_shapes(value)


def _frames(tb: TracebackType | None) -> str:
    return "\n".join(f"{frame.filename}:{frame.lineno} {frame.name}" for frame in traceback.extract_tb(tb))


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


def _foreign_record(logger: WrappedLogger, method_name: str, event_dict: EventDict) -> EventDict:
    """A third party record keeps its logger name, its level and its exception type; its message is never
    rendered (SPEC_DECISIONS 43.6)."""
    record = event_dict.get("_record")
    event_dict["event"] = LogEvent.LIBRARY_LOG.value
    if isinstance(record, logging.LogRecord):
        event_dict["logger"] = record.name
        if record.exc_info and record.exc_info[0] is not None:
            event_dict["exception_type"] = record.exc_info[0].__name__
    return event_dict


FRAMEWORK_KEYS = ("_record", "_from_structlog")


def strip_framework_keys(logger: WrappedLogger, method_name: str, event_dict: EventDict) -> EventDict:
    """Remove, by exact name, the keys the logging framework itself injects (SPEC_DECISIONS 53.8), so the
    redaction that follows names every other key it drops."""
    for key in FRAMEWORK_KEYS:
        event_dict.pop(key, None)
    return event_dict


class ThirdPartyFilter(logging.Filter):
    """Drops records of third party loggers below WARNING (SPEC_DECISIONS 43.6). A record of this package
    carries its structlog event dictionary as its message."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Keep the package's records and third party records at WARNING or above."""
        return isinstance(record.msg, dict) or record.levelno >= logging.WARNING


class _FixedKeys:
    def __init__(self, bank_code: str | None) -> None:
        self._bank_code = bank_code

    def __call__(self, logger: WrappedLogger, method_name: str, event_dict: EventDict) -> EventDict:
        event_dict["service"] = SERVICE_NAME
        event_dict["bank_code"] = self._bank_code
        request_id = current_request_id()
        if request_id is not None:
            event_dict.setdefault("request_id", request_id)
        return event_dict


def configure_logging(bank_code: str | None, level: str, stream: TextIO | None = None) -> None:
    """Route structlog and the standard library through one JSON handler (stdout by default)."""
    shared: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True, key="ts"),
        _FixedKeys(bank_code),
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
            strip_framework_keys,
            _render_exception,
            RedactionProcessor(),
            structlog.processors.JSONRenderer(sort_keys=False),
        ],
    )
    handler = logging.StreamHandler(stream if stream is not None else sys.stdout)
    handler.setFormatter(formatter)
    handler.addFilter(ThirdPartyFilter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    for name in QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


class StructlogOperationalLog:
    """The `OperationalLog` port on structlog."""

    def __init__(self, name: str) -> None:
        self._logger: structlog.stdlib.BoundLogger = structlog.stdlib.get_logger(name)

    def info(self, event: str, **fields: LogValue) -> None:
        """An informational record."""
        self._logger.info(event, **fields)

    def warning(self, event: str, **fields: LogValue) -> None:
        """A warning record."""
        self._logger.warning(event, **fields)

    def failure(self, event: str, error: BaseException, **fields: LogValue) -> None:
        """An error record with the exception's type and frame locations (never its message)."""
        self._logger.error(event, exc_info=error, **fields)
