# OnePledge layer 2: product contract (SPEC)

Version 1.0, 2026-11-24. Owner: Shreyas (layer 2). Binding inputs: `contracts/key_holder.yaml`, `contracts/registry.yaml`, `docs/BUILD_PLAN.md`, `docs/layer2/CONTEXT.md`, `docs/layer2/SPEC_DECISIONS.md`.

Layer 2 is `key_holder/`, `registry/`, and `ledger_verifier/`.

How to read this file:

- "MUST", "MUST NOT" and plain present tense statements are requirements.
- Where this file and a contract YAML disagree on a wire format, the YAML wins and this file is wrong. Report it. Do not guess a third format.
- Where this file and `BUILD_PLAN.md` or the 23-page PDF disagree, `docs/layer2/SPEC_DECISIONS.md` wins.
- Release is decided only by review against `docs/layer2/RUBRIC.md`.
- No em dashes in code, docs or output.
- After each work package, stop. The next package starts only when Shreyas says so. See `WORK_PACKAGES.md`.

Contents: 1 Goals. 2 What already exists. 3 Packages. 4 Key holder. 5 Registry data. 6 Decision. 7 Certificates, checkpoints, outbox. 8 Ledger verifier. 9 Join to the bank. 10 Tests. 11 Known limitations.

---

## 1. Goals and non-goals

### 1.1 Goals

G1. Two real key holder processes answer `POST /v1/evaluate`. Each holds one secret scalar. Neither sees a raw loan value. Two holders in series produce the points the bank unblinds into the tokens in `contracts/examples/vectors/tokenization.json` when the scalars match that vector.

G2. One registry answers every path in `contracts/registry.yaml`. A submission is one database transaction: locks, conflicts, ledger append, certificate, checkpoint, outbox. A retry with the same idempotency key and the same body returns the stored response and does not lock again.

G3. Exactly one bank holds the active lock on an asset token. Two submissions at the same instant, repeated, still produce one lock.

G4. The ledger is insert-only. `ledger_verifier` recomputes the hash chain and the signatures without trusting the registry process. A forced edit fails verification at that sequence.

G5. A response, alert, case or log line never names another member or another member's tokens. A scan of the key holder databases and the registry database finds no VIN, name, amount or date.

G6. Nayan's bank agent works against these processes by changing URLs and keys only. His source tree stays as he wrote it, and his existing tests still pass against the doubles.

### 1.2 Non-goals

N1. Screens, Docker Compose for the whole demo, CI workflows, and the nine-scene script. Those are layer 3. Layer 2 ships Dockerfiles for its own processes.

N2. The bank pipeline, data generator, outside records, and scorecard. Those are layer 1.

N3. Editing `contracts/*.yaml` or `contracts/examples/` without all three owners.

N4. Key rotation as a migration of existing locks. `key_version` is stored and checked. `KEY_ROTATED` is a legal event type and is not produced by this version (same limit as layer 1).

N5. Sigstore Rekor. Decision 8.

N6. TLS between services. The demo runs without it. A real deployment needs TLS. Say so in the README.

N7. Replication, partitioning, a streaming platform.

---

## 2. What already exists

Layer 2 does not rebuild these.

| Piece | Where | What layer 2 does with it |
|---|---|---|
| Key holder contract | `contracts/key_holder.yaml` | Implement it |
| Registry contract | `contracts/registry.yaml` | Implement it |
| Examples and vectors | `contracts/examples/` | Test against them |
| Key holder double | `contracts/fakes/.../key_holder/double.py` | Oracle for evaluate |
| Registry double | `contracts/fakes/.../registry/` | Oracle for decisions, hashes, privacy |
| Bank caller | `TokenizeLoans`, `KeyHolderHttpClient`, `RegistryClient` | The client under test. Do not modify it |
| Service shape | `outside_records/` | Copy the package shape, roles, and Alembic layout |

`/healthz` on layer 2 returns the contract `Health` schema. It does not set `test_double` to true.

---

## 3. Packages and layout

```
key_holder/                 package onepledge_key_holder
  pyproject.toml  README.md  Dockerfile  alembic.ini  migrations/
  src/onepledge_key_holder/{domain,application,infrastructure,interface}/
  tests/
registry/                   package onepledge_registry
  pyproject.toml  README.md  Dockerfile  alembic.ini  migrations/
  src/onepledge_registry/{domain,application,infrastructure,interface}/
  tests/
ledger_verifier/            package onepledge_ledger_verifier
  pyproject.toml  README.md
  src/onepledge_ledger_verifier/
  tests/
docs/layer2/                CONTEXT SPEC WORK_PACKAGES RUBRIC SPEC_DECISIONS
```

