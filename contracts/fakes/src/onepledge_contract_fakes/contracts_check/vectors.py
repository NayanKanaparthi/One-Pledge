"""Recomputing the published test vectors from their stated inputs (SPEC 7.4 item 4, SPEC 9).

Part of the contract checker in the TEST DOUBLES package `onepledge_contract_fakes`.

Tokenisation: canonical value strings of SPEC 9.2, input bytes and the two-holder
blind OPRF of SPEC 9.3, with the fixed scalars and blind of the vector file.
Signatures: canonical JSON, SHA-256 and Ed25519 over the certificate, checkpoint
and ledger event examples.
"""

from __future__ import annotations

import base64
import hashlib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import oprf
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from onepledge_contract_fakes.canonical_json import canonical_json, sha256_hex
from onepledge_contract_fakes.contracts_check.documents import load_json
from onepledge_contract_fakes.registry.ledger import event_hash

INPUT_PREFIX = b"onepledge:v1:"
TOKEN_PREFIX = b"onepledge:v1:token"
SEPARATOR = b"\x1f"


def canonical_values(loan: Mapping[str, Any]) -> dict[str, str]:
    """The canonical value string of each token kind for one loan (SPEC 9.2)."""
    vin = str(loan["vin"])
    borrower = f"{loan['lender_lei']}|{loan['borrower_ref']}"
    cents = int(loan["original_amount_cents"])
    day = str(loan["origination_date"])
    return {
        "ASSET": "AUTO|" + vin,
        "VIN": vin,
        "BORROWER": borrower,
        "AMOUNT_EXACT": f"{borrower}|{cents}",
        "AMOUNT_100": f"{borrower}|{cents // 10000}",
        "AMOUNT_1000": f"{borrower}|{cents // 100000}",
        "DATE_DAY": f"{borrower}|{day}",
        "DATE_MONTH": f"{borrower}|{day[:7]}",
    }


def input_bytes(kind: str, value: str) -> bytes:
    """The OPRF input of one (kind, canonical value) pair (SPEC 9.3 step 1)."""
    return INPUT_PREFIX + kind.encode("ascii") + SEPARATOR + value.encode("utf-8")


def token_of(kind: str, unblinded: bytes) -> str:
    """The token derived from an unblinded point (SPEC 9.3 step 6)."""
    return hashlib.sha256(TOKEN_PREFIX + SEPARATOR + kind.encode("ascii") + SEPARATOR + unblinded).hexdigest()


def _b64(point: bytes) -> str:
    return base64.b64encode(point).decode("ascii")


def check_tokenization(vector: Mapping[str, Any]) -> list[str]:
    """Problems found when recomputing every value of `tokenization.json`."""
    problems: list[str] = []
    k1 = oprf.mask.from_base64(vector["holder_1_scalar_b64"])
    k2 = oprf.mask.from_base64(vector["holder_2_scalar_b64"])
    blind = oprf.mask.from_base64(vector["blind_scalar_b64"])
    expected_values = canonical_values(vector["loan"])
    for entry in vector["tokens"]:
        kind = entry["kind"]
        value = expected_values.get(kind)
        if value is None or value != entry["canonical_value"]:
            problems.append(f"{kind}: canonical_value differs from the SPEC 9.2 rule")
            continue
        raw = input_bytes(kind, value)
        point = oprf.data.hash(raw)
        blinded = blind.mask(point)
        after_1 = k1.mask(blinded)
        after_2 = k2.mask(after_1)
        unblinded = blind.unmask(after_2)
        recomputed = {
            "input_hex": raw.hex(),
            "blinded": _b64(bytes(blinded)),
            "after_holder_1": _b64(bytes(after_1)),
            "after_holder_2": _b64(bytes(after_2)),
            "unblinded": _b64(bytes(unblinded)),
            "token": token_of(kind, bytes(unblinded)),
        }
        problems.extend(f"{kind}: {name} differs" for name, got in recomputed.items() if got != entry[name])
        if bytes(k2.mask(k1.mask(point))) != bytes(unblinded):
            problems.append(f"{kind}: two-holder result differs from direct evaluation")
    if sorted(entry["kind"] for entry in vector["tokens"]) != sorted(expected_values):
        problems.append("the vector does not list exactly the eight token kinds")
    return problems


KEY_HOLDER_EXAMPLES = ("key_holder/evaluate.request.json", "key_holder/evaluate.response.json")


