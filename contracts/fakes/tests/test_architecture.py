"""Import boundaries and environment access of the doubles package (SPEC 2.1, 21.6)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from onepledge_testtools.declarations import (
    undeclared_source_imports,
    undeclared_test_requirements,
)
from onepledge_testtools.repository_rules import (
    layout_problems,
    markdown_files,
    markdown_problems,
    source_tree_problems,
    vin_shaped_lines,
)

pytestmark = pytest.mark.unit

SOURCE = Path(__file__).resolve().parents[1] / "src" / "onepledge_contract_fakes"
REPOSITORY = SOURCE.parents[3]
# This package owns contracts/ and the root tooling, so their documents are checked here.
PACKAGE_DIR = SOURCE.parents[1]
OWNED_MARKDOWN = markdown_files(REPOSITORY / "contracts") + sorted(REPOSITORY.glob("*.md"))
OTHER_PACKAGES = (
    "onepledge_bank_agent",
    "onepledge_datagen",
    "onepledge_outside_records",
    "onepledge_evaluation",
)


def modules() -> list[Path]:
    return sorted(SOURCE.rglob("*.py"))


def imported_names(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_no_other_onepledge_package_is_imported() -> None:
    for path in modules():
        offending = {n for n in imported_names(path) if n.split(".")[0] in OTHER_PACKAGES}
        assert not offending, f"{path.relative_to(SOURCE)} imports {sorted(offending)}"


def test_os_environ_is_read_only_in_the_settings_module() -> None:
    for path in modules():
        if path == SOURCE / "settings.py":
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in ("environ", "getenv"):
                pytest.fail(f"{path.relative_to(SOURCE)} reads the environment")


def test_no_source_module_looks_like_a_test_module() -> None:
    assert not [p for p in modules() if p.name.startswith("test_") or p.name.endswith("_test.py")]


def test_every_module_says_it_is_a_test_double() -> None:
    for path in modules():
        docstring = ast.get_docstring(ast.parse(path.read_text())) or ""
        text = docstring.lower()
        assert (
            "test double" in text or "test controls" in text or "contract checker" in text or "check" in text
        ), f"{path.relative_to(SOURCE)} does not say what it is"


def test_no_type_check_is_suppressed() -> None:
    package = SOURCE.parents[1]
    for path in [*package.joinpath("src").rglob("*.py"), *package.joinpath("tests").rglob("*.py")]:
        assert "type: " + "ignore" not in path.read_text(encoding="utf-8"), path.name


def parsed(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


# SPEC_DECISIONS 37: one text to integer function, `validation.parse_integer_text`, which builds
# the value by arithmetic. Nothing in the package calls `int()` at all, asks Unicode whether a
# character is a digit, or writes the Unicode digit class `\d` in a pattern.
UNICODE_DIGIT_PREDICATES = frozenset({"isdigit", "isdecimal", "isnumeric"})


def test_no_text_becomes_an_integer_outside_the_one_function() -> None:
    for path in modules():
        nodes = list(ast.walk(parsed(path)))
        name = path.relative_to(SOURCE)
        called = {n.func.id for n in nodes if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        attributes = {n.attr for n in nodes if isinstance(n, ast.Attribute)}
        strings = [n.value for n in nodes if isinstance(n, ast.Constant) and isinstance(n.value, str)]
        assert "int" not in called, f"{name} calls int(); use validation.parse_integer_text"
        assert not attributes & UNICODE_DIGIT_PREDICATES, (
            f"{name} asks Unicode whether a character is a digit"
        )
        assert not [s for s in strings if "\\d" in s], f"{name} writes the Unicode digit class in a pattern"


# SPEC_DECISIONS 38: JSON text is parsed only by the bounded parser (request bodies and settings)
# and by the contract checker's loader of the repository's own files; a request model validates
# the same bytes only after the bounded parse in `web.requests.parse_model_json`.
JSON_PARSERS = frozenset({SOURCE / "json_body.py", SOURCE / "contracts_check" / "documents.py"})


def json_parsing_calls(path: Path) -> list[str]:
    found = []
    for call in ast.walk(parsed(path)):
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute):
            owner = call.func.value.id if isinstance(call.func.value, ast.Name) else ""
            attr = call.func.attr
            if (attr in ("loads", "load") and owner == "json") or attr in ("json", "model_validate_json"):
                found.append(attr)
    return found


def test_json_text_is_parsed_only_by_the_bounded_parser() -> None:
    for path in modules():
        found = json_parsing_calls(path)
        if path == SOURCE / "web" / "requests.py":
            found = [attr for attr in found if attr != "model_validate_json"]
        assert path in JSON_PARSERS or not found, f"{path.relative_to(SOURCE)} parses JSON with {found}"


# SPEC_DECISIONS 35: every app is built by `web.middleware.new_service_app`, the one place that turns slash
# redirection off.
def test_apps_are_built_only_with_slash_redirection_off() -> None:
    builders = 0
    for path in modules():
        for node in ast.walk(parsed(path)):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "FastAPI":
                assert path == SOURCE / "web" / "middleware.py", (
                    f"{path.relative_to(SOURCE)} builds its own FastAPI app"
                )
                flag = {k.arg: k.value for k in node.keywords}.get("redirect_slashes")
                assert isinstance(flag, ast.Constant) and flag.value is False
                builders += 1
    assert builders == 1


def test_no_argument_is_converted_with_int_or_float() -> None:
    """`type=int` would accept `+5`, ` 7` and non-ASCII digits (SPEC_DECISIONS 37)."""
    for path in sorted(SOURCE.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.keyword) and node.arg == "type" and isinstance(node.value, ast.Name):
                assert node.value.id not in ("int", "float"), (
                    f"{path.name} converts an argument with {node.value.id}"
                )


# SPEC_DECISIONS 66: one place writes the records of a 5xx response. Routes raise named errors;
# only the web plumbing builds envelopes and writes failure records.
FAILURE_EVENTS = frozenset({"UNHANDLED_EXCEPTION", "INTERNAL_ERROR", "SERVER_ERROR"})


def test_only_the_web_plumbing_builds_envelopes_and_failure_records() -> None:
    for path in modules():
        # The web plumbing writes the records of a 5xx; the CLI's last resort handler writes its
        # own one error record (SPEC_DECISIONS 78).
        if path.parent == SOURCE / "web" or path == SOURCE / "cli_output.py":
            continue
        tree = parsed(path)
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        attributes = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        assert not names & {"error_response", "failure_response"}, f"{path.name} builds an envelope"
        assert not attributes & FAILURE_EVENTS, f"{path.name} writes a failure record"


def test_the_source_tree_keeps_the_spec_2_2_size_limits() -> None:
    """400 lines per module, 60 per function, 15 public methods per class (SPEC_DECISIONS 71 rule 1)."""
    assert source_tree_problems(SOURCE) == []


def test_every_markdown_file_of_the_package_renders_its_tables_whole() -> None:
    """Rows keep the header's cell count and nothing interrupts a table (SPEC_DECISIONS 71 rule 2)."""
    assert markdown_problems(OWNED_MARKDOWN, REPOSITORY) == []


def test_no_source_line_is_vin_shaped() -> None:
    """CODE-03 is literal: the reviewers' grep for 17 VIN alphabet characters prints nothing."""
    assert vin_shaped_lines(SOURCE) == []


def test_the_tests_need_nothing_the_package_does_not_declare() -> None:
    """The root plugins and every third party import of the tests are declared (SPEC_DECISIONS 117)."""
    assert undeclared_test_requirements(PACKAGE_DIR, PACKAGE_DIR / "tests") == []


def test_every_import_of_the_source_is_declared() -> None:
    """SPEC 2.1 and SPEC_DECISIONS 120: a runtime import is declared by the package that makes it."""
    assert undeclared_source_imports(PACKAGE_DIR, SOURCE) == []


def test_the_readme_layout_names_what_exists_and_every_module() -> None:
    """SPEC_DECISIONS 112.11: every path of the layout block exists and every module is named."""
    assert layout_problems(PACKAGE_DIR / "README.md", REPOSITORY, [SOURCE]) == []
