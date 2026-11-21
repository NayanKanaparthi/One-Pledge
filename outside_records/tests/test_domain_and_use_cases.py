"""Domain types and use cases with in-memory ports (no I/O)."""

from __future__ import annotations

import hashlib
import string
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from onepledge_outside_records.application.administration import RegisterClient, SeedRecords
from onepledge_outside_records.application.lookups import (
    AuthenticateClient,
    GetContracts,
    GetTitle,
    ListPayments,
)
from onepledge_outside_records.application.ports import RecordSet
from onepledge_outside_records.domain.errors import (
    MESSAGES,
    ContractNotFoundError,
    DomainError,
    InvalidVinFormatError,
    OperatorError,
    TitleNotFoundError,
    UnauthenticatedError,
    ValidationFailedError,
)
from onepledge_outside_records.domain.records import (
    ApiClient,
    Contract,
    Payment,
    PaymentPage,
    PaymentQuery,
    RecordCounts,
    TitleRecord,
    VinText,
)

pytestmark = pytest.mark.unit

VIN_ALPHABET = "ABCDEFGHJKLMNPRSTUVWXYZ0123456789"
VIN = "1HGCM82633A004352"
KEY = "opk_" + "A" * 43


@dataclass
class MemoryReader:
    """A RecordsReader over plain lists."""

    titles: dict[str, TitleRecord] = field(default_factory=dict)
    contracts_by_vin: dict[str, tuple[Contract, ...]] = field(default_factory=dict)
    clients: dict[str, ApiClient] = field(default_factory=dict)
    queries: list[PaymentQuery] = field(default_factory=list)

    def title(self, vin: VinText) -> TitleRecord | None:
        return self.titles.get(vin.value)

    def payments(self, query: PaymentQuery) -> PaymentPage:
        self.queries.append(query)
        return PaymentPage((), 0, query.limit, query.offset)

    def contracts(self, vin: VinText) -> tuple[Contract, ...]:
        return self.contracts_by_vin.get(vin.value, ())

    def active_client(self, api_key_sha256: str) -> ApiClient | None:
        return self.clients.get(api_key_sha256)


@dataclass
class MemoryRegistry:
    """A ClientRegistry that records what it was asked to store."""

    stored: list[tuple[str, str]] = field(default_factory=list)

    def upsert(self, name: str, api_key_sha256: str) -> ApiClient:
        self.stored.append((name, api_key_sha256))
        return ApiClient("0b0f6a3c-9a7e-4bb4-8a43-6a3f1b1f7a10", name)


@dataclass
class MemorySeed:
    """A RecordsSource and RecordsWriter pair."""

    records: RecordSet
    written: list[RecordSet] = field(default_factory=list)

    def load(self, data_dir: Path) -> RecordSet:
        return self.records

    def replace_all(self, records: RecordSet) -> RecordCounts:
        self.written.append(records)
        return RecordCounts(len(records.titles), len(records.payments), len(records.contracts))


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


@given(st.text(alphabet=VIN_ALPHABET, min_size=17, max_size=17))
def test_any_seventeen_characters_of_the_alphabet_parse(text: str) -> None:
    assert VinText.parse(text).value == text


@given(
    st.text(alphabet=VIN_ALPHABET, min_size=16, max_size=16),
    st.integers(min_value=0, max_value=16),
    st.sampled_from("IOQioqa-_ é١１\n"),
)
def test_one_character_outside_the_alphabet_is_refused(base: str, position: int, bad: str) -> None:
    text = base[:position] + bad + base[position:]
    with pytest.raises(InvalidVinFormatError):
        VinText.parse(text)


@pytest.mark.parametrize("text", ["", VIN[:16], VIN + "1", VIN.lower(), f" {VIN}", f"{VIN}\n"])
def test_wrong_lengths_case_and_whitespace_are_refused(text: str) -> None:
    with pytest.raises(InvalidVinFormatError) as raised:
        VinText.parse(text)
    assert raised.value.details[0].field == "vin"
    assert raised.value.details[0].issue == "PATTERN_MISMATCH"


def test_the_check_digit_is_not_verified() -> None:
    assert VinText.parse("1HGCM82633A004353").value == "1HGCM82633A004353"


