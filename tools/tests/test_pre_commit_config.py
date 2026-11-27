"""The pre-commit hooks of the repository work as written (SPEC 2.2, SPEC_DECISIONS 135).

pre-commit's `pygrep` language reads a hook's `entry` as a Python regular expression and compiles
it as bytes (`re.compile(pattern.encode(), flags)`), then searches the bytes of each file. A
pattern that only a `str` regular expression accepts, such as `\\u2014`, is a bad escape there and
the hook fails on every file. These tests load the configuration with pre-commit's own loader and
compile and run every `pygrep` entry with pre-commit's own `pygrep` program, so the result is
the one `pre-commit run` gives.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import cast

import pytest
from pre_commit.clientlib import load_config
from pre_commit.languages import pygrep

from onepledge_testtools.processes import PYTHON, run

REPOSITORY = Path(__file__).resolve().parents[2]
CONFIG = REPOSITORY / ".pre-commit-config.yaml"
EM_DASH = "\u2014"
EN_DASH = "\u2013"
# The flags pygrep's own argument parser accepts; anything else in `args` would be a file name.
_PYGREP_FLAGS = {"-i": re.IGNORECASE, "--ignore-case": re.IGNORECASE, "--multiline": re.MULTILINE}


def _hooks() -> list[dict[str, object]]:
    config = cast(dict[str, object], load_config(str(CONFIG)))
    repos = cast(list[dict[str, object]], config["repos"])
    return [hook for repo in repos for hook in cast(list[dict[str, object]], repo["hooks"])]


def _pygrep_hooks() -> list[dict[str, object]]:
    return [hook for hook in _hooks() if hook["language"] == "pygrep"]


def _compiled(hook: dict[str, object]) -> re.Pattern[bytes]:
    """The hook's pattern compiled exactly as pygrep compiles it: the entry encoded, as bytes."""
    flags = 0
    for argument in cast(list[str], hook.get("args", [])):
        flags |= _PYGREP_FLAGS[argument]
    return re.compile(cast(str, hook["entry"]).encode(), flags)


def _hook(hook_id: str) -> dict[str, object]:
    (found,) = [hook for hook in _hooks() if hook["id"] == hook_id]
    return found


@pytest.mark.unit
def test_the_configuration_has_pygrep_hooks_to_check() -> None:
    assert {hook["id"] for hook in _pygrep_hooks()} >= {"no-em-dash", "codeowners-without-handles"}


@pytest.mark.unit
@pytest.mark.parametrize("hook_id", [str(hook["id"]) for hook in _pygrep_hooks()])
def test_every_pygrep_entry_compiles_as_bytes_the_way_pygrep_compiles_it(hook_id: str) -> None:
    hook = _hook(hook_id)
    _compiled(hook)
    # pygrep's own program, with no file to read: it parses the arguments and compiles the pattern.
    arguments = cast(list[str], hook.get("args", []))
    assert pygrep.main([*arguments, cast(str, hook["entry"])]) == 0


@pytest.mark.unit
def test_the_em_dash_hook_matches_a_line_with_u2014_and_no_other_line() -> None:
    pattern = _compiled(_hook("no-em-dash"))
    assert pattern.search(f"a sentence {EM_DASH} with an em dash\n".encode())
    assert pattern.search(f"{EM_DASH}".encode())
    others = ("a sentence - with a hyphen\n", f"a range 1{EN_DASH}2\n", "plain ASCII\n", "\u20ac 12\n")
    for line in others:
        assert not pattern.search(line.encode()), line


@pytest.mark.integration
def test_pygrep_fails_a_file_with_an_em_dash_and_passes_one_without(tmp_path: Path) -> None:
    """pre-commit runs a pygrep hook as `python -m pre_commit.languages.pygrep ENTRY FILE...`."""
    entry = cast(str, _hook("no-em-dash")["entry"])
    with_dash, without = tmp_path / "with.md", tmp_path / "without.md"
    with_dash.write_text(f"first line\nsecond {EM_DASH} line\n", encoding="utf-8")
    without.write_text(f"first line\nsecond - line, 1{EN_DASH}2, \u20ac 3\n", encoding="utf-8")
    passed = run([PYTHON, "-m", "pre_commit.languages.pygrep", entry, str(without)])
    assert (passed.returncode, passed.stdout, passed.stderr) == (0, "", "")
    failed = run([PYTHON, "-m", "pre_commit.languages.pygrep", entry, str(with_dash)])
    assert (failed.returncode, failed.stderr) == (1, "")
    assert failed.stdout == f"{with_dash}:2:second {EM_DASH} line\n"
