"""The registry and key holder TEST DOUBLES in-process, reached by the bank agent over httpx.

Each bank gets its own member id and its own keys for the registry and both holders.
The doubles run their real FastAPI apps (real OPRF, real Ed25519, real rules); the
bank's HTTP clients reach them through an in-process transport, and their test
controls (`/_test/stats`, `/received`, `/fail-next`, `/dump`, `/tamper`, `/checkpoint`)
are called the same way. `down` makes a service fail at the transport level.
"""

from __future__ import annotations

import base64
import json
import secrets
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi.testclient import TestClient

from onepledge_bank_agent.domain.canonical_json import canonical_json
from onepledge_bank_agent.infrastructure.http.base import HttpTimeouts
from onepledge_contract_fakes.key_holder.app import create_key_holder_app
from onepledge_contract_fakes.key_holder.config import KeyHolderConfig
from onepledge_contract_fakes.members import api_key_sha256
from onepledge_contract_fakes.registry.app import create_registry_app
from onepledge_contract_fakes.registry.config import RegistryConfig
from onepledge_contract_fakes.registry.double import RegistryDouble
from onepledge_contract_fakes.settings import MemberCredential
from onepledge_contract_fakes.testing import new_api_key, new_scalar

REGISTRY_URL = "http://registry.test"
KEY_HOLDER_URLS = {"kh1": "http://key-holder-1.test", "kh2": "http://key-holder-2.test"}
SIGNING_KEY_ID = "registry-test-1"
CONTROL_HEADER = "X-Test-Control-Key"
BANKS = ("HARBOR", "NORTHGATE", "CALDER")


@dataclass(frozen=True)
class SentRequest:
    """One request a bank agent client sent to a double, exactly as it left the client."""

    base_url: str
    method: str
    path: str
    query: dict[str, str]
    headers: dict[str, str]
    body: bytes


@dataclass(frozen=True)
class Member:
    """One bank's registry identity and its three service keys."""

    bank_code: str
    member_id: str
    registry_key: str
    kh1_key: str
    kh2_key: str


@dataclass(frozen=True)
class HolderScalars:
    """The secret scalars of the two holders. Sharing them makes tokens comparable across doubles."""

    kh1: bytes
    kh2: bytes

    @classmethod
    def fresh(cls) -> HolderScalars:
        """Two new random scalars."""
        return cls(new_scalar(), new_scalar())


def _member(code: str) -> Member:
    return Member(code, str(uuid.uuid4()), new_api_key(), new_api_key(), new_api_key())


