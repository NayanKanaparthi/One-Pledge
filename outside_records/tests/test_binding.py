"""BIND_HOST and PORT at serve start (SPEC_DECISIONS 73): each outcome of the probe and its sentence.

The real probe on each address is in test_cli.py, next to the serving helpers. The sentences
are the doubles' sentences, word for word.
"""

from __future__ import annotations

import errno
import os
import socket

import pytest

from onepledge_outside_records.domain.errors import OperatorError
from onepledge_outside_records.infrastructure import binding
from onepledge_outside_records.infrastructure.binding import BindOutcome, check_binding, socket_probe

NO_IPV6 = "BIND_HOST is an IPv6 address and this machine has no IPv6. Set BIND_HOST to 127.0.0.1 or 0.0.0.0."
NOT_LOCAL = "BIND_HOST is not an address of this machine. Set BIND_HOST to 127.0.0.1 or 0.0.0.0."
OTHER = "BIND_HOST cannot be bound. Set BIND_HOST to 127.0.0.1 or 0.0.0.0."
PORT_TAKEN = "PORT is already in use or not permitted on BIND_HOST; set another PORT."


def scripted(host_outcome: BindOutcome, port_outcome: BindOutcome = BindOutcome.BOUND) -> binding.BindProbe:
    def probe(host: str, port: int) -> BindOutcome:
        del host
        return host_outcome if port == 0 else port_outcome

    return probe


@pytest.mark.unit
@pytest.mark.parametrize(
    ("outcome", "sentence"),
    [(BindOutcome.NO_FAMILY, NO_IPV6), (BindOutcome.NOT_LOCAL, NOT_LOCAL), (BindOutcome.OTHER, OTHER)],
)
def test_each_outcome_of_the_host_probe_has_its_sentence(outcome: BindOutcome, sentence: str) -> None:
    with pytest.raises(OperatorError) as raised:
        check_binding("::1", 8080, scripted(outcome))
    assert raised.value.sentence == sentence


@pytest.mark.unit
def test_the_host_is_probed_on_port_zero_first_then_the_port() -> None:
    calls: list[tuple[str, int]] = []

    def probe(host: str, port: int) -> BindOutcome:
        calls.append((host, port))
        return BindOutcome.BOUND

    check_binding("127.0.0.1", 8080, probe)
    assert calls == [("127.0.0.1", 0), ("127.0.0.1", 8080)]


@pytest.mark.unit
def test_a_taken_port_names_the_variables_and_no_value() -> None:
    with pytest.raises(OperatorError) as raised:
        check_binding("127.0.0.1", 18_299, scripted(BindOutcome.BOUND, BindOutcome.IN_USE))
    assert raised.value.sentence == PORT_TAKEN
    assert "127.0.0.1" not in raised.value.sentence and "18299" not in raised.value.sentence


@pytest.mark.unit
@pytest.mark.parametrize(
    ("host", "code", "outcome"),
    [
        ("::", errno.EAFNOSUPPORT, BindOutcome.NO_FAMILY),
        ("127.0.0.1", errno.EAFNOSUPPORT, BindOutcome.OTHER),
        ("192.0.2.1", errno.EADDRNOTAVAIL, BindOutcome.NOT_LOCAL),
        ("127.0.0.1", errno.EADDRINUSE, BindOutcome.IN_USE),
        ("127.0.0.1", errno.EACCES, BindOutcome.IN_USE),
        ("127.0.0.1", errno.ENOBUFS, BindOutcome.OTHER),
    ],
)
def test_creating_the_socket_is_guarded_and_each_errno_is_one_outcome(
    monkeypatch: pytest.MonkeyPatch, host: str, code: int, outcome: BindOutcome
) -> None:
    def refuse(*_: object, **__: object) -> socket.socket:
        raise OSError(code, os.strerror(code))

    monkeypatch.setattr(socket, "socket", refuse)
    assert socket_probe(host, 0) is outcome
