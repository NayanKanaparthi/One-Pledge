# OnePledge layer 2: release rubric (FIXED)

Version 1.0, 2026-11-24. This rubric is fixed. It is not edited to make a build pass. A change needs Shreyas's written approval and a new version number, recorded in `SPEC_DECISIONS.md`.

The specification is `docs/layer2/SPEC.md` together with `docs/layer2/SPEC_DECISIONS.md`. Contract files win on the wire.

## Rules of grading

1. Every criterion is mandatory and is graded PASS or FAIL. There is no partial credit.
2. A criterion passes only if the reviewer observes every statement in its pass condition. A sentence in a README is not evidence.
3. If a command cannot be run because the product lacks what it needs, the criterion fails.
4. The reviewer records PASS or FAIL, the commands run, and the decisive output. A FAIL states what was expected, what was observed, and where.
5. Layer 2 is released only when every criterion below passes in one review on one commit. After any fix, every criterion is graded again.
6. The reviewer does not modify product code.
7. Layer 1 files are part of the setup and are not part of the product under review. A review that only passes because `bank_agent/` was edited fails JOIN-03.

## Reviewer setup

Fresh clone. PostgreSQL 16 available. Follow `key_holder/README.md` and `registry/README.md` to provision, migrate and serve. Private database names so the review does not collide with a developer.

```
KH1=http://127.0.0.1:8301
KH2=http://127.0.0.1:8302
REG=http://127.0.0.1:8400
```

Use the API keys the README generated for two members, A and B, and for the auditor. `PSQL` is `psql` against the registry database as a superuser.

---

## 1. Key holder

### KH-01 Health identifies the holder and is not a double

- **Requirement.** Each process serves the contract health body.
- **Procedure.** `curl -s $KH1/healthz` and the same for `$KH2`.
- **Pass.** Both exit from curl 0. `status` is `ok`, `service` is `key_holder`, `holder_id` is `kh1` on the first and `kh2` on the second, `active_key_version` is an integer greater than or equal to 1. `test_double` is absent or false.

### KH-02 Same points as the double

- **Requirement.** With the same scalar, evaluate matches `KeyHolderDouble`.
- **Procedure.** Run the package test named in the key holder README for the oracle comparison, or the reviewer's own script that evaluates one blinded point on both and compares.
- **Pass.** The 44-character elements are equal. Order and length match the request.

### KH-03 A bad element rejects the whole request

- **Requirement.** SPEC section 4.2.
- **Procedure.** POST three elements, the middle one the identity (base64 of 32 zero bytes), with a valid API key.
- **Pass.** Status 422, code `INVALID_ELEMENT`, `details` include index 1 and do not include the submitted strings. `evaluation_log` row count is unchanged.

### KH-04 Cap, auth, and a pure repeat

- **Requirement.** The contract error codes and pure evaluation.
- **Procedure.** Call with no key. Call with a pinned `key_version` of 999999. Call evaluate twice with the same points. Exhaust the daily cap if the README exposes a low cap for review, otherwise run the package test that covers 429.
- **Pass.** 401 `UNAUTHENTICATED`, 409 `KEY_VERSION_UNAVAILABLE`, two identical 200 bodies, and 429 `RATE_LIMITED` with `Retry-After` greater than or equal to 1.

### KH-05 Nothing blinded is stored

- **Requirement.** Decision 12.
- **Procedure.** After KH-02, dump the key holder database text and search for the base64 element that was sent.
- **Pass.** No match. Columns present are the four tables in SPEC section 4.1 and no element column.

---

## 2. Tokens through the bank's math

### TK-01 Published vector

- **Requirement.** SPEC G1.
- **Procedure.** Run the WP3 test (`pytest` target named in `key_holder/README.md`).
- **Pass.** Exit 0. The eight token kinds match `contracts/examples/vectors/tokenization.json`. `git diff -- bank_agent` is empty.

---

## 3. Registry decisions

### REG-01 Idempotent replay

- **Requirement.** SPEC section 6.
- **Procedure.** POST a one-item pledge twice with the same `Idempotency-Key` and body. POST a third time with the same key and one byte changed in a token.
- **Pass.** First status 201. Second status 200, header `Idempotent-Replay: true`, body byte-identical to the first. Third status 409 `IDEMPOTENCY_KEY_REUSED`. Ledger event count does not grow on the second call.

### REG-02 Rules A, B and C

- **Requirement.** SPEC section 6 table.
- **Procedure.** Member A locks asset token T with field tokens F. Member A submits T again. Member B submits T with the same `BORROWER`, `AMOUNT_EXACT` and `DATE_DAY`. Member B submits a new asset token that shares `BORROWER` and the pair (`AMOUNT_EXACT`, `DATE_DAY`) and differs in `ASSET`.
- **Pass.** Second A result is `LOCKED` with `already_held` true and no new `LOCK_ACQUIRED`. B against T is `CONFLICT`, basis `ASSET`, kind `EXACT`. The field case is `CONFLICT`, basis `FIELDS`, kind `LIKELY`. One active lock on T.