def vector_dependencies(name: str, vector: Mapping[str, Any]) -> list[str]:
    """The example files (relative to `examples/`) a vector file is checked against."""
    if name == "tokenization.json":
        return list(KEY_HOLDER_EXAMPLES)
    files = []
    for entry_name in ("certificate", "checkpoint", "ledger_event"):
        entry = vector.get(entry_name)
        if isinstance(entry, Mapping) and isinstance(entry.get("file"), str):
            files.append(str(entry["file"]))
    return files


def check_key_holder_examples(vector: Mapping[str, Any], examples_dir: Path) -> list[str]:
    """The key holder examples must carry the vector's blinded and holder 1 values."""
    request, response = (load_json(examples_dir / name) for name in KEY_HOLDER_EXAMPLES)
    blinded = [entry["blinded"] for entry in vector["tokens"]]
    after_1 = [entry["after_holder_1"] for entry in vector["tokens"]]
    problems = []
    if not isinstance(request, dict) or request.get("elements") != blinded:
        problems.append("key_holder/evaluate.request.json elements differ from the vector's blinded values")
    if not isinstance(response, dict) or response.get("elements") != after_1:
        problems.append("key_holder/evaluate.response.json elements differ from the vector's holder 1 values")
    return problems


def check_signatures(vector: Mapping[str, Any], examples_dir: Path) -> list[str]:
    """Problems found when recomputing every value of `signatures.json`."""
    problems: list[str] = []
    key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(vector["signing_seed_b64"]))
    public = _b64(key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))
    if public != vector["public_key_b64"]:
        problems.append("public_key_b64 does not belong to signing_seed_b64")
    problems.extend(_check_signed(key, vector["certificate"], examples_dir, with_body_hash=True))
    problems.extend(_check_signed(key, vector["checkpoint"], examples_dir, with_body_hash=False))
    problems.extend(_check_ledger_event(vector["ledger_event"], examples_dir))
    return problems


def _check_signed(
    key: Ed25519PrivateKey, entry: Mapping[str, Any], examples_dir: Path, with_body_hash: bool
) -> list[str]:
    name = entry["file"]
    example = load_json(examples_dir / name)
    if not isinstance(example, dict):
        return [f"{name} is not a JSON object"]
    canonical = canonical_json(example["body"])
    problems = []
    if canonical.decode("utf-8") != entry["canonical_body_utf8"]:
        problems.append(f"{name}: canonical body differs from canonical_body_utf8")
    signature = _b64(key.sign(canonical))
    if signature != entry["signature"] or example.get("signature") != entry["signature"]:
        problems.append(f"{name}: signature differs from the recomputed Ed25519 signature")
    if with_body_hash and (
        sha256_hex(canonical) != entry["body_hash"] or example.get("body_hash") != entry["body_hash"]
    ):
        problems.append(f"{name}: body_hash differs from the SHA-256 of the canonical body")
    return problems


def _check_ledger_event(entry: Mapping[str, Any], examples_dir: Path) -> list[str]:
    name = entry["file"]
    page = load_json(examples_dir / name)
    if not isinstance(page, dict):
        return [f"{name} is not a JSON object"]
    problems = []
    payload_bytes = canonical_json(entry["payload"])
    if (
        payload_bytes.decode("utf-8") != entry["canonical_payload_utf8"]
        or sha256_hex(payload_bytes) != entry["payload_hash"]
    ):
        problems.append("ledger_event: payload canonical form or payload_hash differs")
    previous = "0" * 64
    for event in page["events"]:
        recomputed = event_hash(
            event["seq"], event["event_type"], event["payload_hash"], event["prev_hash"], event["recorded_at"]
        )
        if event["prev_hash"] != previous or event["event_hash"] != recomputed:
            problems.append(f"{name}: event {event['seq']} does not chain to the previous event")
        if event["seq"] == entry["seq"]:
            problems.extend(_compare_vector_event(name, event, entry))
        previous = event["event_hash"]
    return problems


def _compare_vector_event(name: str, event: Mapping[str, Any], entry: Mapping[str, Any]) -> list[str]:
    header = {
        key: event[key]
        for key in ("event_type", "payload_hash", "prev_hash", "recorded_at", "schema_version")
    }
    header["seq"] = event["seq"]
    problems = []
    if canonical_json(header).decode("utf-8") != entry["canonical_header_utf8"]:
        problems.append(f"{name}: event {event['seq']} canonical header differs from canonical_header_utf8")
    if event["event_hash"] != entry["event_hash"] or event["payload_hash"] != entry["payload_hash"]:
        problems.append(f"{name}: event {event['seq']} differs from the ledger_event vector")
    return problems
