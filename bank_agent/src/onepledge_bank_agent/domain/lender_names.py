"""Lender name normalisation (SPEC 11.1), used to compare a title's lienholder and a contract's lender."""

from __future__ import annotations

import re

from onepledge_bank_agent.domain.text_rules import ascii_upper

_LEGAL_SUFFIXES: tuple[tuple[str, ...], ...] = (
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
_OUTSIDE_NAME_ALPHABET = re.compile(r"[^A-Z0-9 ]")


def normalise_lender_name(name: str | None) -> str:
    """Upper case, `&` as ` AND `, other punctuation as spaces, collapsed, trailing legal suffixes dropped."""
    if name is None:
        return ""
    spaced = _OUTSIDE_NAME_ALPHABET.sub(" ", ascii_upper(name).replace("&", " AND "))
    tokens = [token for token in spaced.split(" ") if token]
    dropped = True
    while dropped and tokens:
        dropped = False
        for suffix in _LEGAL_SUFFIXES:
            if tuple(tokens[-len(suffix) :]) == suffix and len(tokens) >= len(suffix):
                tokens = tokens[: -len(suffix)]
                dropped = True
                break
    return " ".join(tokens)


def lender_names_match(first: str | None, second: str | None) -> bool:
    """Equal normalised forms that are not empty."""
    left = normalise_lender_name(first)
    return bool(left) and left == normalise_lender_name(second)
