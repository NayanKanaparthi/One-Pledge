"""The four checks of `onepledge-contracts check` (SPEC 7.4).

Part of the contract checker in the TEST DOUBLES package `onepledge_contract_fakes`.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openapi_spec_validator import OpenAPIV31SpecValidator

from onepledge_contract_fakes.cli_output import plural
from onepledge_contract_fakes.contracts_check.cleaning_cases import check_cleaning_cases
from onepledge_contract_fakes.contracts_check.documents import (
    DocumentError,
    confined,
    load_json,
    load_yaml_mapping,
)
from onepledge_contract_fakes.contracts_check.schemas import ContractSchemas, plain_problem
from onepledge_contract_fakes.contracts_check.vectors import (
    check_key_holder_examples,
    check_signatures,
    check_tokenization,
    vector_dependencies,
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
    skipped_because: str | None = None

    @property
    def ok(self) -> bool:
        """Whether the check found nothing wrong (a skipped check is not counted as a problem)."""
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


INDEX = "examples/index.json"


class ContractsChecker:
    """Runs every check over one contracts directory."""

    def __init__(self, contracts_dir: Path) -> None:
        self._dir = contracts_dir
        self._examples = contracts_dir / "examples"

    def run(self) -> CheckReport:
        """Run the checks in order and collect their outcomes."""
        specs, outcomes = self._load_and_validate_specs()
        index = self._load_index(outcomes)
        examples, invalid = self._check_examples(specs, index)
        outcomes.append(examples)
        outcomes.append(self._check_references(specs, index))
        outcomes.extend(self._check_vectors(invalid))
        outcomes.append(self._check_cleaning_cases())
        return CheckReport(tuple(outcomes), len(CONTRACT_FILES), len(index))

    def _load_and_validate_specs(self) -> tuple[dict[str, Mapping[str, Any]], list[CheckOutcome]]:
        specs: dict[str, Mapping[str, Any]] = {}
        outcomes: list[CheckOutcome] = []
        for name in CONTRACT_FILES:
            outcome = CheckOutcome(f"openapi {name}", "valid OpenAPI 3.1")
            try:
                spec = load_yaml_mapping(self._dir / name, name)
            except DocumentError as error:
                outcome.problems.append(error.message)
                outcomes.append(outcome)
                continue
            if spec.get("openapi") != OPENAPI_VERSION:
                outcome.problems.append(f"{name} does not declare openapi: {OPENAPI_VERSION}")
            outcome.problems.extend(
                f"{name}: {plain_problem(issue)}" for issue in OpenAPIV31SpecValidator(spec).iter_errors()
            )
            specs[name] = spec
            outcomes.append(outcome)
        return specs, outcomes

    def _load_index(self, outcomes: list[CheckOutcome]) -> list[Mapping[str, Any]]:
        try:
            document = load_json(self._examples / "index.json", INDEX)
        except DocumentError as error:
            outcomes.append(CheckOutcome("index", INDEX, [error.message]))
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
    ) -> tuple[CheckOutcome, set[str]]:
        """The examples outcome and the example files found invalid (vectors that read them are skipped)."""
        outcome = CheckOutcome("examples", f"{plural(len(index), 'example')} validate against their schemas")
        invalid: set[str] = set()
        validators = {name: ContractSchemas(spec, name) for name, spec in specs.items()}
        for entry in index:
            file_name, contract, schema = entry.get("file"), entry.get("contract"), entry.get("schema")
            validator = validators.get(str(contract))
            if validator is None or not isinstance(schema, str) or not validator.has_component(schema):
                outcome.problems.append(f"examples/{file_name}: unknown contract or schema in the index")
                invalid.add(str(file_name))
                continue
            try:
                path = confined(self._examples, file_name, INDEX)
                instance = load_json(path, f"examples/{file_name}")
            except DocumentError as error:
                outcome.problems.append(error.message)
                invalid.add(str(file_name))
                continue
            problems = validator.component_errors(schema, instance)
            if problems:
                invalid.add(str(file_name))
            outcome.problems.extend(f"examples/{file_name}: {problem}" for problem in problems)
        return outcome, invalid

    def _check_references(
        self, specs: Mapping[str, Mapping[str, Any]], index: list[Mapping[str, Any]]
    ) -> CheckOutcome:
        references = [(name, ref) for name, spec in specs.items() for ref in _external_values(spec)]
        outcome = CheckOutcome(
            "references", f"{plural(len(references), 'externalValue link')} resolve, every example is indexed"
        )
        for name, reference in references:
            try:
                target = confined(self._dir, reference, name)
            except DocumentError as error:
                outcome.problems.append(error.message)
                continue
            if not target.is_file():
                outcome.problems.append(f"{name}: externalValue {reference} does not resolve to a file")
        indexed = {str(entry.get("file")) for entry in index}
        for folder in EXAMPLE_FOLDERS:
            for path in sorted((self._examples / folder).glob("*")):
                relative = f"{folder}/{path.name}"
                if path.is_file() and relative not in indexed:
                    outcome.problems.append(f"examples/{relative} is not listed in examples/index.json")
        for entry in index:
            try:
                listed = confined(self._examples, entry.get("file"), INDEX)
            except DocumentError:
                continue  # Reported once, by the examples check.
            if not listed.is_file():
                outcome.problems.append(
                    f"examples/index.json lists examples/{entry.get('file')}, which does not exist"
                )
        return outcome

    def _check_cleaning_cases(self) -> CheckOutcome:
        """The neutral cleaning table exists and every case has the documented shape."""
        outcome = CheckOutcome("cleaning cases", "cleaning_cases.json")
        try:
            document = load_json(self._examples / "cleaning_cases.json", "examples/cleaning_cases.json")
        except DocumentError as error:
            outcome.problems.append(error.message)
            return outcome
        outcome.problems.extend(check_cleaning_cases(document))
        cases = document.get("cases") if isinstance(document, dict) else None
        count = len(cases) if isinstance(cases, list) else 0
        outcome.summary = f"{plural(count, 'case')} have the documented shape"
        return outcome

    def _check_vectors(self, invalid_examples: set[str]) -> list[CheckOutcome]:
        tokenization = self._check_vector(
            "tokenization.json", "every OPRF value and token recomputes", invalid_examples
        )
        signatures = self._check_vector(
            "signatures.json", "every hash and signature recomputes", invalid_examples
        )
        return [tokenization, signatures]

    def _check_vector(self, name: str, summary: str, invalid_examples: set[str]) -> CheckOutcome:
        """One vector file; skipped with one line when an example it reads was already reported invalid."""
        outcome = CheckOutcome(f"vectors {name}", summary)
        shown = f"examples/vectors/{name}"
        try:
            vector = load_json(self._examples / "vectors" / name, shown)
            if not isinstance(vector, dict):
                raise DocumentError(shown, "not a JSON object")
            broken = sorted(set(vector_dependencies(name, vector)) & invalid_examples)
            if broken:
                outcome.skipped_because = (
                    f"vectors/{name}: not checked, depends on {broken[0]} which is invalid"
                )
                return outcome
            if name == "tokenization.json":
                outcome.problems.extend(check_tokenization(vector))
                outcome.problems.extend(check_key_holder_examples(vector, self._examples))
            else:
                outcome.problems.extend(check_signatures(vector, self._examples))
        except DocumentError as error:
            outcome.problems.append(error.message)
        except KeyError:
            outcome.problems.append(f"vectors/{name} lacks a field the check needs")
        except (TypeError, ValueError):
            outcome.problems.append(f"vectors/{name} holds a value of the wrong type or form")
        return outcome
