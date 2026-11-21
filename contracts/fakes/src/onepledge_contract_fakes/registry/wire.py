"""Request models of the registry double, equal to the schemas of `registry.yaml` (TEST DOUBLE)."""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from onepledge_contract_fakes.registry.model import (
    FIELD_KINDS,
    Position,
    Submission,
    SubmissionItem,
    SubmissionKind,
)

CODE_PATTERN = r"^[0-9a-f]{64}$"
KEY_VERSION_PATTERN = r"^[1-9][0-9]*\.[1-9][0-9]*$"
MAX_ITEMS = 500

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

    item_ref: uuid.UUID
    asset_token: Token
    field_tokens: FieldTokensModel | None = None


class SubmissionRequestModel(BaseModel):
    """`SubmissionRequest`."""

    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal[1]
    kind: Literal["BACK_BOOK", "PLEDGE", "RELEASE"]
    key_version: Annotated[str, Field(pattern=KEY_VERSION_PATTERN)]
    items: Annotated[list[SubmissionItemModel], Field(min_length=1, max_length=MAX_ITEMS)]

    def to_submission(self) -> Submission:
        """The domain value of the request."""
        items = tuple(
            SubmissionItem(
                item_ref=str(item.item_ref),
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
