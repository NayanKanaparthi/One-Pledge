"""Import boundaries, environment access and size limits of the package (SPEC 2.1, 2.2, 21.6)."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

PACKAGE = Path(__file__).resolve().parents[1]
SOURCE = PACKAGE / "src" / "onepledge_outside_records"
MIGRATIONS = PACKAGE / "migrations"
OTHER_PACKAGES = frozenset(
    {"onepledge_bank_agent", "onepledge_datagen", "onepledge_contract_fakes", "onepledge_evaluation"}
)
SQL_PACKAGES = frozenset({"sqlalchemy", "psycopg", "alembic"})
WEB_PACKAGES = frozenset({"fastapi", "starlette", "uvicorn"})
OWN = "onepledge_outside_records"
MAX_MODULE_LINES, MAX_FUNCTION_LINES = 400, 60


def modules() -> list[Path]:
    return sorted(SOURCE.rglob("*.py"))


def layer(path: Path) -> str:
    parts = path.relative_to(SOURCE).parts
    return parts[0] if len(parts) > 1 else "package"


def imports(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


def roots(path: Path) -> set[str]:
    return {name.split(".")[0] for name in imports(path)}


def own_layers(path: Path) -> set[str]:
    return {name.split(".")[1] for name in imports(path) if name.startswith(OWN + ".")}


def test_no_other_onepledge_package_is_imported() -> None:
    for path in [*modules(), *MIGRATIONS.rglob("*.py")]:
        assert not roots(path) & OTHER_PACKAGES, path.name


def test_the_domain_imports_only_the_standard_library() -> None:
    for path in modules():
        if layer(path) == "domain":
            third_party = {r for r in roots(path) if r not in sys.stdlib_module_names and r != OWN}
            assert not third_party, f"{path.name} imports {sorted(third_party)}"
            assert own_layers(path) <= {"domain"}, path.name


def test_the_application_imports_only_the_domain() -> None:
    for path in modules():
        if layer(path) == "application":
            third_party = {r for r in roots(path) if r not in sys.stdlib_module_names and r != OWN}
            assert not third_party, f"{path.name} imports {sorted(third_party)}"
            assert own_layers(path) <= {"domain", "application"}, path.name


def test_infrastructure_does_not_reach_the_interface() -> None:
    for path in modules():
        if layer(path) == "infrastructure":
            assert "interface" not in own_layers(path), path.name


def test_sql_appears_only_in_infrastructure_and_fastapi_only_in_the_interface() -> None:
    for path in modules():
        if roots(path) & SQL_PACKAGES:
            assert layer(path) in ("infrastructure", "interface"), path.name
        if roots(path) & WEB_PACKAGES:
            assert layer(path) == "interface", path.name
    interface_sql = {
        path.name for path in modules() if layer(path) == "interface" and roots(path) & {"psycopg", "alembic"}
    }
    assert not interface_sql


def test_sql_text_is_written_only_in_infrastructure() -> None:
    for path in modules():
        if layer(path) in ("domain", "application"):
            text = path.read_text(encoding="utf-8").upper()
            for keyword in ("SELECT ", "INSERT ", "UPDATE ", "DELETE FROM", "COPY "):
                assert keyword not in text, f"{path.name} holds SQL"


def test_the_environment_is_read_only_in_the_settings_module() -> None:
    for path in modules():
        if path == SOURCE / "infrastructure" / "settings.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Attribute) and node.attr in ("environ", "getenv"):
                pytest.fail(f"{path.name} reads the environment")


def test_modules_and_functions_stay_small() -> None:
    for path in modules():
        source = path.read_text(encoding="utf-8")
        assert len(source.splitlines()) <= MAX_MODULE_LINES, path.name
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.end_lineno is not None:
                assert node.end_lineno - node.lineno + 1 <= MAX_FUNCTION_LINES, f"{path.name}:{node.name}"


def test_every_module_has_a_docstring_and_never_names_the_scoring_file() -> None:
    for path in [*modules(), *MIGRATIONS.rglob("*.py")]:
        source = path.read_text(encoding="utf-8")
        if path.name != "__init__.py":
            assert ast.get_docstring(ast.parse(source)), path.name
        assert "answer" + "_key" not in source, path.name


def test_no_type_check_is_suppressed() -> None:
    for path in [*PACKAGE.rglob("*.py")]:
        assert "type: " + "ignore" not in path.read_text(encoding="utf-8"), path.name


def parsed(path: Path) -> list[ast.AST]:
    return list(ast.walk(ast.parse(path.read_text(encoding="utf-8"))))


# SPEC_DECISIONS 37 and 45: one module turns text into integers and dates, by arithmetic over
# the ten ASCII digits. Nothing else calls `int()`, asks Unicode whether a character is a digit,
# writes the Unicode digit class `\d`, or parses an ISO date with the permissive standard parser.
TEXT_VALUES = SOURCE / "domain" / "text_values.py"
UNICODE_DIGIT_PREDICATES = frozenset({"isdigit", "isdecimal", "isnumeric"})


def test_no_text_becomes_a_number_or_date_outside_the_one_module() -> None:
    for path in [*modules(), *sorted(MIGRATIONS.rglob("*.py"))]:
        nodes = parsed(path)
        name = path.relative_to(PACKAGE)
        called = {n.func.id for n in nodes if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        attributes = {n.attr for n in nodes if isinstance(n, ast.Attribute)}
        strings = [n.value for n in nodes if isinstance(n, ast.Constant) and isinstance(n.value, str)]
        assert "int" not in called, f"{name} calls int(); use domain.text_values"
        assert not attributes & UNICODE_DIGIT_PREDICATES, (
            f"{name} asks Unicode whether a character is a digit"
        )
        assert not [s for s in strings if "\\d" in s], f"{name} writes the Unicode digit class in a pattern"
        if path != TEXT_VALUES:
            assert "fromisoformat" not in attributes, f"{name} parses a date outside domain.text_values"


# SPEC_DECISIONS 35: the app is built in one place, with slash redirection off.
def test_the_app_is_built_only_with_slash_redirection_off() -> None:
    builders = []
    for path in modules():
        for node in parsed(path):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "FastAPI":
                flag = {k.arg: k.value for k in node.keywords}.get("redirect_slashes")
                assert isinstance(flag, ast.Constant) and flag.value is False, path.name
                builders.append(path.name)
    assert builders == ["plumbing.py"]
