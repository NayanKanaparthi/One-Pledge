"""Use case: generate a data set from a seed and a profile (SPEC 5.1)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from onepledge_datagen import GENERATOR_VERSION
from onepledge_datagen.application.answer_key import AnswerKeyBuilder
from onepledge_datagen.application.example_contract import expected_document
from onepledge_datagen.application.factories import LoanFactory
from onepledge_datagen.application.invariants import CheckReport, InvariantChecker
from onepledge_datagen.application.kit import PlantingKit
from onepledge_datagen.application.layout import TimelineLayout
from onepledge_datagen.application.messiness import MessinessApplier
from onepledge_datagen.application.model import (
    CHECKSUMS,
    EXAMPLE_EXPECTED,
    EXAMPLE_RECORDS,
    EXAMPLE_TAPE,
    DataSetModel,
    TapeFile,
)
from onepledge_datagen.application.portfolio import PortfolioBuilder
from onepledge_datagen.application.ports import DataSetCodec, DataSetDirectory
from onepledge_datagen.application.profiles import ProfilePlan, plan_for
from onepledge_datagen.application.records import OutsideRecordsBuilder
from onepledge_datagen.application.reference import ReferenceBuilder
from onepledge_datagen.application.workspace import Workspace
from onepledge_datagen.domain import catalog
from onepledge_datagen.domain.entities import Profile
from onepledge_datagen.domain.errors import InvariantsViolatedError
from onepledge_datagen.domain.randomness import SeededStreams


@dataclass(frozen=True)
class GenerationSummary:
    """What was written, for the command line."""

    profile: str
    seed: int
    files: int
    steps: tuple[tuple[str, str, str, str, str, int], ...]
    cases: int
    messiness: int
    titles: int
    payments: int
    contracts: int
    rows_checked: int


def checksums_document(files: dict[str, bytes]) -> dict[str, object]:
    """SHA-256 of every other output file, sorted by path."""
    return {
        "algorithm": "sha256",
        "files": {path: hashlib.sha256(files[path]).hexdigest() for path in sorted(files)},
    }


class GenerateDataSet:
    """Builds, checks and writes one data set."""

    def __init__(self, codec: DataSetCodec, directory: DataSetDirectory, checker: InvariantChecker) -> None:
        self._codec = codec
        self._directory = directory
        self._checker = checker

    def run(self, seed: int, profile: Profile, target: Path, force: bool) -> GenerationSummary:
        """Generate, check every invariant on the rendered bytes, then write."""
        plan = plan_for(profile)
        model = self._build(seed, plan)
        files = self._codec.render(model)
        view = self._codec.parse(files)
        report = self._checker.check(view)
        if report.violations:
            raise InvariantsViolatedError(report.violations)
        if plan.writes_contract_example:
            files.update(self._contract_example(files, model, report))
        files[CHECKSUMS] = self._codec.render_json(checksums_document(files))
        self._directory.write(files, target, force)
        return GenerationSummary(
            profile=profile.value,
            seed=seed,
            files=len(files),
            steps=tuple(
                (s.step_id, s.bank_code, s.facility_code, s.kind, s.as_of.isoformat(), s.data_rows)
                for s in view.steps
            ),
            cases=len({row.case_id for row in view.case_rows}),
            messiness=len(view.messiness),
            titles=len(view.titles),
            payments=len(view.payments),
            contracts=len(view.contracts),
            rows_checked=report.rows_checked,
        )

    def _contract_example(
        self, files: dict[str, bytes], model: DataSetModel, report: CheckReport
    ) -> dict[str, bytes]:
        view = self._codec.parse(files)
        example = {EXAMPLE_TAPE: files[model.tapes[0].path]}
        example.update({target: files[source] for source, target in EXAMPLE_RECORDS.items()})
        example[EXAMPLE_EXPECTED] = self._codec.render_json(expected_document(view, report.observations))
        return example

    @staticmethod
    def _build(seed: int, plan: ProfilePlan) -> DataSetModel:
        streams = SeededStreams(seed)
        workspace = Workspace(streams, plan.steps, catalog.TIMELINE_END)
        kit = PlantingKit(workspace, streams, LoanFactory(streams))
        PortfolioBuilder(plan.baseline).build(kit)
        for planter in plan.planters:
            planter.plant(kit)
        messiness = MessinessApplier(plan.messiness).apply(kit)
        layout = TimelineLayout(plan.totals_rows)
        layout.lay_out(workspace, messiness)
        titles, payments, contracts = OutsideRecordsBuilder(
            streams.stream("records"), catalog.TIMELINE_END
        ).build(workspace.plans)
        facility_codes = tuple(dict.fromkeys(step.facility_code for step in plan.steps))
        tapes = tuple(
            TapeFile(
                path=workspace.step(step_id).relative_path(),
                lender_format=workspace.lender_format(step_id),
                rows=tuple(tuple(row.cells) for row in workspace.rows[step_id]),
                trailing_blank_line=plan.totals_rows and workspace.lender_format(step_id).totals_row,
            )
            for step_id in workspace.step_order
        )
        manifest: dict[str, object] = {
            "generator_version": GENERATOR_VERSION,
            "seed": seed,
            "profile": plan.profile.value,
            "timeline_end": catalog.TIMELINE_END.isoformat(),
            "steps": TimelineLayout.manifest_steps(workspace),
        }
        return DataSetModel(
            manifest=manifest,
            reference=ReferenceBuilder(streams).build(facility_codes),
            answer_key=AnswerKeyBuilder().build(workspace, seed, plan.profile.value),
            tapes=tapes,
            titles=tuple(titles),
            payments=tuple(payments),
            contracts=tuple(contracts),
        )
