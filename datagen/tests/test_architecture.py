"""Import boundaries of the generator (SPEC 2.1, 21.6)."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

from onepledge_datagen.domain.errors import DataGenError
from onepledge_datagen.domain.oracle.cleaning import TapeFormatError
from onepledge_testtools.declarations import (
    undeclared_source_imports,
    undeclared_test_requirements,
)
from onepledge_testtools.repository_rules import markdown_files, markdown_problems, source_tree_problems

pytestmark = pytest.mark.unit

SOURCE = Path(__file__).resolve().parents[1] / "src" / "onepledge_datagen"
REPOSITORY = SOURCE.parents[2]
PACKAGE_DIR = SOURCE.parents[1]
OWNED_MARKDOWN = markdown_files(REPOSITORY / "datagen")
OTHER_PACKAGES = (
    "onepledge_bank_agent",
    "onepledge_contract_fakes",
    "onepledge_outside_records",
    "onepledge_evaluation",
)
LAYERS = ("domain", "application", "infrastructure", "interface")
ALLOWED_INTERNAL = {
    "domain": {"domain"},
    "application": {"domain", "application"},
    "infrastructure": {"domain", "application", "infrastructure"},
    "interface": {"domain", "application", "infrastructure", "interface"},
}


def imports(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_the_generator_imports_no_other_onepledge_package_and_no_third_party_code() -> None:
    for path in SOURCE.rglob("*.py"):
        for name in imports(path):
            top = name.split(".")[0]
            assert top not in OTHER_PACKAGES, f"{path.name} imports {name}"
            assert top in sys.stdlib_module_names or top in ("onepledge_datagen", "__future__"), (
                f"{path.name}: {name}"
            )


def test_layers_only_import_inward() -> None:
    for layer in LAYERS:
        for path in (SOURCE / layer).rglob("*.py"):
            for name in imports(path):
                parts = name.split(".")
                if parts[0] == "onepledge_datagen" and len(parts) > 1 and parts[1] in LAYERS:
                    assert parts[1] in ALLOWED_INTERNAL[layer], f"{layer}/{path.name} imports {name}"


def test_the_generator_reads_no_environment_and_uses_no_clock_or_os_randomness() -> None:
    for path in SOURCE.rglob("*.py"):
        text = path.read_text()
        for forbidden in (
            "os.environ",
            "getenv",
            "datetime.now",
            "time.time",
            "uuid4(",
            "os.urandom",
            "secrets.",
        ):
            assert forbidden not in text, f"{path.name} uses {forbidden}"


def test_no_source_module_looks_like_a_test_module() -> None:
    assert not [p for p in SOURCE.rglob("*.py") if p.name.startswith("test_")]


def test_every_generator_error_derives_from_one_base() -> None:
    assert issubclass(TapeFormatError, DataGenError)
    assert TapeFormatError.code == "TAPE_FORMAT"
    for path in SOURCE.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ClassDef) and node.name.endswith("Error"):
                bases = {ast.unparse(base) for base in node.bases}
                assert (
                    bases & {"DataGenError", "PlanError", "InvariantsViolatedError"}
                    or node.name == "DataGenError"
                ), f"{path.name}:{node.name} derives from {sorted(bases)}"


def test_the_oracle_reads_completely() -> None:
    for path in (SOURCE / "domain" / "oracle").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ClassDef | ast.FunctionDef) and not node.name.startswith("__"):
                assert ast.get_docstring(node), f"{path.name}:{node.name} has no docstring"


def nodes(path: Path) -> list[ast.AST]:
    return list(ast.walk(ast.parse(path.read_text(encoding="utf-8"))))


# SPEC_DECISIONS 37 and 45: one module turns text into integers and dates, by arithmetic over
# the ten ASCII digits. Nothing else calls `int()`, asks Unicode whether a character is a digit,
# writes the Unicode digit class `\d`, or parses an ISO date with the permissive standard parser.
TEXT_VALUES = SOURCE / "domain" / "text_values.py"
UNICODE_DIGIT_PREDICATES = frozenset({"isdigit", "isdecimal", "isnumeric"})


def test_no_text_becomes_a_number_or_date_outside_the_one_module() -> None:
    for path in sorted(SOURCE.rglob("*.py")):
        found = nodes(path)
        name = path.relative_to(SOURCE)
        called = {n.func.id for n in found if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        attributes = {n.attr for n in found if isinstance(n, ast.Attribute)}
        strings = [n.value for n in found if isinstance(n, ast.Constant) and isinstance(n.value, str)]
        assert "int" not in called, f"{name} calls int(); use domain.text_values"
        assert not attributes & UNICODE_DIGIT_PREDICATES, (
            f"{name} asks Unicode whether a character is a digit"
        )
        assert not [s for s in strings if "\\d" in s], f"{name} writes the Unicode digit class in a pattern"
        if path != TEXT_VALUES:
            assert not attributes & {"fromisoformat", "strptime"}, f"{name} parses a date outside text_values"


# SPEC_DECISIONS 50.2: the oracle's cleaning module parses by explicit character level code and
# shares no technique with the bank agent: no regular expression at all.
ORACLE_CLEANING = SOURCE / "domain" / "oracle" / "cleaning.py"


def test_the_oracle_cleaning_module_uses_no_regular_expression() -> None:
    assert "re" not in imports(ORACLE_CLEANING)
    assert not [n for n in nodes(ORACLE_CLEANING) if isinstance(n, ast.Name) and n.id == "re"]
    for forbidden in ("regex", "fnmatch"):
        assert forbidden not in imports(ORACLE_CLEANING)


# SPEC_DECISIONS 45: the oracle trims ASCII whitespace only and changes case of ASCII letters only.
# `str.strip()` without arguments, `upper()`, `lower()`, `split()` without arguments and
# `isspace`/`isalpha`/`isalnum`/`islower`/`isupper` all apply Unicode rules.
UNICODE_TEXT_METHODS = frozenset(
    {"upper", "lower", "casefold", "isspace", "isalpha", "isalnum", "islower", "isupper"}
)


def test_the_oracle_cleaning_module_applies_only_ascii_text_rules() -> None:
    for node in nodes(ORACLE_CLEANING):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            method = node.func.attr
            receiver = ast.unparse(node.func.value)
            if method in UNICODE_TEXT_METHODS:
                assert receiver == "ascii_name", f"{receiver}.{method}() applies Unicode rules"
            if method in ("strip", "lstrip", "rstrip", "split"):
                assert node.args, f"{receiver}.{method}() without arguments applies Unicode whitespace"


def test_no_argument_is_converted_with_int_or_float() -> None:
    """`type=int` would accept `+5`, ` 7` and non-ASCII digits (SPEC_DECISIONS 37)."""
    for path in sorted(SOURCE.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.keyword) and node.arg == "type" and isinstance(node.value, ast.Name):
                assert node.value.id not in ("int", "float"), (
                    f"{path.name} converts an argument with {node.value.id}"
                )


def test_the_source_tree_keeps_the_spec_2_2_size_limits() -> None:
    """400 lines per module, 60 per function, 15 public methods per class (SPEC_DECISIONS 71 rule 1)."""
    assert source_tree_problems(SOURCE) == []


def test_every_markdown_file_of_the_package_renders_its_tables_whole() -> None:
    """Rows keep the header's cell count and nothing interrupts a table (SPEC_DECISIONS 71 rule 2)."""
    assert markdown_problems(OWNED_MARKDOWN, REPOSITORY) == []


def test_the_tests_need_nothing_the_package_does_not_declare() -> None:
    """The root plugins and every third party import of the tests are declared (SPEC_DECISIONS 117)."""
    assert undeclared_test_requirements(PACKAGE_DIR, PACKAGE_DIR / "tests") == []


def test_every_import_of_the_source_is_declared() -> None:
    """SPEC 2.1 and SPEC_DECISIONS 120: a runtime import is declared by the package that makes it."""
    assert undeclared_source_imports(PACKAGE_DIR, SOURCE) == []