Rules match `BUILD_PLAN.md` section 9 and the PDF class list for the shared service:

- Python 3.12+, type hints, mypy strict, ruff, pytest. Coverage at least 85% on `domain` plus `application` for `onepledge_key_holder` and `onepledge_registry`.
- Constructor injection. No globals for keys, clocks or repositories.
- Business errors are exception classes whose names end in `Error`.
- One installable package per folder, src layout, `py.typed`, console scripts below.
- Allowed runtime dependencies are the layer 1 set that this layer actually imports: fastapi, uvicorn, pydantic, sqlalchemy, alembic, psycopg, cryptography, oprf, structlog. Dev: pytest, pytest-cov, hypothesis, ruff, mypy. Nothing else without a new decision in `SPEC_DECISIONS.md`.
- Import boundary: none of the three packages imports `onepledge_bank_agent`, `onepledge_datagen`, `onepledge_outside_records`, `onepledge_evaluation`, or `onepledge_contract_fakes` from `src/`.

Application classes (PDF, layer 2 row), one class per use case:

| Package | Domain | Application | Infrastructure |
|---|---|---|---|
| Key holder | blinded element, key version | `EvaluateBlinded` | scalar from the environment, evaluation log, rate limit, credential lookup |
| Registry | `PledgeLock`, `LedgerEvent`, `AssetToken`, `ConflictCase`, `Certificate`, `Checkpoint`, `MatchRule` (`ExactAssetRule`, `FieldAgreementRule`) | `SubmitBatch`, `ReleaseLocks`, `OpenCase`, `ResolveCase`, `IssueCertificate`, `SignCheckpoint`, `DeliverOutbox` | `LedgerRepository`, `LockRepository`, `UnitOfWork`, `HashChain`, `Signer`, `KeyStore` |
| Verifier | chain check result | `VerifyLedger` | read-only ledger and signature check |

Console scripts:

```
onepledge-keyholder db provision
onepledge-keyholder db migrate
onepledge-keyholder serve
onepledge-registry db provision
onepledge-registry db migrate
onepledge-registry serve
onepledge-ledger-verifier verify
```

Two key holder processes run the same package. `HOLDER_ID` is `kh1` or `kh2`. Default ports are 8301 and 8302. The registry default port is 8400. Bind addresses follow the layer 1 rule: an IP literal, including `127.0.0.1` and `0.0.0.0`.

Secrets live in the environment and in `.env.example` as empty placeholders. Never commit a scalar, an API key, or a signing seed.

---

## 4. Key holder

### 4.1 Database, one per holder

| Table | Columns | Notes |
|---|---|---|
| key_versions | key_version, status, created_at | status active or retired-readable. The scalar is not a column |
| evaluation_log | eval_id, member_id, request_id, item_count, at | One row per accepted request. No elements |
| rate_limits | member_id, window_start, items_used | UTC day window. The cap counts elements |
| api_credentials | credential_id, member_id, key_sha256, active | Lookup by SHA-256 of `X-API-Key` |

Owner role migrates. App role is the role the server uses. The app role inserts into the log and the rate table and does not update `evaluation_log`.

### 4.2 Evaluate

`POST /v1/evaluate` implements `contracts/key_holder.yaml`.

- Authenticate `X-API-Key` by SHA-256. Missing or unknown key: 401 `UNAUTHENTICATED`.
- Body is `request_id`, optional `key_version`, and `elements`. The bank sends `key_version: null`, which means the active version.
- For each element, decode standard base64 to 32 bytes. Reject the identity (32 zero bytes). Compute `oprf.mask(scalar).mask(oprf.data(element_bytes))`. An element that is not a ristretto255 point, or an all-zero result, rejects the whole request with 422 `INVALID_ELEMENT`. `details` lists indexes only, never the values. No partial result is stored or returned.
- A pinned `key_version` that is not active or retired-readable: 409 `KEY_VERSION_UNAVAILABLE`.
- Over the daily element cap: 429 `RATE_LIMITED` and `Retry-After`.
- Response `elements[i]` is the evaluation of request `elements[i]`, same length. `holder_id` is `kh1` or `kh2`. `key_version` is the version used for every element.
- Evaluation is a pure function of `(key_version, element)`. A repeat returns the same elements.
- The holder does not unblind and does not hash the point into a token. The bank does both.

`GET /healthz` needs no key. Body: `status` ok, `service` `key_holder`, `holder_id`, `active_key_version`.

Startup refuses a scalar that is not 32 bytes or is all zeros.

---

## 5. Registry data

One PostgreSQL 16 database. Raw loan values are not a column, a payload field, or a log field.

### 5.1 Ledger

