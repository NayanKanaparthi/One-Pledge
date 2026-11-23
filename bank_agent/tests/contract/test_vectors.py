"""SPEC 21.4 vectors: tokenisation reproduces `tokenization.json`; the verifier accepts the certificate
and checkpoint of `signatures.json`, read through the bank's own registry client, and rejects each with
any one byte of the signed body or of the signature changed."""

from __future__ import annotations

import base64
import json
import uuid
from collections.abc import Mapping
from datetime import date
from typing import Any

import httpx
import oprf
import pytest

from bank_testkit.data import CONTRACTS
from onepledge_bank_agent.domain.certificate_rules import delivered_sha256, verify_proof
from onepledge_bank_agent.domain.checkpoint_rules import event_header_hash, signature_failure
from onepledge_bank_agent.domain.enums import CertificateFailure, CheckpointFailure, TokenKind
from onepledge_bank_agent.domain.registry_records import EventHeader, SignedCertificate
from onepledge_bank_agent.domain.tokenization import token_from_point, token_inputs
from onepledge_bank_agent.domain.values import BorrowerRef, Lei, LoanTerms, Money
from onepledge_bank_agent.domain.vin import Vin
from onepledge_bank_agent.infrastructure.crypto.blinder import OprfBlinder
from onepledge_bank_agent.infrastructure.crypto.signatures import Ed25519SignatureVerifier
from onepledge_bank_agent.infrastructure.http.base import CallPolicy, RetryingHttpCaller
from onepledge_bank_agent.infrastructure.http.registry_client import RegistryHttpClient

pytestmark = pytest.mark.contract

EXAMPLES = CONTRACTS / "examples"
SIGNATURES = json.loads((EXAMPLES / "vectors" / "signatures.json").read_text(encoding="utf-8"))
TOKENIZATION = json.loads((EXAMPLES / "vectors" / "tokenization.json").read_text(encoding="utf-8"))
VERIFIER = Ed25519SignatureVerifier({SIGNATURES["signing_key_id"]: SIGNATURES["public_key_b64"]})


class QuietLog:
    def info(self, event: str, **fields: object) -> None:
        """Nothing."""

    def warning(self, event: str, **fields: object) -> None:
        """Nothing."""

    def failure(self, event: str, error: BaseException, **fields: object) -> None:
        """Nothing."""


def example(name: str) -> dict[str, Any]:
    found: dict[str, Any] = json.loads((EXAMPLES / "registry" / name).read_text(encoding="utf-8"))
    return found


def registry_answering(document: object) -> RegistryHttpClient:
    """The bank's registry client, every call answered 200 with `document`."""
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=document))
    client = httpx.Client(base_url="http://registry.test", transport=transport)
    return RegistryHttpClient(
        RetryingHttpCaller(client, "k" * 40, "registry", QuietLog(), CallPolicy(lambda _: None))
    )


def test_the_vector_loan_gives_the_vector_tokens_through_the_bank_agent_code() -> None:
    loan = TOKENIZATION["loan"]
    terms = LoanTerms(
        Vin.parse(loan["vin"]),
        BorrowerRef(loan["borrower_ref"]),
        date.fromisoformat(loan["origination_date"]),
        Money(loan["original_amount_cents"]),
    )
    blind = base64.b64decode(TOKENIZATION["blind_scalar_b64"])
    scalars = [base64.b64decode(TOKENIZATION[f"holder_{n}_scalar_b64"]) for n in (1, 2)]
    blinder = OprfBlinder(lambda: blind)
    tokens = {}
    for token_input in token_inputs(terms, Lei(loan["lender_lei"])):
        blinded = blinder.blind(token_input.oprf_input())
        element = base64.b64decode(blinded.element)
        for scalar in scalars:
            element = bytes(oprf.mask(scalar).mask(oprf.data(element)))
        point = blinder.unblind(blinded, base64.b64encode(element).decode())
        assert point is not None
        tokens[token_input.kind.value] = token_from_point(token_input.kind, point)
    assert tokens == {entry["kind"]: entry["token"] for entry in TOKENIZATION["tokens"]}
    assert list(tokens) == [kind.value for kind in TokenKind]


