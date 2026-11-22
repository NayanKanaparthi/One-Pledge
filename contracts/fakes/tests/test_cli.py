"""Command lines of the doubles: help, configuration errors and a real served process."""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import select
import socket
import subprocess
import sys
import time
import urllib.request
from collections.abc import Callable

import pytest

from onepledge_contract_fakes.cli_output import CliOutput
from onepledge_contract_fakes.key_holder import cli as key_holder_cli
from onepledge_contract_fakes.members import api_key_sha256
from onepledge_contract_fakes.registry import cli as registry_cli
from onepledge_contract_fakes.testing import new_api_key, new_members, new_scalar

# A serve call that refuses in process would serve forever if the refusal were missing, so each one
# has a short limit of its own (SPEC_DECISIONS 76).
SERVE_REFUSAL_LIMIT = pytest.mark.timeout(15)
UNUSED_PORT = "18099"

ENV_PREFIXES = ("HOLDER_ID", "PORT", "FAKE_", "REGISTRY_", "BIND_HOST", "LOG_LEVEL")


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
        return port


def members_json() -> str:
    members, _ = new_members(2)
    return json.dumps([{"member_id": m.member_id, "api_key_sha256": m.api_key_sha256} for m in members])


def registry_env(port: int) -> dict[str, str]:
    return {
        "PORT": str(port),
        "FAKE_REGISTRY_SIGNING_SEED_B64": base64.b64encode(new_scalar()).decode(),
        "REGISTRY_SIGNING_KEY_ID": "registry-local-1",
        "FAKE_REGISTRY_MEMBERS": members_json(),
        "FAKE_REGISTRY_AUDITOR_KEY_SHA256": api_key_sha256(new_api_key()),
    }


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for name in list(os.environ):
        if name.startswith(ENV_PREFIXES):
            monkeypatch.delenv(name)
    return monkeypatch


