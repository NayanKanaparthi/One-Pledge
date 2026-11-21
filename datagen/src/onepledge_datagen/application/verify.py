"""Use case: re-check every invariant of an existing data directory (SPEC 5.1 `verify`)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from onepledge_datagen.application.example_contract import expected_document
from onepledge_datagen.application.invariants import InvariantChecker
from onepledge_datagen.application.model import CHECKSUMS, EXAMPLE_EXPECTED, EXAMPLE_RECORDS, EXAMPLE_TAPE
from onepledge_datagen.application.ports import DataSetCodec, DataSetDirectory
from onepledge_datagen.application.view import DataSetView
from onepledge_datagen.domain.oracle.model import RowObservation


@dataclass(frozen=True)
class VerificationReport:
    """The outcome of `verify`."""

    violations: tuple[str, ...]
    files: int
    rows_checked: int

    @property
    def ok(self) -> bool:
        """Whether nothing was violated."""
        return not self.violations


def checksum_violations(files: dict[str, bytes]) -> list[str]:
    """Every file must be listed with its SHA-256, and nothing else may be present."""
    if CHECKSUMS not in files:
        return [f"{CHECKSUMS} is missing"]
    try:
        listed = json.loads(files[CHECKSUMS])["files"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return [f"{CHECKSUMS} cannot be read"]
    violations = []
    others = {path for path in files if path != CHECKSUMS}
    for path in sorted(set(listed) | others):
        if path not in files:
            violations.append(f"{path} is listed in {CHECKSUMS} but missing")
        elif path not in listed:
            violations.append(f"{path} is present but not listed in {CHECKSUMS}")
        elif hashlib.sha256(files[path]).hexdigest() != listed[path]:
            violations.append(f"{path} does not match its checksum")
    return violations


class VerifyDataSet:
    """Reads a data directory and re-checks checksums and every invariant."""

    def __init__(self, codec: DataSetCodec, directory: DataSetDirectory, checker: InvariantChecker) -> None:
        self._codec = codec
        self._directory = directory
        self._checker = checker

    def run(self, source: Path) -> VerificationReport:
        """All violations found; an unreadable directory raises DataSetUnreadableError."""
        files = self._directory.read(source)
        violations = checksum_violations(files)
        view = self._codec.parse(files)
        report = self._checker.check(view)
        violations.extend(report.violations)
        if EXAMPLE_EXPECTED in files:
            violations.extend(self._example_violations(files, view, report.observations))
        return VerificationReport(tuple(violations), len(files), report.rows_checked)

    def _example_violations(
        self, files: dict[str, bytes], view: DataSetView, observations: dict[tuple[str, int], RowObservation]
    ) -> list[str]:
        violations = []
        tape = files.get(view.steps[0].file)
        if files.get(EXAMPLE_TAPE) != tape:
            violations.append(f"{EXAMPLE_TAPE} differs from the generated tape")
        for source, target in EXAMPLE_RECORDS.items():
            if files.get(target) != files.get(source):
                violations.append(f"{target} differs from {source}")
        expected = self._codec.render_json(expected_document(view, observations))
        if files.get(EXAMPLE_EXPECTED) != expected:
            violations.append(f"{EXAMPLE_EXPECTED} differs from the oracle's results")
        return violations
