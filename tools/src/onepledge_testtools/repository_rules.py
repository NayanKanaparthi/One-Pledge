"""Two repository rules every package enforces in its own tests (SPEC_DECISIONS 71).

1. The size limits of SPEC 2.2 on a `src/` tree: at most 400 lines per module, 60 lines per
   function and 15 public methods per class.
2. Markdown tables render whole: every row of a table has the header's cell count, and no
   line that is not a table row sits between a table's rows or directly below its last row.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable, Iterator
from pathlib import Path

MAX_MODULE_LINES = 400
MAX_FUNCTION_LINES = 60
MAX_PUBLIC_METHODS = 15

_DELIMITER_CELL = re.compile(r"^\s*:?-{3,}:?\s*$")
_FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
_UNESCAPED_PIPE = re.compile(r"(?<!\\)\|")
# CODE-03 of the rubric, as literal as the reviewers' grep: `grep -rnE "[A-HJ-NPR-Z0-9]{17}"`.
VIN_SHAPED = re.compile(r"[A-HJ-NPR-Z0-9]{17}")
_SKIPPED_PARTS = frozenset({".git", ".venv", "node_modules", ".pytest_cache", ".mypy_cache", ".ruff_cache"})


def python_modules(tree: Path) -> list[Path]:
    """Every `.py` file under `tree`, in path order."""
    return sorted(path for path in tree.rglob("*.py") if "__pycache__" not in path.parts)


def size_problems(module: Path, text: str) -> list[str]:
    """Where the module breaks a SPEC 2.2 limit, one sentence each."""
    problems: list[str] = []
    lines = len(text.splitlines())
    if lines > MAX_MODULE_LINES:
        problems.append(f"{module} has {lines} lines; the limit is {MAX_MODULE_LINES}.")
    for node in ast.walk(ast.parse(text, filename=str(module))):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            length = (node.end_lineno or node.lineno) - node.lineno + 1
            if length > MAX_FUNCTION_LINES:
                problems.append(
                    f"{module}:{node.lineno} function {node.name} has {length} lines; "
                    f"the limit is {MAX_FUNCTION_LINES}."
                )
        elif isinstance(node, ast.ClassDef):
            public = [
                item.name
                for item in node.body
                if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef) and not item.name.startswith("_")
            ]
            if len(public) > MAX_PUBLIC_METHODS:
                problems.append(
                    f"{module}:{node.lineno} class {node.name} has {len(public)} public methods; "
                    f"the limit is {MAX_PUBLIC_METHODS}."
                )
    return problems


def source_tree_problems(tree: Path) -> list[str]:
    """Every size limit broken anywhere in a `src/` tree."""
    problems: list[str] = []
    for module in python_modules(tree):
        problems.extend(size_problems(module.relative_to(tree.parent), module.read_text(encoding="utf-8")))
    return problems


def vin_shaped_lines(tree: Path) -> list[str]:
    """Every line of a text file under `tree` that the CODE-03 grep would print, as `path:line`."""
    found: list[str] = []
    for path in sorted(tree.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        found.extend(
            f"{path.relative_to(tree.parent)}:{number}"
            for number, line in enumerate(text.splitlines(), start=1)
            if VIN_SHAPED.search(line)
        )
    return found


def markdown_files(root: Path) -> list[Path]:
    """Every Markdown file under `root`, skipping tool caches and environments."""
    return sorted(path for path in root.rglob("*.md") if not _SKIPPED_PARTS & set(path.parts))


def _cells(line: str) -> int:
    body = line.strip()
    body = body.removeprefix("|")
    if body.endswith("|") and not body.endswith("\\|"):
        body = body[:-1]
    return len(_UNESCAPED_PIPE.split(body))


def _is_row(line: str) -> bool:
    return line.lstrip().startswith("|")


def _is_delimiter(line: str) -> bool:
    if not _is_row(line):
        return False
    body = line.strip().strip("|")
    return all(_DELIMITER_CELL.match(cell) for cell in _UNESCAPED_PIPE.split(body))


def _outside_fences(lines: list[str]) -> Iterator[tuple[int, str]]:
    fence: str | None = None
    for number, line in enumerate(lines, start=1):
        match = _FENCE.match(line)
        if match is not None and fence is None:
            fence = match.group(1)[0]
            yield number, ""
            continue
        if match is not None and match.group(1)[0] == fence:
            fence = None
            yield number, ""
            continue
        if fence is None:
            yield number, line
        else:
            yield number, ""


def table_problems(name: str, text: str) -> list[str]:
    """Every broken table of one Markdown document, one sentence each."""
    lines = [line for _, line in _outside_fences(text.splitlines())]
    problems: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if _is_row(line) and index + 1 < len(lines) and _is_delimiter(lines[index + 1]):
            index = _check_table(name, lines, index, problems)
            continue
        if _is_row(line):
            problems.append(f"{name}:{index + 1} is a table row that belongs to no table.")
        index += 1
    return problems


def _check_table(name: str, lines: list[str], start: int, problems: list[str]) -> int:
    width = _cells(lines[start])
    index = start + 1
    while index < len(lines) and _is_row(lines[index]):
        cells = _cells(lines[index])
        if cells != width:
            problems.append(f"{name}:{index + 1} has {cells} cells; the table's header has {width}.")
        index += 1
    if index < len(lines) and lines[index].strip():
        problems.append(
            f"{name}:{index + 1} interrupts the table above it; leave a blank line below a table."
        )
    return index


def markdown_problems(files: Iterable[Path], relative_to: Path) -> list[str]:
    """Every broken table in the given Markdown files."""
    problems: list[str] = []
    for path in files:
        problems.extend(table_problems(str(path.relative_to(relative_to)), path.read_text(encoding="utf-8")))
    return problems


_LAYOUT_HEADING = "## Layout"
FENCES = 2  # the opening and the closing fence of a code block


def _layout_block(text: str) -> list[str]:
    """The lines of the first fenced block after the `## Layout` heading, or none."""
    lines = text.splitlines()
    if _LAYOUT_HEADING not in lines:
        return []
    start = lines.index(_LAYOUT_HEADING)
    fences = [i for i in range(start, len(lines)) if lines[i].startswith("```")]
    return lines[fences[0] + 1 : fences[1]] if len(fences) >= FENCES else []


def _named_paths(block: list[str], repository: Path) -> list[Path]:
    """Every path a layout block names, relative to the repository.

    The first line is the package directory. Below it, each line holds names separated by
    spaces, indented two spaces under the directory they belong to; text after `#` is a
    comment. `name/` is a directory, `name.ext` a file, a bare `name` a module or package.
    """
    content = [line for line in block if line.split("#", 1)[0].strip()]
    if not content:
        return []
    stack: list[tuple[int, Path]] = [(-1, repository / content[0].split()[0].rstrip("/"))]
    named: list[Path] = [stack[0][1]]
    for line in content[1:]:
        indent = len(line) - len(line.lstrip(" "))
        while len(stack) > 1 and stack[-1][0] >= indent:
            stack.pop()
        parent = stack[-1][1]
        for token in line.split("#", 1)[0].split():
            if token.endswith("/"):
                directory = parent / token.rstrip("/")
                named.append(directory)
                stack.append((indent, directory))
            elif "." in token:
                named.append(parent / token)
            else:
                # A bare name is a module, or a file or package of exactly that name.
                plain = parent / token
                named.append(plain if plain.exists() else parent / f"{token}.py")
    return named


def layout_problems(readme: Path, repository: Path, sources: Iterable[Path]) -> list[str]:
    """Where a README's layout block and the tree differ (SPEC_DECISIONS 112.11).

    Every path the block names must exist, and every module of each source tree must be named.
    """
    named = _named_paths(_layout_block(readme.read_text(encoding="utf-8")), repository)
    if not named:
        return [f"{readme.relative_to(repository)} has no layout block"]
    shown = str(readme.relative_to(repository))
    problems = [
        f"{shown} names {path.relative_to(repository)}, which does not exist"
        for path in named
        if not path.exists()
    ]
    listed = set(named)
    for tree in sources:
        for module in python_modules(tree):
            if module.name != "__init__.py" and module not in listed:
                problems.append(f"{shown} does not name {module.relative_to(repository)}")
    return problems
