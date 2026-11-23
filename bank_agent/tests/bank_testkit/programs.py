"""Installed console programs run as child processes, with fixed arguments and an explicit environment."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Mapping, Sequence

import pytest

from onepledge_testtools import processes

PASSED_THROUGH = ("PATH", "HOME", "LANG", "PYTHONPATH", "VIRTUAL_ENV")
PROGRAM_TIMEOUT_SECONDS = 120


def run_program(
    name: str, arguments: Sequence[str], environment: Mapping[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """Run an installed program; without `environment` it inherits the test process environment."""
    command = shutil.which(name)
    if command is None:
        pytest.fail(f"{name} is not installed; run make setup.")
    if environment is None:
        variables = dict(os.environ)
    else:
        variables = {key: os.environ[key] for key in PASSED_THROUGH if key in os.environ}
        variables.update(environment)
    return processes.run([command, *arguments], variables, timeout=PROGRAM_TIMEOUT_SECONDS)


WIDTH = 100
"""The width of human readable command line output (SPEC_DECISIONS 78)."""
CONTINUATION = "  "


def unwrap(text: str) -> str:
    """Human readable text with each wrapped line joined back to the line it continues.

    A line longer than 100 columns fails the test; a continuation line starts with two spaces, so this
    is for sentences and single lines, not for tables, whose rows are indented themselves.
    """
    joined: list[str] = []
    for line in text.splitlines():
        assert len(line) <= WIDTH, f"over {WIDTH} columns: {line!r}"
        if line.startswith(CONTINUATION) and joined:
            joined[-1] += " " + line.strip()
        else:
            joined.append(line)
    return "".join(part + "\n" for part in joined)


def split_stderr(stderr: str) -> tuple[list[dict[str, object]], list[str]]:
    """stderr split into JSON log records (each must parse as an object) and the sentences between
    them, each wrapped sentence joined back into one (every human readable line at most 100 columns)."""
    records: list[dict[str, object]] = []
    human: list[str] = []
    for line in stderr.splitlines():
        if line.startswith("{"):
            record = json.loads(line)
            assert isinstance(record, dict)
            records.append(record)
        else:
            human.append(line)
    return records, unwrap("\n".join(human)).splitlines()