@pytest.mark.unit
@pytest.mark.parametrize("build", [key_holder_cli.build_parser, registry_cli.build_parser])
def test_help_describes_the_serve_command(
    build: Callable[[], argparse.ArgumentParser], capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exited:
        build().parse_args(["--help"])
    assert exited.value.code == 0
    assert "TEST DOUBLE" in capsys.readouterr().out


@pytest.mark.unit
def test_missing_configuration_is_one_sentence_naming_the_variable(clean_env: pytest.MonkeyPatch) -> None:
    err = io.StringIO()
    assert key_holder_cli.main(["serve"], CliOutput(io.StringIO(), err)) == 1
    assert err.getvalue() == "Configuration error: HOLDER_ID is required but not set.\n"
    err = io.StringIO()
    assert registry_cli.main(["serve"], CliOutput(io.StringIO(), err)) == 1
    assert err.getvalue().startswith("Configuration error: REGISTRY_SIGNING_KEY_ID ")


@pytest.mark.integration
@SERVE_REFUSAL_LIMIT
def test_a_port_in_use_is_one_sentence(clean_env: pytest.MonkeyPatch) -> None:
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        port = taken.getsockname()[1]
        for name, value in registry_env(port).items():
            clean_env.setenv(name, value)
        err = io.StringIO()
        assert registry_cli.main(["serve"], CliOutput(io.StringIO(), err)) == 1
        assert err.getvalue() == "PORT is already in use or not permitted on BIND_HOST; set another PORT.\n"


def key_holder_env(keys: str, active: str = "1") -> dict[str, str]:
    return {
        "HOLDER_ID": "kh1",
        "PORT": UNUSED_PORT,
        "FAKE_KH_KEYS": keys,
        "FAKE_KH_ACTIVE_VERSION": active,
        "FAKE_KH_MEMBERS": members_json(),
    }


SCALAR = base64.b64encode(new_scalar()).decode()
ONE = base64.b64encode((1).to_bytes(32, "little")).decode()


@pytest.mark.unit
@SERVE_REFUSAL_LIMIT
@pytest.mark.parametrize(
    ("env", "sentence"),
    [
        (key_holder_env(json.dumps({"\u00b2": SCALAR})), "FAKE_KH_KEYS must map positive integer versions"),
        (
            key_holder_env(json.dumps({"\u0663": SCALAR}), "3"),
            "FAKE_KH_KEYS must map positive integer versions",
        ),
        (key_holder_env(json.dumps({"9" * 5000: SCALAR})), "FAKE_KH_KEYS must map positive integer versions"),
        (key_holder_env('{"1": ' + "9" * 5000 + "}"), "FAKE_KH_KEYS must be valid JSON"),
        (
            key_holder_env(json.dumps({"1": SCALAR}), "9" * 5000),
            "FAKE_KH_ACTIVE_VERSION is out of range",
        ),
        (key_holder_env(json.dumps({"1": ONE})), "FAKE_KH_KEYS must not contain a scalar of zero or one"),
        (
            {**key_holder_env(json.dumps({"1": SCALAR})), "BIND_HOST": "999.1.1.1"},
            "BIND_HOST must be an IPv4 or IPv6 address; set it to 127.0.0.1 or 0.0.0.0.",
        ),
    ],
)
def test_a_malformed_setting_is_one_line_naming_the_variable(
    clean_env: pytest.MonkeyPatch, env: dict[str, str], sentence: str
) -> None:
    for name, value in env.items():
        clean_env.setenv(name, value)
    out, err = io.StringIO(), io.StringIO()
    assert key_holder_cli.main(["serve"], CliOutput(out, err)) == 1
    assert err.getvalue().startswith(f"Configuration error: {sentence}")
    assert err.getvalue().count("\n") == 1 and "Unexpected error" not in err.getvalue()
    assert out.getvalue() == ""


@pytest.mark.integration
@SERVE_REFUSAL_LIMIT
def test_a_bind_host_that_is_not_local_is_one_sentence(clean_env: pytest.MonkeyPatch) -> None:
    for name, value in {**registry_env(free_port()), "BIND_HOST": "192.0.2.77"}.items():
        clean_env.setenv(name, value)
    err = io.StringIO()
    assert registry_cli.main(["serve"], CliOutput(io.StringIO(), err)) == 1
    assert (
        err.getvalue()
        == "BIND_HOST is not an address of this machine. Set BIND_HOST to 127.0.0.1 or 0.0.0.0.\n"
    )


@pytest.mark.integration
def test_the_registry_serves_health_as_a_process() -> None:
    port = free_port()
    env = {**{k: v for k, v in os.environ.items() if not k.startswith(ENV_PREFIXES)}, **registry_env(port)}
    script = "from onepledge_contract_fakes.registry.cli import main; raise SystemExit(main(['serve']))"
    process = subprocess.Popen(  # noqa: S603 - fixed interpreter and arguments
        [sys.executable, "-c", script], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    try:
        body = None
        for _ in range(100):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=1) as response:
                    body = json.loads(response.read())
                break
            except OSError:
                time.sleep(0.1)
        assert body == {"status": "ok", "service": "registry", "test_double": True}
    finally:
        process.terminate()
        stdout, _ = process.communicate(timeout=10)
    lines = [json.loads(line) for line in stdout.decode().splitlines() if line.strip()]
    assert lines and lines[0]["event"] == "service_started" and lines[0]["service"] == "registry"


# SPEC_DECISIONS 73.6: the real probe with each kind of address. This machine may have no IPv6,
# so `::` and `::1` may give the IPv6 sentence; never the last resort one.
NO_IPV6_SENTENCE = (
    "BIND_HOST is an IPv6 address and this machine has no IPv6. Set BIND_HOST to 127.0.0.1 or 0.0.0.0."
)
NOT_LOCAL_SENTENCE = "BIND_HOST is not an address of this machine. Set BIND_HOST to 127.0.0.1 or 0.0.0.0."


def serve_outcome(cli_module: str, env: dict[str, str]) -> tuple[str, str]:
    """Start a double's serve as a process: ("started", "") once it logs service_started, else its stderr."""
    script = f"from onepledge_contract_fakes.{cli_module}.cli import main; raise SystemExit(main(['serve']))"
    process = subprocess.Popen(  # noqa: S603 - fixed interpreter and arguments
        [sys.executable, "-c", script], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    deadline = time.monotonic() + 20
    try:
        assert process.stdout is not None
        while time.monotonic() < deadline and process.poll() is None:
            ready, _, _ = select.select([process.stdout], [], [], 0.2)
            if ready and "service_started" in process.stdout.readline():
                return "started", ""
        _, err = process.communicate(timeout=10)
        return "refused", err
    finally:
        if process.poll() is None:
            process.terminate()
            process.communicate(timeout=10)


KEYS = json.dumps({"1": SCALAR})


@pytest.mark.integration
@pytest.mark.timeout(120)
@pytest.mark.parametrize("cli_module", ["registry", "key_holder"])
@pytest.mark.parametrize("host", ["127.0.0.1", "0.0.0.0", "8.8.8.8", "::", "::1"])  # noqa: S104 - decision 73.6
def test_the_real_probe_on_each_bind_host(cli_module: str, host: str) -> None:
    base = {k: v for k, v in os.environ.items() if not k.startswith(ENV_PREFIXES)}
    with socket.socket() as spare:
        spare.bind(("127.0.0.1", 0))
        port = spare.getsockname()[1]
    settings = registry_env(port) if cli_module == "registry" else {**key_holder_env(KEYS), "PORT": str(port)}
    outcome, err = serve_outcome(cli_module, {**base, **settings, "BIND_HOST": host})
    if host == "8.8.8.8":
        assert (outcome, err) == ("refused", NOT_LOCAL_SENTENCE + "\n")
    elif host in ("::", "::1"):
        assert outcome == "started" or err == NO_IPV6_SENTENCE + "\n", err
    else:
        assert outcome == "started", err