def flipped(data: bytes, index: int) -> bytes:
    return data[:index] + bytes([data[index] ^ 0x01]) + data[index + 1 :]


def test_the_vector_certificate_verifies_and_any_one_changed_byte_is_rejected() -> None:
    certificate = registry_answering(example("certificate.response.json")).certificate(uuid.uuid4())
    vector = SIGNATURES["certificate"]
    message = vector["canonical_body_utf8"].encode("utf-8")
    assert verify_proof(certificate, delivered_sha256(certificate.body), VERIFIER) is None
    assert certificate.body_hash == vector["body_hash"] and certificate.signature == vector["signature"]
    key = SIGNATURES["signing_key_id"]
    signature = base64.b64decode(vector["signature"])
    for index in range(len(message)):
        assert not VERIFIER.verifies(key, flipped(message, index), vector["signature"]), index
    for index in range(len(signature)):
        assert not VERIFIER.verifies(key, message, base64.b64encode(flipped(signature, index)).decode()), (
            index
        )


def test_a_changed_certificate_gives_the_reason_of_the_first_failing_step() -> None:
    certificate = registry_answering(example("certificate.response.json")).certificate(uuid.uuid4())
    summary = certificate.body["summary"]
    assert isinstance(summary, Mapping)
    body = {**certificate.body, "summary": {**summary, "locked": 3}}
    signature = base64.b64decode(certificate.signature)
    cases = {
        CertificateFailure.UNKNOWN_SIGNING_KEY: SignedCertificate(
            certificate.body,
            certificate.body_hash,
            certificate.signature,
            "registry-other-1",
            certificate.issued_at,
        ),
        CertificateFailure.BODY_HASH_MISMATCH: SignedCertificate(
            body,
            certificate.body_hash,
            certificate.signature,
            certificate.signing_key_id,
            certificate.issued_at,
        ),
        CertificateFailure.BAD_SIGNATURE: SignedCertificate(
            certificate.body,
            certificate.body_hash,
            base64.b64encode(flipped(signature, 0)).decode(),
            certificate.signing_key_id,
            certificate.issued_at,
        ),
    }
    for reason, changed in cases.items():
        assert verify_proof(changed, delivered_sha256(changed.body), VERIFIER) is reason


def test_the_vector_checkpoint_verifies_and_any_one_changed_byte_is_rejected() -> None:
    checkpoint = example("checkpoint_latest.response.json")
    vector = SIGNATURES["checkpoint"]
    key = checkpoint["signing_key_id"]
    assert signature_failure(checkpoint["body"], key, checkpoint["signature"], VERIFIER) is None
    message = vector["canonical_body_utf8"].encode("utf-8")
    for index in range(len(message)):
        assert not VERIFIER.verifies(key, flipped(message, index), vector["signature"]), index
    signature = base64.b64decode(vector["signature"])
    for index in range(len(signature)):
        assert not VERIFIER.verifies(key, message, base64.b64encode(flipped(signature, index)).decode()), (
            index
        )
    body = {**checkpoint["body"], "last_event_seq": checkpoint["body"]["last_event_seq"] + 1}
    assert signature_failure(body, key, checkpoint["signature"], VERIFIER) is CheckpointFailure.BAD_SIGNATURE
    assert (
        signature_failure(checkpoint["body"], "registry-other-1", checkpoint["signature"], VERIFIER)
        is CheckpointFailure.UNKNOWN_SIGNING_KEY
    )


def test_the_vector_event_header_hashes_to_its_event_hash() -> None:
    vector = SIGNATURES["ledger_event"]
    header = json.loads(vector["canonical_header_utf8"])
    event = EventHeader(
        header["seq"],
        header["event_type"],
        header["payload_hash"],
        header["prev_hash"],
        vector["event_hash"],
        header["recorded_at"],
        header["schema_version"],
    )
    assert event_header_hash(event) == vector["event_hash"]
    headers = registry_answering(example("ledger_events.headers.response.json")).event_headers(0, 1000)
    assert headers.events and all(event_header_hash(e) == e.event_hash for e in headers.events)
