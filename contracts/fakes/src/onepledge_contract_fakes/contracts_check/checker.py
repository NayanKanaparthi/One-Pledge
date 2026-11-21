"""The four checks of `onepledge-contracts check` (SPEC 7.4)."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openapi_spec_validator import OpenAPIV31SpecValidator

from onepledge_contract_fakes.contracts_check.documents import DocumentError, load_json, load_yaml_mapping
from onepledge_contract_fakes.contracts_check.schemas import ContractSchemas
from onepledge_contract_fakes.contracts_check.vectors import (
    check_key_holder_examples,
    check_signatures,
    check_tokenization,
)

CONTRACT_FILES: tuple[str, ...] = (
    "bank_agent.yaml",
    "key_holder.yaml",
    "registry.yaml",
    "outside_records.yaml",
)
EXAMPLE_FOLDERS: tuple[str, ...] = ("bank_agent", "key_holder", "registry", "outside_records")
OPENAPI_VERSION = "3.1.0"


@dataclass
class CheckOutcome:
    """The result of one check: a short name, a summary and the problems found."""

    name: str
    summary: str
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Whether the check found nothing wrong."""
        return not self.problems


@dataclass(frozen=True)
class CheckReport:
    """Every check outcome and the counts for the closing line."""

    outcomes: tuple[CheckOutcome, ...]
    contract_count: int
    example_count: int

    @property
    def ok(self) -> bool:
        """Whether every check passed."""
        return all(outcome.ok for outcome in self.outcomes)


def _external_values(node: object) -> Iterator[str]:
    if isinstance(node, Mapping):
        for key, value in node.items():
            if key == "externalValue" and isinstance(value, str):
                yield value
            else:
                yield from _external_values(value)
    elif isinstance(node, list):
        for item in node:
            yield from _external_values(item)


class ContractsChecker:
    """Runs every check over one contracts directory."""

    def __init__(self, contracts_dir: Path) -> None:
        self._dir = contracts_dir
        self._examples = contracts_dir / "examples"

    def run(self) -> CheckReport:
        """Run the checks in order and collect their outcomes."""
        specs, outcomes = self._load_and_validate_specs()
        index = self._load_index(outcomes)
        outcomes.append(self._check_examples(specs, index))
        outcomes.append(self._check_references(specs, index))
        outcomes.extend(self._check_vectors())
        return CheckReport(tuple(outcomes), len(CONTRACT_FILES), len(index))

    def _load_and_validate_specs(self) -> tuple[dict[str, Mapping[str, Any]], list[CheckOutcome]]:
        specs: dict[str, Mapping[str, Any]] = {}
        outcomes: list[CheckOutcome] = []
        for name in CONTRACT_FILES:
            outcome = CheckOutcome(f"openapi {name}", "valid OpenAPI 3.1")
            try:
                spec = load_yaml_mapping(self._dir / name)
            except DocumentError as error:
                outcome.problems.append(f"{name} {error.problem}")
                outcomes.append(outcome)
                continue
            if spec.get("openapi") != OPENAPI_VERSION:
                outcome.problems.append(f"{name} does not declare openapi: {OPENAPI_VERSION}")
            outcome.problems.extend(
                f"{name}: {issue.message}" for issue in OpenAPIV31SpecValidator(spec).iter_errors()
            )
            specs[name] = spec
            outcomes.append(outcome)
        return specs, outcomes

    def _load_index(self, outcomes: list[CheckOutcome]) -> list[Mapping[str, Any]]:
        try:
            document = load_json(self._examples / "index.json")
        except DocumentError as error:
            outcomes.append(
                CheckOutcome("index", "examples/index.json", [f"examples/index.json {error.problem}"])
            )
            return []
        entries = document.get("examples") if isinstance(document, dict) else None
        if not isinstance(entries, list):
            outcomes.append(
                CheckOutcome("index", "examples/index.json", ["examples/index.json has no examples list"])
            )
            return []
        return [entry for entry in entries if isinstance(entry, dict)]

    def _check_examples(
        self, specs: Mapping[str, Mapping[str, Any]], index: list[Mapping[str, Any]]
    ) -> CheckOutcome:
        outcome = CheckOutcome("examples", f"{len(index)} examples validate against their schemas")
        validators = {name: ContractSchemas(spec, name) for name, spec in specs.items()}
        for entry in index:
            file_name, contract, schema = entry.get("file"), entry.get("contract"), entry.get("schema")
            validator = validators.get(str(contract))
            if validator is None or not isinstance(schema, str) or not validator.has_component(schema):
                outcome.problems.append(f"examples/{file_name}: unknown contract or schema in the index")
                continue
            try:
                instance = load_json(self._examples / str(file_name))
            except DocumentError as error:
                outcome.problems.append(f"examples/{file_name} {error.problem}")
                continue
            outcome.problems.extend(
                f"examples/{file_name}: {problem}" for problem in validator.component_errors(schema, instance)
            )
        return outcome

    def _check_references(
        self, specs: Mapping[str, Mapping[str, Any]], index: list[Mapping[str, Any]]
    ) -> CheckOutcome:
        references = [(name, ref) for name, spec in specs.items() for ref in _external_values(spec)]
        outcome = CheckOutcome(
            "references", f"{len(references)} externalValue links resolve, every example is indexed"
        )
        for name, reference in references:
            if not (self._dir / reference).is_file():
                outcome.problems.append(f"{name}: externalValue {reference} does not resolve to a file")
        indexed = {str(entry.get("file")) for entry in index}
        for folder in EXAMPLE_FOLDERS:
            for path in sorted((self._examples / folder).glob("*")):
                relative = f"{folder}/{path.name}"
                if path.is_file() and relative not in indexed:
                    outcome.problems.append(f"examples/{relative} is not listed in examples/index.json")
        for entry in index:
            if not (self._examples / str(entry.get("file"))).is_file():
                outcome.problems.append(
                    f"examples/index.json lists examples/{entry.get('file')}, which does not exist"
                )
        return outcome

    def _check_vectors(self) -> list[CheckOutcome]:
        tokenization = CheckOutcome("vectors tokenization.json", "every OPRF value and token recomputes")
        signatures = CheckOutcome("vectors signatures.json", "every hash and signature recomputes")
        try:
            vector = load_json(self._examples / "vectors" / "tokenization.json")
            if not isinstance(vector, dict):
                raise DocumentError(self._examples / "vectors" / "tokenization.json", "is not a JSON object")
            tokenization.problems.extend(check_tokenization(vector))
            tokenization.problems.extend(check_key_holder_examples(vector, self._examples))
        except (DocumentError, KeyError, TypeError, ValueError) as error:
            tokenization.problems.append(
                f"vectors/tokenization.json cannot be checked ({type(error).__name__})"
            )
        try:
            vector = load_json(self._examples / "vectors" / "signatures.json")
            if not isinstance(vector, dict):
                raise DocumentError(self._examples / "vectors" / "signatures.json", "is not a JSON object")
            signatures.problems.extend(check_signatures(vector, self._examples))
        except (DocumentError, KeyError, TypeError, ValueError) as error:
            signatures.problems.append(f"vectors/signatures.json cannot be checked ({type(error).__name__})")
        return [tokenization, signatures]
