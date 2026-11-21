"""Lender name normalisation of SPEC 11.1."""

from __future__ import annotations

import re

_SUFFIXES: tuple[tuple[str, ...], ...] = (
    ("LLC",),
    ("L", "L", "C"),
    ("INC",),
    ("INCORPORATED",),
    ("CORP",),
    ("CORPORATION",),
    ("CO",),
    ("COMPANY",),
    ("LTD",),
    ("LIMITED",),
    ("LP",),
    ("L", "P"),
)
_NOT_NAME_CHARACTER = re.compile(r"[^A-Z0-9 ]")


def normalise_lender_name(name: str | None) -> str:
    """Upper case, `&` as AND, punctuation to spaces, collapsed, legal suffixes dropped."""
    if name is None:
        return ""
    text = name.upper().replace("&", " AND ")
    tokens = _NOT_NAME_CHARACTER.sub(" ", text).split()
    changed = True
    while changed and tokens:
        changed = False
        for suffix in _SUFFIXES:
            if len(tokens) >= len(suffix) and tuple(tokens[-len(suffix) :]) == suffix:
                tokens = tokens[: -len(suffix)]
                changed = True
                break
    return " ".join(tokens)


def names_match(first: str | None, second: str | None) -> bool:
    """Equal non-empty normalised forms."""
    left, right = normalise_lender_name(first), normalise_lender_name(second)
    return bool(left) and left == right
