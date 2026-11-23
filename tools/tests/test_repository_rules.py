"""The size and Markdown table rules (SPEC_DECISIONS 71), on hand-written inputs and on the tooling itself."""

from __future__ import annotations

from pathlib import Path

import pytest

from onepledge_testtools.declarations import undeclared_source_imports, undeclared_test_requirements
from onepledge_testtools.repository_rules import (
    VIN_SHAPED,
    _named_paths,
    markdown_files,
    markdown_problems,
    size_problems,
    source_tree_problems,
    table_problems,
)

TOOLS = Path(__file__).resolve().parents[1]
REPOSITORY = TOOLS.parent
MODULE = Path("pkg/module.py")


def _function(name: str, body_lines: int) -> str:
    return f"def {name}() -> None:\n" + "    pass\n" * (body_lines - 1)


@pytest.mark.unit
def test_a_module_of_400_lines_passes_and_401_fails() -> None:
    assert size_problems(MODULE, "x = 1\n" * 400) == []
    assert size_problems(MODULE, "x = 1\n" * 401) == ["pkg/module.py has 401 lines; the limit is 400."]


@pytest.mark.unit
def test_a_function_of_60_lines_passes_and_61_fails_also_when_nested() -> None:
    assert size_problems(MODULE, _function("short", 60)) == []
    nested = "class Holder:\n" + "".join(f"    {line}\n" for line in _function("long", 61).splitlines())
    assert size_problems(MODULE, nested) == ["pkg/module.py:2 function long has 61 lines; the limit is 60."]


@pytest.mark.unit
def test_a_class_of_15_public_methods_passes_and_16_fails_private_ones_not_counted() -> None:
    def holder(public: int) -> str:
        methods = [f"    def m{i}(self) -> None: ...\n" for i in range(public)]
        return "class Holder:\n" + "".join(methods) + "    def _private(self) -> None: ...\n"

    assert size_problems(MODULE, holder(15)) == []
    assert size_problems(MODULE, holder(16)) == [
        "pkg/module.py:1 class Holder has 16 public methods; the limit is 15."
    ]


GOOD_TABLE = """# Title

| A | B |
|---|---|
| `x` | y \\| z |
| 1 | 2 |

Text after a blank line.

```
| not | a | table |
```
"""


@pytest.mark.unit
def test_a_whole_table_and_pipes_in_code_blocks_pass() -> None:
    assert table_problems("doc.md", GOOD_TABLE) == []


@pytest.mark.unit
def test_a_row_with_another_cell_count_fails() -> None:
    text = "| A | B | C |\n|---|---|---|\n| 1 | 2 | 3 |\n| 1 | 2 | 3 | Every\n"
    assert table_problems("doc.md", text) == ["doc.md:4 has 4 cells; the table's header has 3."]


@pytest.mark.unit
def test_a_paragraph_directly_below_the_last_row_fails() -> None:
    text = "| A | B |\n|---|---|\n| 1 | 2 |\nfailure is one sentence\n"
    assert table_problems("doc.md", text) == [
        "doc.md:4 interrupts the table above it; leave a blank line below a table."
    ]


@pytest.mark.unit
def test_a_row_cut_off_from_its_table_by_a_paragraph_fails() -> None:
    text = "| A | B |\n|---|---|\n| 1 | 2 |\n\nA paragraph.\n| 3 | 4 |\n"
    assert table_problems("doc.md", text) == ["doc.md:6 is a table row that belongs to no table."]


@pytest.mark.unit
def test_an_unescaped_pipe_in_a_code_span_splits_the_cell_as_github_renders_it() -> None:
    text = '| Kind | Value |\n|---|---|\n| `ASSET` | `"AUTO|" + V` |\n'
    assert table_problems("doc.md", text) == ["doc.md:3 has 3 cells; the table's header has 2."]


@pytest.mark.unit
def test_the_tooling_keeps_the_size_limits_and_its_documents_render() -> None:
    assert source_tree_problems(TOOLS / "src" / "onepledge_testtools") == []
    assert markdown_problems(markdown_files(TOOLS), REPOSITORY) == []


@pytest.mark.unit
def test_the_vin_shaped_pattern_is_the_reviewers_grep() -> None:
    assert VIN_SHAPED.search("ORDER = 2**252 + 27742317777372353535851937790883648493")
    assert not VIN_SHAPED.search("ORDER = 2**252 + 27_742_317_777_372_353_535_851_937_790_883_648_493")
    assert not VIN_SHAPED.search("1HGCM82633A00435")


@pytest.mark.unit
def test_the_tools_tests_need_nothing_the_tools_do_not_declare() -> None:
    assert undeclared_test_requirements(TOOLS, TOOLS / "tests") == []
    assert undeclared_source_imports(TOOLS, TOOLS / "src" / "onepledge_testtools") == []


@pytest.mark.unit
def test_a_layout_block_is_read_as_a_tree_with_comments() -> None:
    block = [
        "tools/",
        "  pyproject.toml",
        "  src/",
        "    onepledge_testtools/   # the package",
        "      processes  network   # helpers",
        "      no_such_module",
    ]
    named = _named_paths(block, REPOSITORY)
    package = REPOSITORY / "tools" / "src" / "onepledge_testtools"
    assert REPOSITORY / "tools" / "pyproject.toml" in named
    assert {package / "processes.py", package / "network.py"} <= set(named)
    assert package / "no_such_module.py" in named and not (package / "no_such_module.py").exists()
