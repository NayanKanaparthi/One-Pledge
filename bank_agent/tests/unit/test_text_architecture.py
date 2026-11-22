"""Architecture rules that make the ASCII and integer classes impossible to reintroduce
(SPEC_DECISIONS 37, 45 and 50).

- `str.isdigit`, `isdecimal`, `isnumeric` (and the other Unicode classifiers) appear nowhere
  in `src/` or the test kit, nor does a regular expression shorthand class (`\\d`, `\\s`,
  `\\w`), which matches more than ASCII in Python.
- Text becomes an integer only in `domain/text_rules.py`: `int`, `float` and `Decimal` are
  not called in the domain or the settings loader.
- The parsers of tape cells and settings use the ASCII helpers, never the Unicode
  `strip`, `split`, `upper` or `lower` without arguments.
- The independent oracle of the test kit imports no `re`, calls no `int`, `float` or
  `Decimal`, and imports nothing from the bank agent.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

PACKAGE = Path(__file__).resolve().parents[2]
SOURCE = PACKAGE / "src" / "onepledge_bank_agent"
TESTKIT = PACKAGE / "tests" / "bank_testkit"
INDEPENDENT = TESTKIT / "independent.py"
TEXT_RULES = SOURCE / "domain" / "text_rules.py"
UNICODE_CLASSIFIERS = frozenset(
    {"isdigit", "isdecimal", "isnumeric", "isspace", "isalnum", "isalpha", "islower", "isupper", "istitle"}
)
SHORTHANDS = ("\\d", "\\s", "\\w", "\\D", "\\S", "\\W")
NUMBER_BUILDERS = frozenset({"int", "float", "Decimal"})
UNICODE_TEXT_METHODS = frozenset(
    {"strip", "lstrip", "rstrip", "split", "upper", "lower", "casefold", "title"}
)
PARSERS = [
    *sorted((SOURCE / "domain" / "cleaning").glob("*.py")),
    SOURCE / "domain" / "values.py",
    SOURCE / "domain" / "vin.py",
    SOURCE / "domain" / "lender_names.py",
    SOURCE / "infrastructure" / "settings.py",
]


def files(*roots: Path) -> list[Path]:
    return [path for root in roots for path in sorted(root.rglob("*.py")) if "__pycache__" not in path.parts]


def nodes(path: Path) -> list[ast.AST]:
    return list(ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))))


def where(path: Path, node: ast.AST) -> str:
    return f"{path.relative_to(PACKAGE)}:{getattr(node, 'lineno', 0)}"


def calls(path: Path) -> list[tuple[ast.Call, str]]:
    """Every call with the name of what is called (`int`, or the attribute name of `x.strip`)."""
    found = []
    for node in nodes(path):
        if isinstance(node, ast.Call):
            function = node.func
            name = function.id if isinstance(function, ast.Name) else getattr(function, "attr", "")
            found.append((node, name))
    return found


def test_no_unicode_classifier_and_no_regular_expression_shorthand_class() -> None:
    violations = []
    for path in files(SOURCE, TESTKIT):
        for call, name in calls(path):
            if name in UNICODE_CLASSIFIERS:
                violations.append(f"{where(path, call)} calls {name}")
        for node in nodes(path):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and path != TEXT_RULES:
                violations += [f"{where(path, node)} holds {s}" for s in SHORTHANDS if s in node.value]
    assert violations == []


def test_text_becomes_a_number_only_in_text_rules() -> None:
    domain = [path for path in files(SOURCE / "domain") if path != TEXT_RULES]
    targets = [*domain, SOURCE / "infrastructure" / "settings.py", INDEPENDENT]
    violations = [
        f"{where(path, node)} calls {name}"
        for path in targets
        for node, name in calls(path)
        if name in NUMBER_BUILDERS
    ]
    assert violations == []


def test_parsers_use_the_ascii_helpers_for_trimming_splitting_and_case() -> None:
    violations = [
        f"{where(path, node)} calls {name}() without arguments"
        for path in [*PARSERS, INDEPENDENT]
        for node, name in calls(path)
        if name in UNICODE_TEXT_METHODS and isinstance(node.func, ast.Attribute) and not node.args
    ]
    assert violations == []


def test_the_independent_oracle_shares_no_technique_or_code_with_the_product() -> None:
    imported = [
        (node, name)
        for node in nodes(INDEPENDENT)
        if isinstance(node, ast.Import | ast.ImportFrom)
        for name in (
            [alias.name for alias in node.names] if isinstance(node, ast.Import) else [node.module or ""]
        )
    ]
    assert [name for _, name in imported if name == "re" or name.startswith("onepledge_bank_agent")] == []


def test_the_rules_find_a_planted_violation(tmp_path: Path) -> None:
    planted = tmp_path / "planted.py"
    planted.write_text(
        'import re\nX = re.compile(r"\\d+")\nY = "7".isdigit()\nZ = int("7")\n', encoding="utf-8"
    )
    assert [name for _, name in calls(planted) if name in UNICODE_CLASSIFIERS] == ["isdigit"]
    assert [name for _, name in calls(planted) if name in NUMBER_BUILDERS] == ["int"]
    constants = [n.value for n in nodes(planted) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    assert any("\\d" in value for value in constants)
