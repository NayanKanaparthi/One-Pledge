"""Child processes in tests: one way to start them (SPEC_DECISIONS 119).

Every test that runs a program does it through `start` or `run`: an argument list, never a
shell, with the test interpreter (`PYTHON`) or an installed program of this repository as the
first argument. This module holds the one `S603` suppression of the test code.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Mapping, Sequence

PYTHON = sys.executable
DEFAULT_TIMEOUT_SECONDS = 60


def start(
    arguments: Sequence[str], env: Mapping[str, str] | None = None, merge_stderr: bool = False
) -> subprocess.Popen[str]:
    """Start `arguments` with stdout and stderr piped as text (stderr into stdout when merged)."""
    stderr = subprocess.STDOUT if merge_stderr else subprocess.PIPE
    return subprocess.Popen(  # noqa: S603 - an argument list from the test itself, never a shell
        list(arguments),
        env=None if env is None else dict(env),
        stdout=subprocess.PIPE,
        stderr=stderr,
        text=True,
    )


def run(
    arguments: Sequence[str], env: Mapping[str, str] | None = None, timeout: float = DEFAULT_TIMEOUT_SECONDS
) -> subprocess.CompletedProcess[str]:
    """Run `arguments` to the end and return the exit code, stdout and stderr; killed after `timeout`."""
    process = start(arguments, env)
    try:
        out, err = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()
        raise
    return subprocess.CompletedProcess(process.args, process.returncode, out, err)


def stop(process: subprocess.Popen[str], timeout: float = 10) -> tuple[str, str]:
    """Stop a process this test started, by its own handle, and return what it printed."""
    if process.poll() is None:
        process.terminate()
    try:
        out, err = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        out, err = process.communicate()
    return out or "", err or ""
