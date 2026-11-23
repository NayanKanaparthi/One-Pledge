"""The real BIND_HOST probe on this machine (SPEC_DECISIONS 73, point 6).

`ServeSettings.check_binding()` with the socket probe, on the five addresses the decision
names: the IPv4 loopback and wildcard start, an address of another machine is refused with
its sentence, and the two IPv6 addresses either start or give the IPv6 sentence. A socket
error never escapes as anything but one of the fixed sentences, and a port another socket
holds gives the PORT sentence.
"""

from __future__ import annotations

import socket

import pytest

from onepledge_bank_agent.infrastructure.operator_errors import OperatorError
from onepledge_bank_agent.infrastructure.settings import ServeSettings

pytestmark = pytest.mark.integration

NO_IPV6 = "BIND_HOST is an IPv6 address and this machine has no IPv6. Set BIND_HOST to 127.0.0.1 or 0.0.0.0."
NOT_LOCAL = "BIND_HOST is not an address of this machine. Set BIND_HOST to 127.0.0.1 or 0.0.0.0."
PORT_TAKEN = "PORT is already in use or not permitted on BIND_HOST; set another PORT."
STARTED = "started"


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
        return port


def outcome(host: str, port: int) -> str:
    settings = ServeSettings.from_env(
        {"BANK_CODE": "HARBOR", "BANK_NAME": "Harbor Bank", "BIND_HOST": host, "PORT": str(port)}
    )
    try:
        settings.check_binding()
    except OperatorError as refused:
        return refused.sentence
    return STARTED


@pytest.mark.parametrize(
    ("host", "allowed"),
    [
        ("127.0.0.1", {STARTED}),
        ("0.0.0.0", {STARTED}),  # noqa: S104 - the wildcard is an accepted value under test
        ("8.8.8.8", {NOT_LOCAL}),
        ("::", {STARTED, NO_IPV6}),
        ("::1", {STARTED, NO_IPV6}),
    ],
)
def test_the_real_probe_accepts_this_machine_and_refuses_another(host: str, allowed: set[str]) -> None:
    assert outcome(host, free_port()) in allowed


def test_a_port_another_socket_listens_on_is_refused_with_the_port_sentence() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as holder:
        holder.bind(("127.0.0.1", 0))
        holder.listen()
        assert outcome("127.0.0.1", holder.getsockname()[1]) == PORT_TAKEN
