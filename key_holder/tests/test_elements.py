"""The element rule and the daily window (domain, no I/O)."""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta, timezone

import oprf
import pytest
from hypothesis import given
from hypothesis import strategies as st
from onepledge_key_holder.domain.credentials import api_key_sha256
from onepledge_key_holder.domain.daily_window import seconds_until_reset, window_start
from onepledge_key_holder.domain.elements import decode_element, encode_element, is_usable_scalar

pytestmark = pytest.mark.unit

VALID = oprf.data.hash(b"loan").to_base64()


def test_a_canonical_element_decodes_to_its_32_bytes() -> None:
    raw = decode_element(VALID)
    assert raw == bytes(oprf.data.hash(b"loan"))
    assert raw is not None and encode_element(raw) == VALID


def test_the_identity_is_refused() -> None:
    assert decode_element(base64.b64encode(bytes(32)).decode()) is None


def non_canonical(element: str) -> str:
    """The same 32 bytes spelled with non-zero padding bits: still 43 characters and `=`."""
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
    return element[:42] + alphabet[alphabet.index(element[42]) + 1] + "="


def test_non_canonical_base64_is_refused() -> None:
    spelled = non_canonical(VALID)
    assert spelled != VALID
    assert base64.b64decode(spelled) == base64.b64decode(VALID)
    assert decode_element(spelled) is None


@pytest.mark.parametrize(
    "text",
    [
        "",
        "====",
        "not base64 at all",
        base64.b64encode(bytes(31)).decode(),
        base64.b64encode(bytes([1]) * 33).decode(),
        VALID[:-1],
        VALID.replace("=", ""),
        VALID + "\n",
        " " + VALID,
        base64.urlsafe_b64encode(bytes([251]) * 32).decode(),
        "é" * 44,
    ],
)
def test_anything_that_is_not_44_characters_of_standard_base64_is_refused(text: str) -> None:
    assert decode_element(text) is None


@given(st.binary(min_size=32, max_size=32).filter(lambda raw: raw != bytes(32)))
def test_property_every_non_zero_32_bytes_round_trips_in_one_spelling(raw: bytes) -> None:
    text = encode_element(raw)
    assert len(text) == 44
    assert decode_element(text) == raw


@pytest.mark.parametrize(
    ("scalar", "usable"),
    [(bytes(32), False), (b"short", False), (bytes(33), False), (bytes([1]) + bytes(31), True)],
)
def test_a_scalar_is_32_bytes_and_not_all_zeros(scalar: bytes, usable: bool) -> None:
    assert is_usable_scalar(scalar) is usable


def test_the_window_is_the_utc_day_whatever_the_offset_of_the_clock() -> None:
    late = datetime(2026, 11, 16, 23, 30, tzinfo=timezone(timedelta(hours=-5)))
    assert window_start(late) == datetime(2026, 11, 17, 0, 0, tzinfo=UTC)
    assert seconds_until_reset(late) == 19 * 3600 + 30 * 60


@pytest.mark.parametrize(
    ("now", "seconds"),
    [
        (datetime(2026, 11, 16, 12, 0, tzinfo=UTC), 43_200),
        (datetime(2026, 11, 16, 23, 59, 59, tzinfo=UTC), 1),
        (datetime(2026, 11, 16, 23, 59, 59, 900_000, tzinfo=UTC), 1),
        (datetime(2026, 11, 16, 0, 0, tzinfo=UTC), 86_400),
    ],
)
def test_retry_after_is_the_whole_seconds_to_midnight_and_at_least_one(now: datetime, seconds: int) -> None:
    assert seconds_until_reset(now) == seconds


def test_an_api_key_is_kept_as_its_sha256() -> None:
    assert api_key_sha256("abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