`ledger_events` is the source of truth. Columns are decision 5. Updates and deletes are refused by trigger and by privilege, including for the owner.

Event types: `MEMBER_JOINED`, `LOCK_ACQUIRED`, `LOCK_RELEASED`, `CONFLICT_DETECTED`, `CASE_OPENED`, `CASE_RESOLVED`, `CERTIFICATE_ISSUED`, `CHECKPOINT_SIGNED`, `KEY_ROTATED`.

On first provision, append one `MEMBER_JOINED` per configured member, in list order, then sign checkpoint 1. This matches the double, so a bank that has never submitted still has a chain to verify.

### 5.2 Projections

These tables are rebuilt by replaying `ledger_events` from seq 1. A rebuild command MUST reproduce them. They may be written inside the same transaction as the events so reads stay fast. They are not a second source of truth.

| Table | Role |
|---|---|
| members, api_credentials | Who may call, keys as SHA-256 |
| submissions | Unique `(member_id, idempotency_key)`. Stores the response bytes and the body hash |
| pledge_locks | Unique `asset_token` where state is `LOCKED` |
| lock_field_tokens | The eight kinds for a lock. Codes only |
| match_candidates | One row per overlap, with the kinds that agreed |
| conflict_cases | `OPEN`, `CLEARED_DATA_ERROR`, `CLEARED_LATE_RELEASE`, `CONFIRMED` |
| certificates | `as_of_seq`, `body_hash`, `signature` |
| checkpoints | `last_event_seq`, `head_hash`, `signature` |
| outbox | `ALERT`, `CASE_UPDATE`, `CHECKPOINT`. Per member, positions strictly increasing |
| access_log | Route template, status, credential, time. Decision 6 |

There is no `facility_ref`. Decision 4.

---

## 6. Decision

`POST /v1/submissions` is one transaction. Items run in request order.

Idempotency, from `registry.yaml`:

- First key: decide, store the response, status 201.
- Same key, same canonical body: return the stored bytes, status 200, header `Idempotent-Replay: true`. No new events.
- Same key, different body: 409 `IDEMPOTENCY_KEY_REUSED`.
- `key_version` other than the active pair: 409 `KEY_VERSION_MISMATCH`. The wire form is `<holder1>.<holder2>`, for example `1.1`.

Rules, copied from `registry.yaml` and `contracts/fakes/.../registry/matching.py`:

| Rule | When | Result | Ledger |
|---|---|---|---|
| A | This member holds the active lock on `asset_token` | `LOCKED`, `already_held` true | No event |
| B | Another member holds that asset token | `CONFLICT`, basis `ASSET`. `EXACT` when `BORROWER`, `AMOUNT_EXACT` and `DATE_DAY` all match, otherwise `LIKELY` | `CONFLICT_DETECTED` and `CASE_OPENED`, or reuse the open case for the same claimant and lock |
| C | Asset is free, same `BORROWER`, and one of `(AMOUNT_EXACT, DATE_DAY)`, `(AMOUNT_EXACT, DATE_MONTH)`, `(AMOUNT_100, DATE_DAY)` matches | `CONFLICT`, basis `FIELDS`, kind `LIKELY`. Most equal kinds wins. A tie goes to the oldest lock. A pair cleared `CLEARED_DATA_ERROR` is exempt | Same case events |
| Lock | No rule matched | `LOCKED`, `already_held` false | `LOCK_ACQUIRED` |
| Release | Caller holds the lock | `RELEASED`. Every open case on that lock becomes `CLEARED_LATE_RELEASE` | `LOCK_RELEASED`, `CASE_RESOLVED` |
| Release miss | Caller does not hold the lock | `REJECTED`, reason `NOT_HOLDER` | No lock change |

`kinds_matched` lists every kind whose value equals the matched lock, in the fixed kind order, including `ASSET` when the asset token matched.

The daily item cap counts `BACK_BOOK` and `PLEDGE` items only. A `RELEASE` is never refused for the cap.

After the item events, append one `CERTIFICATE_ISSUED`. `as_of_seq` is that event's seq. Then sign a checkpoint and append `CHECKPOINT_SIGNED`.

A duplicate `asset_token` inside one request is 422 with issue `DUPLICATE_ASSET_TOKEN`. `field_tokens` present on a `RELEASE`, or missing on a lock request, is 422.

Privacy: a member sees only its own tokens. Another member's submission, certificate, case or outbox message is 404, never 403.

---

## 7. Certificates, checkpoints, outbox

Canonical JSON, from `registry.yaml`: UTF-8, object keys sorted, separators `,` and `:`, no insignificant whitespace. In Python: `json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")`.

