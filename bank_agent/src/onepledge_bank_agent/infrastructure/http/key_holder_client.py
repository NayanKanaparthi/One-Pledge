"""The client of one key holder: `POST /v1/evaluate` (`contracts/key_holder.yaml`, SPEC 9.3, 13.3).

The request carries a fresh request id and the blinded elements, and pins no key
version. The answer must hold exactly as many elements as were sent, each 44 characters
of canonical base64 decoding to 32 bytes that are not all zero, the request id in its
canonical spelling, and one key version (SPEC_DECISIONS 75). A wrong
count or an invalid element is PROTOCOL_VIOLATION and a body outside the schema is
MALFORMED_RESPONSE (both KEY_HOLDER_REJECTED); every other failure is classified by
`classification.py` (SPEC_DECISIONS 49).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated

import httpx
from pydantic import Field

from onepledge_bank_agent.application.ports import IdGenerator
from onepledge_bank_agent.domain.dependency_errors import KeyHolderProtocolError
from onepledge_bank_agent.domain.registry_records import Evaluation
from onepledge_bank_agent.infrastructure.crypto.blinder import decode_point
from onepledge_bank_agent.infrastructure.http.answers import CanonicalUuid, Strict, parse
from onepledge_bank_agent.infrastructure.http.base import RetryingHttpCaller
from onepledge_bank_agent.infrastructure.http.classification import failure

EVALUATE_ROUTE = "/v1/evaluate"
ELEMENT = r"^[A-Za-z0-9+/]{43}=$"


class EvaluateAnswer(Strict):
    """`EvaluateResponse`."""

    request_id: CanonicalUuid
    holder_id: str
    key_version: Annotated[int, Field(ge=1)]
    elements: list[Annotated[str, Field(pattern=ELEMENT)]]


class KeyHolderHttpClient:
    """Implements the `KeyHolderClient` port for one holder."""

    def __init__(self, caller: RetryingHttpCaller, dependency: str, ids: IdGenerator) -> None:
        self._caller = caller
        self._dependency = dependency
        self._ids = ids
        self._errors = caller.errors

    @property
    def dependency(self) -> str:
        """`key_holder_1` or `key_holder_2`."""
        return self._dependency

    def evaluate(self, elements: Sequence[str]) -> Evaluation:
        """Evaluated elements in request order, with the key version the holder used."""
        request_id = self._ids.new_id()
        body = {"request_id": str(request_id), "key_version": None, "elements": list(elements)}
        response = self._caller.post(EVALUATE_ROUTE, EVALUATE_ROUTE, body)
        if response.status_code != httpx.codes.OK:
            raise failure(response, self._errors)
        answer, _ = parse(EvaluateAnswer, response, self._errors)
        valid = all(decode_point(element) is not None for element in answer.elements)
        if answer.request_id != request_id or len(answer.elements) != len(elements) or not valid:
            raise KeyHolderProtocolError(self._dependency, response.status_code)
        return Evaluation(answer.key_version, tuple(answer.elements))
