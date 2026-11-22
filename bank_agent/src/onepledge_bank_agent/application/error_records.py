"""The facts every `error` log record carries (SPEC 16.5, SPEC_DECISIONS 66).

An operator searching the log for the code a caller or the worker output quotes must
find it, so every error record names `error_code`; `exception_type` is added by the log
itself, `http_status` when a dependency answered, and `request_id` from the bound
request context when there is one (the HTTP layer of WP4).
"""

from __future__ import annotations

from onepledge_bank_agent.application.ports import LogValue
from onepledge_bank_agent.domain.enums import FailureCode
from onepledge_bank_agent.domain.errors import DomainError, PipelineStageError


def error_code_of(error: BaseException) -> str:
    """The code of a named error, or INTERNAL_ERROR for any other exception."""
    if isinstance(error, DomainError):
        return error.code
    return FailureCode.INTERNAL_ERROR.value


def http_facts(error: BaseException) -> dict[str, LogValue]:
    """`http_status` for a dependency failure (null when no answer arrived); nothing otherwise."""
    if isinstance(error, PipelineStageError):
        return {"http_status": error.http_status}
    return {}
