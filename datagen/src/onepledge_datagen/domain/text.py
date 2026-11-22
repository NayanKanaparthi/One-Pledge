"""Counted nouns for every fixed message of the generator (SPEC_DECISIONS 23 e)."""

from __future__ import annotations

from collections.abc import Iterable


def plural(count: int, singular: str, plural_form: str | None = None) -> str:
    """`1 invariant violation`, `2 invariant violations`, `5,273 tape rows`."""
    noun = singular if count == 1 else (plural_form or singular + "s")
    return f"{count:,} {noun}"


def plain_list(values: Iterable[object]) -> str:
    """Values as comma separated plain text, `none` when empty: never a Python repr (SPEC_DECISIONS 43.11)."""
    shown = [str(value) for value in values]
    return ", ".join(shown) if shown else "none"


def plain_pairs(pairs: Iterable[tuple[str, str]]) -> str:
    """Pairs such as alerts as `ROLE MATCH_KIND`, comma separated, `none` when empty."""
    return plain_list(f"{first} {second}" for first, second in pairs)
