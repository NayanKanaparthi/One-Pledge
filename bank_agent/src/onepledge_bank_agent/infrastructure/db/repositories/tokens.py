"""`SqlTokenRepository`: `loan_tokens` (insert-only)."""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from onepledge_bank_agent.domain.enums import TokenKind
from onepledge_bank_agent.domain.values import KeyVersion, Sha256Hex, Token
from onepledge_bank_agent.infrastructure.db.models import LoanTokenRow


class SqlTokenRepository:
    """The bank's private map from code to its own loan."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def loans_with_tokens(self, loan_ids: Iterable[uuid.UUID]) -> set[uuid.UUID]:
        """The loans that have at least one stored token, of any key version."""
        wanted = sorted(set(loan_ids))
        if not wanted:
            return set()
        statement = select(LoanTokenRow.loan_id).where(LoanTokenRow.loan_id.in_(wanted)).distinct()
        return set(self._session.execute(statement).scalars())

    def for_loans(self, loan_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, tuple[Token, ...]]:
        """Every token of these loans, of every key version, in kind order."""
        wanted = sorted(set(loan_ids))
        if not wanted:
            return {}
        statement = select(LoanTokenRow).where(LoanTokenRow.loan_id.in_(wanted))
        found: dict[uuid.UUID, list[Token]] = {}
        for row in self._session.execute(statement).scalars():
            token = Token(TokenKind(row.token_kind), Sha256Hex(row.token), KeyVersion.parse(row.key_version))
            found.setdefault(row.loan_id, []).append(token)
        order = {kind: index for index, kind in enumerate(TokenKind)}
        return {
            loan_id: tuple(sorted(tokens, key=lambda t: (t.key_version, order[t.kind])))
            for loan_id, tokens in found.items()
        }

    def add_many(self, tokens: Mapping[uuid.UUID, Sequence[Token]], at: datetime) -> None:
        """Insert tokens; an existing `(loan_id, token_kind, key_version)` row is left unchanged."""
        values = [
            {
                "loan_id": loan_id,
                "token_kind": token.kind.value,
                "key_version": str(token.key_version),
                "token": token.value.value,
                "created_at": at,
            }
            for loan_id, loan_tokens in tokens.items()
            for token in loan_tokens
        ]
        if values:
            self._session.execute(insert(LoanTokenRow).values(values).on_conflict_do_nothing())

    def loans_with_asset_token(self, asset_token: str) -> list[uuid.UUID]:
        """The loans whose ASSET token is this code (a released row and a new one may share it)."""
        statement = (
            select(LoanTokenRow.loan_id)
            .where(LoanTokenRow.token_kind == TokenKind.ASSET.value, LoanTokenRow.token == asset_token)
            .distinct()
        )
        return sorted(self._session.execute(statement).scalars())