- Certificate `body_hash` is the SHA-256 hex of the canonical body. `signature` is Ed25519 over those bytes, standard base64.
- Checkpoint `signature` is Ed25519 over the canonical checkpoint body. `head_hash` equals `event_hash` of event `last_event_seq`.
- Ledger hashes are section 5.1.

Outbox fan-out matches the double:

- A new case queues one `ALERT` for the holder (holder's asset token, role `HOLDER`) and one for the claimant (claimant's asset token, role `CLAIMANT`).
- A resolution queues one `CASE_UPDATE` for each party.
- Every checkpoint queues one `CHECKPOINT` for every member.
- A repeated conflict while the case is `OPEN` reuses `case_id`, appends no case events, and queues no new alert.

`GET /v1/outbox`, `POST /v1/outbox/{id}/ack` (204, including when already acknowledged), `GET /v1/certificates/{id}`, `GET /v1/checkpoints/latest`, `GET /v1/submissions/{id}`, `GET /v1/cases`, `GET /v1/ledger/events`.

`GET /v1/ledger/events?view=full` requires the auditor key, otherwise 403. The member view omits other members' payloads.

Unknown route: 404 `NOT_FOUND` in the error envelope. Wrong method: 405 `METHOD_NOT_ALLOWED` with `Allow`. No framework default body reaches a caller.

The signing seed is an environment variable. The public key is what the bank puts in `REGISTRY_PUBLIC_KEYS`. Layer 2 does not write that variable into the bank's code. The README states the key id and how to export the public key.

---

## 8. Ledger verifier

`onepledge-ledger-verifier verify` reads the registry database as a reader, or reads an exported event list. It does not import the registry write path.

It checks:

- `seq` starts at 1 and has no gaps.
- `prev_hash` of seq 1 is 64 zeros, and each later `prev_hash` equals the previous `event_hash`.
- `payload_hash` and `event_hash` recompute under the canonical JSON rules.
- Each checkpoint signature verifies, and `head_hash` equals the named event.
- Each certificate `body_hash` matches the stored body, and the signature verifies.

Exit 0 when the chain holds. Exit non-zero and name the first broken seq when it does not.

An in-place change of one `payload_hash`, a checkpoint with one signature bit flipped, and a certificate body changed without re-signing each fail at that object. These are the tamper modes layer 1 uses for scene 9.

---

## 9. Join to the bank

Layer 2 does not edit `bank_agent/`. The bank is pointed at layer 2 by:

| Variable | Points at |
|---|---|
| `KEY_HOLDER_1_URL`, `KEY_HOLDER_1_API_KEY` | kh1 |
| `KEY_HOLDER_2_URL`, `KEY_HOLDER_2_API_KEY` | kh2 |
| `REGISTRY_URL`, `REGISTRY_API_KEY`, `REGISTRY_MEMBER_ID` | registry |
| `REGISTRY_PUBLIC_KEYS` | the registry signing public key |

The call order the bank already uses, which layer 2 must accept unchanged:

1. Blind each field locally.
2. `POST /v1/evaluate` on holder 1 with `key_version` null.
3. `POST /v1/evaluate` on holder 2 with holder 1's elements, unchanged.
4. Unblind and derive the 64-hex token in the bank.
5. `POST /v1/submissions` with header `Idempotency-Key`.
6. Fetch the certificate, poll the outbox, ack, and verify checkpoints.

Scenes layer 2 is responsible for, once the bank is pointed here: 2, 3, 4, 6, 7, 8, 9. Scenes 1 and 5 stay inside the bank. An invented loan with no title never reaches the registry.

---

## 10. Tests

Tests are written with the code of the package that introduces the behavior.

- Domain unit tests for matching rules, hash chain, and element rejection. No database.
- Repository tests against real PostgreSQL 16.
- Contract tests that load `contracts/examples/` and the two vectors.
- An oracle test may start the matching double and compare one request. It lives under `tests/` and is the only place a double is imported.
- Edge cases listed on the work package are tests, not a later pass.

Every package's done-check also runs the layer 1 command that package names (`make contracts-check`, and the bank or fake tests it lists), so his build stays green.

A package is also shown working. The command in that package's "Show it working" lines is run, and the hand-back explains the output: what arrived from the bank, what this package decided, and what was stored or returned. A green test suite with nothing running is not enough.

---

## 11. Known limitations

- Demo traffic has no TLS.
- `KEY_ROTATED` is reserved and unused.
- One primary database per service. No standby.
- The `oprf` package is small and unaudited. Both holders use version 5.0.0, the version layer 1 tested. A member can still attempt a small number of guesses, bounded by the daily cap and by the bank's local checks.
- The two-holder design has not been reviewed by a cryptographer. The PDF states this. Layer 2 does not claim otherwise.
