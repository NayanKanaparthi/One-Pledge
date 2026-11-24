"""No query of the bank agent depends on a default collation (SPEC_DECISIONS 133.3).

Every SQL text of the bank agent and its migrations is read from the source. An `ORDER BY`,
`GROUP BY`, `DISTINCT`, `min` or `max` whose item is not a plain column, and a comparison whose
left side is a text expression that is not a plain column (a concatenation, a cast, a function
result), must state `COLLATE "C"`. A positional `ORDER BY 1` is refused because the checker
cannot see what it orders. Case mapping (`lower`, `upper`, `ILIKE`) is never done in SQL on
external text. Plain columns are safe: every text column of the schema is `COLLATE "C"`
(migration 0016), and the catalogue's columns are `name` or `"C"` already.

The bodies of the guard functions (`LANGUAGE plpgsql`, in the migrations) are not queries this
checker can parse; they compare a row's own columns, which are `COLLATE "C"`, with literals and
variables of the default collation, so every comparison there resolves to `"C"` (a non-default
implicit collation wins over the default), and none orders text.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

PACKAGE = Path(__file__).resolve().parents[2]
TREES = (PACKAGE / "src" / "onepledge_bank_agent", PACKAGE / "migrations")
COLLATE_C = 'COLLATE "C"'
_SQL = re.compile(r"\b(SELECT|UPDATE|DELETE FROM|INSERT INTO|ALTER TABLE)\b")
_PLAIN = re.compile(
    r"^[a-z_][a-z0-9_]*(\.[a-z_][a-z0-9_]*)?( (ASC|DESC))?( NULLS (FIRST|LAST))?$", re.IGNORECASE
)
_CLAUSE_END = re.compile(r"\b(LIMIT|OFFSET|UNION|HAVING|FOR|FROM|WHERE|ORDER BY)\b|\)\s*(AS\b|$)|;|$")
_COMPARISON = re.compile(r"\s(NOT LIKE|LIKE|SIMILAR TO|!~\*?|~\*?|<>|!=|<=|>=|=|<|>)\s")
_PREDICATE_SPLIT = re.compile(r"\b(WHERE|AND|OR|ON|WHEN|HAVING)\b")
_FUNCTION = re.compile(r"\b([a-z_][a-z0-9_]*)\s*\(", re.IGNORECASE)
# Functions whose result is not collatable text or is of type `name` (collation "C").
NOT_TEXT = frozenset(
    {
        "count",
        "char_length",
        "length",
        "to_regclass",
        "current_database",
        "pg_get_userbyid",
        "has_table_privilege",
        "has_any_column_privilege",
        "has_function_privilege",
        "has_schema_privilege",
        "has_database_privilege",
        "has_sequence_privilege",
        "hashtext",
        "pg_advisory_xact_lock",
    }
)


def sql_texts(tree: Path) -> Iterator[tuple[str, str]]:
    """Every string literal (f-strings with their holes as `?`) that holds SQL, with its place."""
    for path in sorted(tree.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                text = node.value
            elif isinstance(node, ast.JoinedStr):
                text = "".join(
                    v.value if isinstance(v, ast.Constant) and isinstance(v.value, str) else "?"
                    for v in node.values
                )
            else:
                continue
            if _SQL.search(text) and "LANGUAGE plpgsql" not in text:
                yield f"{path.name}:{node.lineno}", " ".join(text.split())


def _items(clause: str) -> list[str]:
    items, depth, current = [], 0, ""
    for char in clause:
        depth += (char == "(") - (char == ")")
        if char == "," and depth == 0:
            items.append(current.strip())
            current = ""
        else:
            current += char
    return [item for item in [*items, current.strip()] if item]


def _without_alias(item: str) -> str:
    return re.sub(r"\s+AS\s+[a-z_][a-z0-9_]*$", "", item, flags=re.IGNORECASE)


def _states_c(item: str) -> bool:
    return COLLATE_C in item


def _text_expression(side: str) -> bool:
    if "||" in side or "::" in side:
        return True
    return any(name.lower() not in NOT_TEXT for name in _FUNCTION.findall(side))


def _ordering_problems(sql: str) -> list[str]:
    problems = []
    for keyword in ("ORDER BY", "GROUP BY"):
        for start in [m.end() for m in re.finditer(keyword, sql)]:
            end = _CLAUSE_END.search(sql, start)
            for item in _items(sql[start : end.start() if end else len(sql)]):
                if item.split()[0].isdigit():
                    problems.append(f"{keyword} by position {item}")
                elif not (_PLAIN.match(item) or _states_c(item)):
                    problems.append(f"{keyword} {item}")
    return problems


def _distinct_and_extreme_problems(sql: str) -> list[str]:
    problems = [
        f"DISTINCT {item}"
        for match in re.finditer(r"SELECT DISTINCT (.*?) FROM ", sql)
        for item in _items(match.group(1))
        if not (_PLAIN.match(_without_alias(item)) or _states_c(item))
    ]
    problems.extend(
        f"{match.group(1)} of {match.group(2)}"
        for match in re.finditer(r"\b(min|max)\(([^()]*)\)", sql, re.IGNORECASE)
        if not (_PLAIN.match(match.group(2).strip()) or _states_c(match.group(2)))
    )
    return problems


def _comparison_problems(sql: str) -> list[str]:
    problems = []
    for fragment in _PREDICATE_SPLIT.split(sql):
        found = _COMPARISON.search(fragment)
        if found is None:
            continue
        left = fragment[: found.start()].split("SELECT")[-1]
        if _text_expression(left) and not _states_c(left):
            problems.append(f"comparison {left.strip()} {found.group(1)}")
    return problems


def collation_problems(sql: str) -> list[str]:
    """What in one SQL text orders or compares text under a default collation."""
    problems = [f"case mapping {word}" for word in ("ILIKE", "lower(", "upper(") if word in sql]
    problems.extend(_ordering_problems(sql))
    problems.extend(_distinct_and_extreme_problems(sql))
    problems.extend(_comparison_problems(sql))
    return problems


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT a FROM t ORDER BY 1",
        "SELECT a FROM t ORDER BY a || b",
        "SELECT a FROM t GROUP BY lower(a)",
        "SELECT DISTINCT a || b FROM t",
        "SELECT max(a::text) FROM t",
        "SELECT a FROM t WHERE current_setting('x') = 'y'",
        "SELECT a FROM t WHERE a || b LIKE 'x%'",
        "SELECT a FROM t WHERE a ILIKE 'x'",
    ],
)
def test_the_checker_finds_each_kind_of_default_collation_use(sql: str) -> None:
    assert collation_problems(sql) != []


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT a FROM t ORDER BY a, t.b DESC",
        'SELECT a FROM t ORDER BY a || b COLLATE "C"',
        'SELECT DISTINCT (a || b) COLLATE "C" AS x FROM t',
        "SELECT a FROM t WHERE a = :value AND to_regclass(a) IS NULL",
        "SELECT a FROM t WHERE current_setting('x') COLLATE \"C\" = 'y'",
        "SELECT count(*) FROM t WHERE has_table_privilege(current_user, t.oid, 'SELECT') = true",
    ],
)
def test_the_checker_accepts_plain_columns_and_stated_collations(sql: str) -> None:
    assert collation_problems(sql) == []


def test_no_query_of_the_package_depends_on_a_default_collation() -> None:
    found = {
        place: problems
        for tree in TREES
        for place, sql in sql_texts(tree)
        if (problems := collation_problems(sql))
    }
    assert found == {}


_COLUMN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)+(\.(desc|asc)\(\))?$")


def expression_problems(source: str) -> list[str]:
    """Each SQLAlchemy `order_by`, `group_by` or `distinct` over anything but a mapped column, and
    each case mapping (`func.lower`, `func.upper`, `ilike`) in the queries built in Python."""
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        called = ast.unparse(node.func)
        if called.endswith((".order_by", ".group_by", ".distinct")):
            found.extend(
                f"line {node.lineno}: {called.rsplit('.', 1)[1]} {ast.unparse(argument)}"
                for argument in node.args
                if not _COLUMN.match(ast.unparse(argument))
            )
        if called.endswith(("func.lower", "func.upper", ".ilike", ".notilike")):
            found.append(f"line {node.lineno}: case mapping {called}")
    return found


@pytest.mark.parametrize(
    "source",
    [
        "select(a).order_by(func.lower(Row.name))",
        "select(a).order_by(Row.code + Row.name)",
        "select(a).group_by(cast(Row.id, Text))",
        "select(a).where(Row.email.ilike('x'))",
    ],
)
def test_the_expression_check_finds_each_kind(source: str) -> None:
    assert expression_problems(source) != []


def test_the_expression_check_accepts_mapped_columns() -> None:
    assert expression_problems("select(a).order_by(Row.loan_id, check.check_type.desc()).distinct()") == []


def test_no_query_built_in_python_orders_text_by_an_expression() -> None:
    source = PACKAGE / "src" / "onepledge_bank_agent"
    found = {
        str(path.relative_to(source)): problems
        for path in sorted(source.rglob("*.py"))
        if (problems := expression_problems(path.read_text(encoding="utf-8")))
    }
    assert found == {}
