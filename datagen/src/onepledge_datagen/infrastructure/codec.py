"""The data set codec: renders the model to file bytes and parses file bytes into the typed view."""

from __future__ import annotations

import json
from collections.abc import Mapping

from onepledge_datagen.application.model import (
    ANSWER_KEY,
    CONTRACTS,
    MANIFEST,
    PAYMENTS,
    REFERENCE,
    TITLES,
    DataSetModel,
)
from onepledge_datagen.application.view import (
    DataSetView,
    parse_answer_key,
    parse_facilities,
    parse_inherent_fixes,
    parse_steps,
)
from onepledge_datagen.domain.bounded_json import parse_bounded_json
from onepledge_datagen.domain.errors import DataSetUnreadableError
from onepledge_datagen.infrastructure.csv_files import (
    parse_contracts,
    parse_payments,
    parse_titles,
    render_contracts,
    render_payments,
    render_tape,
    render_titles,
)


class FileCodec:
    """JSON with two space indentation and a final newline; CSV as described in `csv_files`."""

    def render_json(self, document: object) -> bytes:
        """Deterministic JSON bytes (key order as built)."""
        return (json.dumps(document, indent=2, ensure_ascii=False) + "\n").encode("utf-8")

    def render(self, model: DataSetModel) -> dict[str, bytes]:
        """Every file of the model."""
        files = {tape.path: render_tape(tape) for tape in model.tapes}
        files[MANIFEST] = self.render_json(model.manifest)
        files[REFERENCE] = self.render_json(model.reference)
        files[ANSWER_KEY] = self.render_json(model.answer_key)
        files[TITLES] = render_titles(model.titles)
        files[PAYMENTS] = render_payments(model.payments)
        files[CONTRACTS] = render_contracts(model.contracts)
        return files

    def parse(self, files: Mapping[str, bytes]) -> DataSetView:
        """The typed view; a missing or malformed file raises DataSetUnreadableError."""
        manifest = self._json(files, MANIFEST)
        reference = self._json(files, REFERENCE)
        answer_key = self._json(files, ANSWER_KEY)
        case_rows, messiness = parse_answer_key(answer_key)
        steps = parse_steps(manifest)
        scanned = {path: files[path].decode("utf-8", errors="replace") for path in self._scanned_paths(files)}
        if not isinstance(manifest, dict) or not isinstance(manifest.get("seed"), int):
            raise DataSetUnreadableError("manifest.json has no integer seed.")
        return DataSetView(
            profile=str(manifest.get("profile")),
            seed=manifest["seed"],
            steps=steps,
            facilities=parse_facilities(reference),
            case_rows=case_rows,
            messiness=messiness,
            inherent_fixes=parse_inherent_fixes(answer_key),
            tapes={step.file: self._file(files, step.file) for step in steps},
            titles=tuple(parse_titles(self._file(files, TITLES))),
            payments=tuple(parse_payments(self._file(files, PAYMENTS))),
            contracts=tuple(parse_contracts(self._file(files, CONTRACTS))),
            label_scanned_text=scanned,
        )

    @staticmethod
    def _scanned_paths(files: Mapping[str, bytes]) -> list[str]:
        prefixes = ("tapes/", "outside_records/")
        return sorted(p for p in files if p.startswith(prefixes) or p in (MANIFEST, REFERENCE))

    @staticmethod
    def _file(files: Mapping[str, bytes], path: str) -> bytes:
        if path not in files:
            raise DataSetUnreadableError(f"{path} is missing.")
        return files[path]

    def _json(self, files: Mapping[str, bytes], path: str) -> object:
        return parse_bounded_json(self._file(files, path), path)