class InProcessDoubles:
    """A registry double and two key holder doubles for up to three banks."""

    def __init__(self, scalars: HolderScalars | None = None, banks: tuple[str, ...] = BANKS) -> None:
        self.members = {code: _member(code) for code in banks}
        self.control_key = secrets.token_urlsafe(32)
        self.signing_seed = secrets.token_bytes(32)
        holders = scalars or HolderScalars.fresh()
        self.scalars = holders
        registry_config = RegistryConfig(
            members=tuple(
                MemberCredential(m.member_id, api_key_sha256(m.registry_key)) for m in self.members.values()
            ),
            signing_seed=self.signing_seed,
            signing_key_id=SIGNING_KEY_ID,
            auditor_key_sha256=api_key_sha256(new_api_key()),
            test_control_key=self.control_key,
        )
        registry_app = create_registry_app(registry_config)
        self.registry: RegistryDouble = registry_app.state.double
        self._clients: dict[str, TestClient] = {REGISTRY_URL: TestClient(registry_app, base_url=REGISTRY_URL)}
        for holder, scalar in (("kh1", holders.kh1), ("kh2", holders.kh2)):
            keys = (m.kh1_key if holder == "kh1" else m.kh2_key for m in self.members.values())
            config = KeyHolderConfig(
                holder_id=holder,
                scalars={1: scalar},
                active_version=1,
                members=tuple(
                    MemberCredential(m.member_id, api_key_sha256(key))
                    for m, key in zip(self.members.values(), keys, strict=True)
                ),
                test_control_key=self.control_key,
            )
            url = KEY_HOLDER_URLS[holder]
            self._clients[url] = TestClient(create_key_holder_app(config), base_url=url)
        self.down: set[str] = set()
        self._forged: dict[str, list[dict[str, Any]]] = {}
        self._restored: dict[str, dict[str, Any]] = {}
        self._originals: dict[str, dict[str, Any]] = {}
        self.sent: list[SentRequest] = []
        self.public_key_b64 = base64.b64encode(
            Ed25519PrivateKey.from_private_bytes(self.signing_seed)
            .public_key()
            .public_bytes(Encoding.Raw, PublicFormat.Raw)
        ).decode("ascii")

    def environment(self, bank_code: str) -> dict[str, str]:
        """The registry and key holder variables of one bank agent."""
        member = self.members[bank_code]
        return {
            "REGISTRY_URL": REGISTRY_URL,
            "REGISTRY_API_KEY": member.registry_key,
            "REGISTRY_MEMBER_ID": member.member_id,
            "REGISTRY_PUBLIC_KEYS": json.dumps({SIGNING_KEY_ID: self.public_key_b64}),
            "KEY_HOLDER_1_URL": KEY_HOLDER_URLS["kh1"],
            "KEY_HOLDER_1_API_KEY": member.kh1_key,
            "KEY_HOLDER_2_URL": KEY_HOLDER_URLS["kh2"],
            "KEY_HOLDER_2_API_KEY": member.kh2_key,
        }

    def handles(self, base_url: str) -> bool:
        """True for the registry and key holder URLs."""
        return base_url in self._clients

    def _handle(self, request: httpx.Request) -> httpx.Response:
        base = f"{request.url.scheme}://{request.url.host}"
        self.sent.append(
            SentRequest(
                base,
                request.method,
                request.url.path,
                dict(request.url.params),
                dict(request.headers),
                request.read(),
            )
        )
        if base in self.down:
            raise httpx.ConnectError("the service is stopped", request=request)
        certificate = request.url.path.removeprefix("/v1/certificates/")
        if base == REGISTRY_URL and request.method == "GET" and certificate in self._restored:
            return httpx.Response(200, json=self._restored[certificate])
        forged = self._forged.get(request.headers.get("X-API-Key", ""), [])
        if base == REGISTRY_URL and forged and request.url.path.startswith("/v1/outbox/"):
            message_id = request.url.path.split("/")[3]
            if any(message["message_id"] == message_id for message in forged):
                forged[:] = [message for message in forged if message["message_id"] != message_id]
                return httpx.Response(204)
        answer = self._clients[base].request(
            request.method, str(request.url), headers=dict(request.headers), content=request.read()
        )
        if base == REGISTRY_URL and forged and request.url.path == "/v1/outbox" and answer.status_code == 200:
            page = answer.json()
            last = max((message["position"] for message in page["messages"]), default=0)
            extra = [{**message, "position": last + n} for n, message in enumerate(forged, start=1)]
            return httpx.Response(200, json={**page, "messages": [*page["messages"], *extra]})
        return httpx.Response(answer.status_code, headers=answer.headers, content=answer.content)

    def tamper_certificate(self, bank_code: str, certificate_id: str) -> dict[str, Any]:
        """CERTIFICATE_BODY on the registry double, keeping the certificate as first signed; returns it."""
        headers = {"X-API-Key": self.members[bank_code].registry_key}
        original = self._clients[REGISTRY_URL].get(f"/v1/certificates/{certificate_id}", headers=headers)
        assert original.status_code == 200, original.text
        self._originals[certificate_id] = original.json()
        self.tamper(mode="CERTIFICATE_BODY", certificate_id=certificate_id)
        return dict(self._originals[certificate_id])

    def restore_certificate(self, certificate_id: str) -> None:
        """Undo `tamper_certificate`: from now on the registry serves the certificate as first signed.

        The double has no control that reverses CERTIFICATE_BODY, so the restored certificate
        is served here, byte for byte the document the double answered before the tamper.
        """
        self._restored[certificate_id] = self._originals[certificate_id]

    def forge_checkpoint(self, bank_code: str, body: Mapping[str, object]) -> dict[str, Any]:
        """A compromised registry: a CHECKPOINT message with `body`, signed with the registry's own key,
        delivered once to one bank ahead of the real outbox. Returns the signed checkpoint."""
        signature = Ed25519PrivateKey.from_private_bytes(self.signing_seed).sign(canonical_json(dict(body)))
        signed = {
            "body": dict(body),
            "signature": base64.b64encode(signature).decode("ascii"),
            "signing_key_id": SIGNING_KEY_ID,
        }
        self.inject_message(bank_code, "CHECKPOINT", signed)
        return signed

    def inject_message(self, bank_code: str, message_type: str, payload: Mapping[str, object]) -> None:
        """Deliver one extra outbox message to one bank, ahead of the real outbox, until acknowledged."""
        message = {
            "message_id": str(uuid.uuid4()),
            "position": 0,
            "type": message_type,
            "created_at": "2026-11-20T12:00:00Z",
            "payload": dict(payload),
        }
        self._forged.setdefault(self.members[bank_code].registry_key, []).append(message)

    def checkpoints(self) -> list[dict[str, Any]]:
        """Every checkpoint the registry signed, oldest first."""
        found: list[dict[str, Any]] = self.control(REGISTRY_URL, "GET", "/_test/dump")["checkpoints"]
        return found

    def client(self, base_url: str) -> httpx.Client:
        """An httpx client reaching one double in-process."""
        return httpx.Client(base_url=base_url, transport=httpx.MockTransport(self._handle))

    def control(
        self, base_url: str, method: str, path: str, body: Mapping[str, object] | None = None
    ) -> dict[str, Any]:
        """Call a `/_test/` endpoint of a double and return its JSON."""
        headers = {CONTROL_HEADER: self.control_key}
        answer = self._clients[base_url].request(method, path, headers=headers, json=body)
        assert answer.status_code == 200, (path, answer.status_code, answer.text)
        found: dict[str, Any] = answer.json()
        return found

    def stats(self, base_url: str = REGISTRY_URL) -> dict[str, Any]:
        """`GET /_test/stats`."""
        return self.control(base_url, "GET", "/_test/stats")

    def received(self, base_url: str = REGISTRY_URL) -> list[dict[str, str]]:
        """`GET /_test/received`."""
        requests: list[dict[str, str]] = self.control(base_url, "GET", "/_test/received")["requests"]
        return requests

    def fail_next(self, base_url: str, **spec: object) -> None:
        """`POST /_test/fail-next`."""
        self.control(base_url, "POST", "/_test/fail-next", spec)

    def tamper(self, **spec: object) -> dict[str, Any]:
        """`POST /_test/tamper` on the registry."""
        return self.control(REGISTRY_URL, "POST", "/_test/tamper", spec)

    def member_stats(self, bank_code: str, base_url: str = REGISTRY_URL) -> dict[str, int]:
        """The registry (or a holder) statistics of one bank."""
        found: dict[str, int] = self.stats(base_url)["by_member"].get(self.members[bank_code].member_id, {})
        return found

    def active_locks(self, bank_code: str) -> list[str]:
        """The asset codes this bank holds active locks on, sorted."""
        member = self.members[bank_code].member_id
        locks = self.control(REGISTRY_URL, "GET", "/_test/dump")["locks"]
        return sorted(lock["asset_token"] for lock in locks if lock["member_id"] == member and lock["active"])

    def close(self) -> None:
        """Close the in-process clients."""
        for client in self._clients.values():
            client.close()


def combined_factory(
    outside: Callable[[str, HttpTimeouts], httpx.Client], doubles: InProcessDoubles
) -> Callable[[str, HttpTimeouts], httpx.Client]:
    """An `HttpClientFactory` that reaches the doubles for their URLs and outside records otherwise."""

    def build(base_url: str, timeouts: HttpTimeouts) -> httpx.Client:
        if doubles.handles(base_url):
            return doubles.client(base_url)
        return outside(base_url, timeouts)

    return build
