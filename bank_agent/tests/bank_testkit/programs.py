"""Installed console programs run as child processes, with fixed arguments and an explicit environment."""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Mapping, Sequence

import pytest

PASSED_THROUGH = ("PATH", "HOME", "LANG", "PYTHONPATH", "VIRTUAL_ENV")


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
    return subprocess.run(  # noqa: S603 - an installed program of this repository, arguments from the test
        [command, *arguments], capture_output=True, text=True, env=variables, check=False, timeout=120
    )
