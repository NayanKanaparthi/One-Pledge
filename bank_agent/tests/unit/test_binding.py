"""BIND_HOST and PORT at serve start (SPEC_DECISIONS 73): each outcome of the probe and its sentence.

The probe is injected, so nothing here opens a socket; the real probe runs in
`tests/integration/test_real_bind_probe.py`. The sentences are those of outside records and the
doubles, word for word.
"""

from __future__ import annotations

import errno
import os
import socket

import pytest

from onepledge_bank_agent.infrastructure.binding import BindOutcome, BindProbe, outcome_of
from onepledge_bank_agent.infrastructure.operator_errors import OperatorError
from onepledge_bank_agent.infrastructure.settings import ServeSettings

pytestmark = pytest.mark.unit

NO_IPV6 = "BIND_HOST is an IPv6 address and this machine has no IPv6. Set BIND_HOST to 127.0.0.1 or 0.0.0.0."
NOT_LOCAL = "BIND_HOST is not an address of this machine. Set BIND_HOST to 127.0.0.1 or 0.0.0.0."
OTHER = "BIND_HOST cannot be bound. Set BIND_HOST to 127.0.0.1 or 0.0.0.0."
PORT_TAKEN = "PORT is already in use or not permitted on BIND_HOST; set another PORT."
ENVIRONMENT = {"BANK_CODE": "HARBOR", "BANK_NAME": "Harbor Bank", "PORT": "18299", "BIND_HOST": "::1"}


def scripted(host_outcome: BindOutcome, port_outcome: BindOutcome = BindOutcome.BOUND) -> BindProbe:
    def probe(host: str, port: int) -> BindOutcome:
        del host
        return host_outcome if port == 0 else port_outcome

    return probe


def settings(**changes: str) -> ServeSettings:
    return ServeSettings.from_env({**ENVIRONMENT, **changes})


@pytest.mark.parametrize(
    ("outcome", "sentence"),
    [(BindOutcome.NO_FAMILY, NO_IPV6), (BindOutcome.NOT_LOCAL, NOT_LOCAL), (BindOutcome.OTHER, OTHER),
     (BindOutcome.IN_USE, OTHER)],
)  # fmt: skip
def test_each_outcome_of_the_host_probe_has_its_sentence(outcome: BindOutcome, sentence: str) -> None:
    with pytest.raises(OperatorError) as raised:
        settings().check_binding(scripted(outcome))
    assert (raised.value.sentence, raised.value.code) == (sentence, "CONFIGURATION_ERROR")


def test_the_host_is_probed_on_port_zero_first_then_the_port() -> None:
    calls: list[tuple[str, int]] = []

    def probe(host: str, port: int) -> BindOutcome:
        calls.append((host, port))
        return BindOutcome.BOUND

    settings(BIND_HOST="127.0.0.1").check_binding(probe)
    assert calls == [("127.0.0.1", 0), ("127.0.0.1", 18299)]


def test_a_taken_port_names_the_variables_and_no_value() -> None:
    with pytest.raises(OperatorError) as raised:
        settings(BIND_HOST="127.0.0.1").check_binding(scripted(BindOutcome.BOUND, BindOutcome.IN_USE))
    assert (raised.value.sentence, raised.value.code) == (PORT_TAKEN, "PORT_UNAVAILABLE")
    assert "127.0.0.1" not in raised.value.sentence and "18299" not in raised.value.sentence


@pytest.mark.parametrize("port_outcome", [BindOutcome.NOT_LOCAL, BindOutcome.OTHER])
def test_a_port_that_fails_for_another_reason_is_a_host_sentence(port_outcome: BindOutcome) -> None:
    with pytest.raises(OperatorError) as raised:
        settings().check_binding(scripted(BindOutcome.BOUND, port_outcome))
    assert raised.value.sentence in (NOT_LOCAL, OTHER)


@pytest.mark.parametrize(
    ("family", "code", "outcome"),
    [
        (socket.AF_INET6, errno.EAFNOSUPPORT, BindOutcome.NO_FAMILY),
        (socket.AF_INET6, errno.EPROTONOSUPPORT, BindOutcome.NO_FAMILY),
        (socket.AF_INET, errno.EAFNOSUPPORT, BindOutcome.OTHER),
        (socket.AF_INET, errno.EADDRNOTAVAIL, BindOutcome.NOT_LOCAL),
        (socket.AF_INET6, errno.EADDRNOTAVAIL, BindOutcome.NOT_LOCAL),
        (socket.AF_INET, errno.EADDRINUSE, BindOutcome.IN_USE),
        (socket.AF_INET, errno.EACCES, BindOutcome.IN_USE),
        (socket.AF_INET, errno.ENOBUFS, BindOutcome.OTHER),
    ],
)
def test_each_socket_error_is_one_outcome(
    family: socket.AddressFamily, code: int, outcome: BindOutcome
) -> None:
    assert outcome_of(OSError(code, os.strerror(code)), family) is outcome
