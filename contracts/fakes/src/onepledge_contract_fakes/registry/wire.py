"""Request models of the registry double, equal to the schemas of `registry.yaml` (TEST DOUBLE)."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_core import PydanticCustomError

from onepledge_contract_fakes.bounds import INT_MAX, MAX_TEXT
from onepledge_contract_fakes.registry.model import (
    FIELD_KINDS,
    Position,
    Submission,
    SubmissionItem,
    SubmissionKind,
)
from onepledge_contract_fakes.validation import CANONICAL_UUID_PATTERN, Issue, parse_integer_text

CODE_PATTERN = r"^[0-9a-f]{64}$"
KEY_VERSION_PATTERN = r"^[1-9][0-9]*\.[1-9][0-9]*$"
MAX_ITEMS = 500
SCHEMA_VERSION = 1

type Token = Annotated[str, Field(pattern=CODE_PATTERN)]


class FieldTokensModel(BaseModel):
    """`FieldTokens`: the seven field codes of a loan."""

    model_config = ConfigDict(extra="forbid", strict=True)

    VIN: Token
    BORROWER: Token
    AMOUNT_EXACT: Token
    AMOUNT_100: Token
    AMOUNT_1000: Token
    DATE_DAY: Token
    DATE_MONTH: Token


class SubmissionItemModel(BaseModel):
    """`SubmissionItem`."""

    model_config = ConfigDict(extra="forbid", strict=True)

    item_ref: Annotated[str, Field(pattern=CANONICAL_UUID_PATTERN)]
    asset_token: Token
    field_tokens: FieldTokensModel | None = None


class SubmissionRequestModel(BaseModel):
    """`SubmissionRequest`."""

    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: int
    kind: Literal["BACK_BOOK", "PLEDGE", "RELEASE"]
    key_version: Annotated[str, Field(pattern=KEY_VERSION_PATTERN, max_length=MAX_TEXT)]
    items: Annotated[list[SubmissionItemModel], Field(min_length=1, max_length=MAX_ITEMS)]

    @field_validator("key_version")
    @classmethod
    def _key_version_parts_fit_an_int_column(cls, value: str) -> str:
        """Each of the two key versions is stored in an `int` column (SPEC_DECISIONS 100)."""
        if any(isinstance(parse_integer_text(part, 1, INT_MAX), Issue) for part in value.split(".")):
            raise PydanticCustomError("less_than_equal", "a key version is out of range")
        return value

    @field_validator("schema_version")
    @classmethod
    def _schema_version_is_one(cls, value: int) -> int:
        """`const: 1` in registry.yaml. The strict model already refused 1.0, true and "1"."""
        if value != SCHEMA_VERSION:
            raise ValueError("schema_version")
        return value

    def to_submission(self) -> Submission:
        """The domain value of the request."""
        items = tuple(
            SubmissionItem(
                item_ref=item.item_ref,
                asset_token=item.asset_token,
                field_tokens=(
                    {kind: getattr(item.field_tokens, kind) for kind in FIELD_KINDS}
                    if item.field_tokens is not None
                    else None
                ),
            )
            for item in self.items
        )
        return Submission(kind=SubmissionKind(self.kind), key_version=self.key_version, items=items)


class CasePositionRequestModel(BaseModel):
    """`CasePositionRequest`."""

    model_config = ConfigDict(extra="forbid", strict=True)

    position: Literal["DATA_ERROR", "CONFIRMED_DOUBLE_PLEDGE"]

    def to_position(self) -> Position:
        """The domain value of the position."""
        return Position(self.position)
