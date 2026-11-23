"""Checking BIND_HOST and PORT before the bank agent serves (SPEC_DECISIONS 73).

`ServeSettings.check_binding()` runs at `serve` start, before anything else is opened: a
socket of the literal's family is bound to `(BIND_HOST, 0)` and closed, then
`(BIND_HOST, PORT)` is tried the same way. Every socket call, creating the socket included,
is inside the guarded section, so no socket error reaches the last resort handler. The
sentences name the variables and never their values, and are word for word those of
outside records and the doubles. The probe is a port (`BindProbe`): unit tests inject each
outcome without touching the network, and an integration test runs the real one.
"""

from __future__ import annotations

import errno
import ipaddress
import socket
from collections.abc import Callable, Mapping
from enum import Enum
from types import MappingProxyType

from onepledge_bank_agent.infrastructure.operator_errors import CONFIGURATION_ERROR, OperatorError

IPV6 = 6
SUGGESTION = "Set BIND_HOST to 127.0.0.1 or 0.0.0.0."
PORT_UNAVAILABLE = "PORT_UNAVAILABLE"


class BindOutcome(Enum):
    """What binding a socket to an address gave."""

    BOUND = "BOUND"
    NO_FAMILY = "NO_FAMILY"
    NOT_LOCAL = "NOT_LOCAL"
    IN_USE = "IN_USE"
    OTHER = "OTHER"


HOST_SENTENCES: Mapping[BindOutcome, str] = MappingProxyType(
    {
        BindOutcome.NO_FAMILY: f"BIND_HOST is an IPv6 address and this machine has no IPv6. {SUGGESTION}",
        BindOutcome.NOT_LOCAL: f"BIND_HOST is not an address of this machine. {SUGGESTION}",
        BindOutcome.OTHER: f"BIND_HOST cannot be bound. {SUGGESTION}",
    }
)
PORT_SENTENCE = "PORT is already in use or not permitted on BIND_HOST; set another PORT."

type BindProbe = Callable[[str, int], BindOutcome]


def family_of(host: str) -> socket.AddressFamily:
    """The address family of an IP literal (the settings loader accepts nothing else)."""
    return socket.AF_INET6 if ipaddress.ip_address(host).version == IPV6 else socket.AF_INET


def outcome_of(error: OSError, family: socket.AddressFamily) -> BindOutcome:
    """The outcome a socket error stands for."""
    if error.errno in (errno.EAFNOSUPPORT, errno.EPROTONOSUPPORT) and family == socket.AF_INET6:
        return BindOutcome.NO_FAMILY
    if error.errno == errno.EADDRNOTAVAIL:
        return BindOutcome.NOT_LOCAL
    if error.errno in (errno.EADDRINUSE, errno.EACCES):
        return BindOutcome.IN_USE
    return BindOutcome.OTHER


def socket_probe(host: str, port: int) -> BindOutcome:
    """Bind a stream socket of the literal's family to `(host, port)` and close it."""
    family = family_of(host)
    try:
        with socket.socket(family, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind((host, port))
    except OSError as error:
        return outcome_of(error, family)
    return BindOutcome.BOUND


def check_binding(host: str, port: int, probe: BindProbe = socket_probe) -> None:
    """Refuse with one sentence unless BIND_HOST is an address of this machine and PORT is free."""
    outcome = probe(host, 0)
    if outcome is not BindOutcome.BOUND:
        raise _refused(outcome)
    outcome = probe(host, port)
    if outcome is BindOutcome.IN_USE:
        raise OperatorError(PORT_SENTENCE, PORT_UNAVAILABLE)
    if outcome is not BindOutcome.BOUND:
        raise _refused(outcome)


def _refused(outcome: BindOutcome) -> OperatorError:
    return OperatorError(HOST_SENTENCES.get(outcome, HOST_SENTENCES[BindOutcome.OTHER]), CONFIGURATION_ERROR)