def test_a_vin_never_appears_in_a_repr_or_an_error() -> None:
    assert VIN not in repr(VinText.parse(VIN))
    with pytest.raises(InvalidVinFormatError) as raised:
        VinText.parse("1HGCM82633A00435Q")
    assert "1HGCM82633A00435Q" not in f"{raised.value!s} {raised.value.args!r} {raised.value.message}"


def test_every_error_code_has_one_fixed_sentence() -> None:
    errors: list[DomainError] = [
        DomainError(),
        UnauthenticatedError(),
        ValidationFailedError(),
        InvalidVinFormatError(),
        TitleNotFoundError(),
        ContractNotFoundError(),
    ]
    for error in errors:
        assert error.message == MESSAGES[error.code]
        assert error.message.endswith(".")
    assert {e.http_status for e in errors} == {500, 401, 422, 404}


def test_authentication_needs_a_known_active_key() -> None:
    reader = MemoryReader(clients={sha256(KEY): ApiClient("id-1", "harbor")})
    authenticate = AuthenticateClient(reader)
    assert authenticate.run(KEY).name == "harbor"
    for presented in (None, "", KEY + "x", KEY.lower()):
        with pytest.raises(UnauthenticatedError):
            authenticate.run(presented)


def test_lookups_return_records_or_raise_not_found() -> None:
    title = TitleRecord(VIN, "NJ", "CLEAN", None, None)
    contract = Contract("c-1", VIN, "Summit Auto Finance LLC", 100, date(2025, 1, 2), "a" * 64)
    reader = MemoryReader(titles={VIN: title}, contracts_by_vin={VIN: (contract,)})
    vin, other = VinText.parse(VIN), VinText.parse("2HGCM82633A004352")
    assert GetTitle(reader).run(vin) == title
    assert GetContracts(reader).run(vin) == (contract,)
    with pytest.raises(TitleNotFoundError):
        GetTitle(reader).run(other)
    with pytest.raises(ContractNotFoundError):
        GetContracts(reader).run(other)
    query = PaymentQuery(vin, None, None)
    assert ListPayments(reader).run(query).items == ()
    assert reader.queries == [query]
    assert (query.limit, query.offset) == (50, 0)


def test_register_client_stores_only_the_hash_of_the_key() -> None:
    registry = MemoryRegistry()
    client = RegisterClient(registry).run("harbor", KEY)
    assert client.name == "harbor"
    assert registry.stored == [("harbor", sha256(KEY))]
    assert KEY not in repr(registry.stored)


@pytest.mark.parametrize("name", ["harbor", "h", "h" * 64, "-harbor", "bank_1-B", "_"])
def test_register_client_accepts_names_of_the_log_shape(name: str) -> None:
    assert RegisterClient(MemoryRegistry()).run(name, KEY).name == name


@pytest.mark.parametrize(
    "name", ["", "har.bor", "har bor", "h" * 65, "harbor\n", "hârbor", "har/bor", "{harbor}", "h:1"]
)
def test_register_client_refuses_bad_names(name: str) -> None:
    with pytest.raises(OperatorError, match="client name"):
        RegisterClient(MemoryRegistry()).run(name, KEY)


@pytest.mark.parametrize(
    "key", ["", "opk_short", "opk_" + "A" * 44, "abc_" + "A" * 43, "opk_" + "A" * 42 + "=", KEY + "\n"]
)
def test_register_client_refuses_keys_in_another_format(key: str) -> None:
    registry = MemoryRegistry()
    with pytest.raises(OperatorError, match="opk_") as raised:
        RegisterClient(registry).run("harbor", key)
    assert registry.stored == []
    if key:
        assert key not in raised.value.sentence


def test_seed_records_loads_and_replaces() -> None:
    records = RecordSet(
        (TitleRecord(VIN, "NJ", "CLEAN", None, None),),
        (Payment("p-1", VIN, date(2025, 1, 2), 100, "HARBOR"),),
        (),
    )
    seed = MemorySeed(records)
    assert SeedRecords(seed, seed).run(Path("unused")) == RecordCounts(1, 1, 0)
    assert seed.written == [records]


def test_the_vin_alphabet_excludes_exactly_i_o_and_q() -> None:
    assert set(string.ascii_uppercase + string.digits) - set(VIN_ALPHABET) == {"I", "O", "Q"}
