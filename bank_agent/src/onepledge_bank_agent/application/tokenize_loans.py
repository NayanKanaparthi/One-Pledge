"""`TokenizeLoans`: the TOKENIZED stage (SPEC 9.3, 9.4, 13.3).

The loans of the submission's accepted items that are still PENDING get their eight
codes. Per chunk of loans: build the eight inputs of each loan, blind every one with a
fresh scalar, send the blinded elements to key holder 1 and its answer to key holder 2,
check both key versions, unblind, and store the eight tokens per loan in one
transaction. No transaction is open during the calls. Loans that already hold all
eight tokens at the submission's key version are skipped, and the inserts ignore rows
that exist, so a resumed stage never stores a token twice; blinds lived only in memory
and are drawn again. A RELEASE submission reuses stored codes and only advances.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from onepledge_bank_agent.application.audit_entries import stage_advanced
from onepledge_bank_agent.application.events import LogEvent
from onepledge_bank_agent.application.faults import FaultPoint
from onepledge_bank_agent.application.ports import UnitOfWorkFactory
from onepledge_bank_agent.application.registry_ports import Blinded, Blinder, KeyHolderClient
from onepledge_bank_agent.application.runtime import Runtime
from onepledge_bank_agent.domain.dependency_errors import KeyHolderProtocolError, KeyVersionChangedError
from onepledge_bank_agent.domain.enums import LoanState, TapeKind, TapeStatus, TokenKind
from onepledge_bank_agent.domain.errors import FacilityNotFoundError
from onepledge_bank_agent.domain.loan import Loan
from onepledge_bank_agent.domain.tokenization import (
    TOKENS_PER_LOAN,
    TokenInput,
    token_from_point,
    token_inputs,
)
from onepledge_bank_agent.domain.values import KeyVersion, Lei, Sha256Hex, Token


@dataclass(frozen=True)
class KeyHolders:
    """The two key holders, in the order the elements pass through them."""

    first: KeyHolderClient
    second: KeyHolderClient


def complete_version(tokens: Sequence[Token]) -> KeyVersion | None:
    """The key version at which a loan holds all eight kinds, if any."""
    by_version: dict[KeyVersion, set[TokenKind]] = {}
    for token in tokens:
        by_version.setdefault(token.key_version, set()).add(token.kind)
    complete = [version for version, kinds in by_version.items() if len(kinds) == TOKENS_PER_LOAN]
    return max(complete) if complete else None


class TokenizeLoans:
    """Runs the tokenise stage of one CHECKED submission."""

    reached = TapeStatus.TOKENIZED

    def __init__(
        self,
        units: UnitOfWorkFactory,
        holders: KeyHolders,
        blinder: Blinder,
        runtime: Runtime,
        chunk_size: int,
    ) -> None:
        self._units = units
        self._holders = holders
        self._blinder = blinder
        self._clock = runtime.clock
        self._faults = runtime.faults
        self._log = runtime.log
        self._chunk_size = chunk_size

    def run(self, submission_id: uuid.UUID) -> None:
        """Tokenise every eligible loan of the submission, then mark it TOKENIZED."""
        with self._units() as unit:
            tape = unit.tapes.get(submission_id)
            if tape is None or tape.status is not TapeStatus.CHECKED:
                return
            facility = unit.facilities.get(tape.facts.facility_id)
            items = [] if tape.facts.kind is TapeKind.RELEASE else unit.items.accepted(submission_id)
            loans = unit.loans.get_many(item.loan_id for item in items)
            stored = unit.tokens.for_loans(loans)
        if facility is None:
            raise FacilityNotFoundError
        eligible = [loans[i.loan_id] for i in items if loans[i.loan_id].state is LoanState.PENDING]
        versions = {complete_version(stored.get(loan.loan_id, ())) for loan in eligible} - {None}
        if len(versions) > 1:
            raise KeyVersionChangedError(self._holders.first.dependency)
        version = next(iter(versions), None)
        to_do = [loan for loan in eligible if complete_version(stored.get(loan.loan_id, ())) is None]
        for index, start in enumerate(range(0, len(to_do), self._chunk_size)):
            chunk = to_do[start : start + self._chunk_size]
            version = self._tokenize_chunk(submission_id, chunk, facility.lender.lei, version, index)
        self._finish(submission_id, len(to_do), len(eligible) - len(to_do))

    def _tokenize_chunk(
        self,
        submission_id: uuid.UUID,
        loans: Sequence[Loan],
        lei: Lei,
        version: KeyVersion | None,
        batch_index: int,
    ) -> KeyVersion:
        """One key holder round for a chunk of loans; returns the key version pair it used."""
        inputs = [(loan.loan_id, entry) for loan in loans for entry in token_inputs(loan.terms, lei)]
        blinded = [self._blinder.blind(token_input.oprf_input()) for _, token_input in inputs]
        first = self._holders.first.evaluate([element.element for element in blinded])
        second = self._holders.second.evaluate(first.elements)
        pair = KeyVersion(first.key_version, second.key_version)
        if version is not None and pair != version:
            changed = self._holders.first if pair.holder_1 != version.holder_1 else self._holders.second
            raise KeyVersionChangedError(changed.dependency)
        self._faults.reached(FaultPoint.AFTER_KEY_HOLDER_RESPONSE)
        tokens = self._tokens(inputs, blinded, second.elements, pair)
        with self._units() as unit:
            unit.tokens.add_many(tokens, self._clock.now())
            unit.commit()
        self._log.info(
            LogEvent.CODES_STORED,
            submission_id=str(submission_id),
            batch_index=batch_index,
            count=len(tokens),
        )
        self._faults.reached(FaultPoint.AFTER_CODES_STORED)
        return pair

    def _tokens(
        self,
        inputs: Sequence[tuple[uuid.UUID, TokenInput]],
        blinded: Sequence[Blinded],
        evaluated: Sequence[str],
        pair: KeyVersion,
    ) -> Mapping[uuid.UUID, list[Token]]:
        """Unblind every element and derive the token of its kind, grouped by loan."""
        tokens: dict[uuid.UUID, list[Token]] = {}
        for (loan_id, token_input), element, answer in zip(inputs, blinded, evaluated, strict=True):
            point = self._blinder.unblind(element, answer)
            if point is None:
                raise KeyHolderProtocolError(self._holders.second.dependency, None)
            value = Sha256Hex(token_from_point(token_input.kind, point))
            tokens.setdefault(loan_id, []).append(Token(token_input.kind, value, pair))
        return tokens

    def _finish(self, submission_id: uuid.UUID, tokenized: int, already: int) -> None:
        with self._units() as unit:
            tape = unit.tapes.get(submission_id, for_update=True)
            if tape is None or tape.status is not TapeStatus.CHECKED:
                return
            before = tape.mark_tokenized(self._clock.now())
            unit.tapes.save(tape)
            counts = {
                "loans_tokenized": tokenized,
                "tokens_stored": tokenized * TOKENS_PER_LOAN,
                "loans_already_tokenized": already,
            }
            unit.record(stage_advanced(submission_id, before, tape.status, counts))
            unit.commit()
