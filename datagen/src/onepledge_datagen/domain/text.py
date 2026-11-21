"""Counted nouns for every fixed message of the generator (SPEC_DECISIONS 23 e)."""

from __future__ import annotations


def plural(count: int, singular: str, plural_form: str | None = None) -> str:
    """`1 invariant violation`, `2 invariant violations`, `5,273 tape rows`."""
    noun = singular if count == 1 else (plural_form or singular + "s")
    return f"{count:,} {noun}"
