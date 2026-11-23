"""Addresses tests use: the IPv4 wildcard as a named value and a free local port (SPEC_DECISIONS 119)."""

from __future__ import annotations

import socket

LOOPBACK_IPV4 = "127.0.0.1"
# The wildcard is a BIND_HOST value under test; nothing in this module binds to it.
WILDCARD_IPV4 = "0.0.0.0"  # noqa: S104 - a value under test, never bound here


def free_port() -> int:
    """A TCP port that was free on the loopback address a moment ago."""
    with socket.socket() as probe:
        probe.bind((LOOPBACK_IPV4, 0))
        port: int = probe.getsockname()[1]
        return port