### REG-03 Release

- **Requirement.** Release path in SPEC section 6.
- **Procedure.** A member who does not hold the lock submits `RELEASE`. The holder then submits `RELEASE`. The other member pledges the same asset token.
- **Pass.** Non-holder gets `REJECTED` and `NOT_HOLDER`. Holder gets `RELEASED`. The later pledge is `LOCKED` and creates no alert.

### REG-04 Race

- **Requirement.** SPEC G3. Release checklist item on simultaneous submits.
- **Procedure.** Run the package test that submits the same new asset token from A and from B concurrently, 100 times, with a fresh token each time.
- **Pass.** Each of the 100 rounds has exactly one `LOCKED` and one `CONFLICT`. The unique index on locked `asset_token` is present.

### REG-05 Ledger refuses edits

- **Requirement.** SPEC section 5.1.
- **Procedure.** As the owner role and as the app role, `UPDATE` one `ledger_events` row and `DELETE` one row.
- **Pass.** Both statements fail for both roles. `seq` values from 1 to max have no gaps.

---

## 4. Proof and privacy

### PR-01 Certificate and checkpoint verify

- **Requirement.** SPEC section 7.
- **Procedure.** Fetch the certificate and the latest checkpoint from REG-01. Verify Ed25519 with the published public key, and recompute `body_hash` and `head_hash`.
- **Pass.** Both signatures verify. `head_hash` equals `event_hash` of `last_event_seq`. `make contracts-check` exits 0.

### PR-02 Alerts name only the caller's token

- **Requirement.** SPEC section 7 fan-out.
- **Procedure.** After REG-02, `GET /v1/outbox` as A and as B.
- **Pass.** Each body contains that caller's asset token and does not contain the other member's id or the other member's asset token. Roles are `HOLDER` for A and `CLAIMANT` for B on the asset match.

### PR-03 Cross-member reads are 404

- **Requirement.** SPEC section 6 privacy rule.
- **Procedure.** As B, `GET` A's `submission_id` and A's `certificate_id`.
- **Pass.** Both are 404. Neither body says the resource exists.

### PR-04 Auditor ledger view

- **Requirement.** SPEC section 7.
- **Procedure.** `GET /v1/ledger/events?view=full` with A's key, then with the auditor key.
- **Pass.** A's key gets 403. The auditor key gets 200 and the event list includes seq 1.

### PR-05 No raw loan data in the databases

- **Requirement.** SPEC G5.
- **Procedure.** After a pledge whose preimage the reviewer knows (a VIN, a borrower reference, an amount in cents, a date), dump both key holder databases and the registry database to text and search for those four strings.
- **Pass.** No matches.

---

## 5. Verifier

### VER-01 Clean chain

- **Requirement.** SPEC section 8.
- **Procedure.** `onepledge-ledger-verifier verify` against the database used above.
- **Pass.** Exit 0.

### VER-02 Tamper is named

- **Requirement.** Scene 9, on a copy of the database.
- **Procedure.** Copy the database. Set one `payload_hash` to 64 zeros, bypassing the trigger only on the copy by a method the README documents for the reviewer (a superuser on the copy). Run the verifier. Restore is not required if the copy is disposable. Repeat on fresh copies for a flipped checkpoint signature bit and a certificate body with `items_locked` incremented and the signature left as it was.
- **Pass.** Each run exits non-zero and the output names the affected seq or certificate id. The original database still passes VER-01.

---

## 6. Join

### JOIN-01 The bank's own client accepts the services

- **Requirement.** SPEC section 9.
- **Procedure.** Point a bank agent, built from the unchanged layer 1 tree, at `KH1`, `KH2` and `REG`. Run one honest pledge and one conflicting pledge.
- **Pass.** The honest pledge reaches a stored certificate the bank marks verified. The conflict creates an alert at each bank, and neither alert payload contains the other bank's member id.

### JOIN-02 His tests still pass on his code

- **Requirement.** SPEC G6.
- **Procedure.** `git diff --stat -- bank_agent datagen outside_records evaluation contracts/fakes docs/layer1 contracts/key_holder.yaml contracts/registry.yaml`. Then `make test` from the README's layer 1 instructions, with the doubles, not with layer 2 URLs.
- **Pass.** The diff is empty. `make test` exits 0.

### JOIN-03 Edge error envelope

- **Requirement.** Unknown routes match the decision layer 1 already relies on.
- **Procedure.** `GET $REG/v1/no-such-route` and `DELETE $REG/v1/submissions`.
- **Pass.** 404 `NOT_FOUND` and 405 `METHOD_NOT_ALLOWED` with an `Allow` header. Both bodies use the contract error envelope.
