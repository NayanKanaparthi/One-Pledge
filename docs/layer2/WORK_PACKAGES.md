# OnePledge layer 2: work packages

Version 1.0, 2026-11-24. Ten packages, in order. Each is one implementer run. Each is tested against Nayan's existing build before the next one starts.

These packages are Shreyas's column of the PDF phase table, split into steps that match the code already on `main`.

| PDF phase | Days | Shreyas's column in the PDF | Packages |
|---|---|---|---|
| 0 Contracts | 1 | Table definitions for the registry and key holders. The two contract files | WP0. The YAML files already exist as drafts |
| 1 Build alone | 2 to 5 | Both key holders. Submissions with locks. The ledger and its hash chain. Safe retries | WP1 to WP5 |
| 2 Join | 6 to 7 | Conflicts, cases, the outbox, certificates | WP6, then the URL join is proved again in WP9 |
| 3 Harden | 8 to 10 | Signed checkpoints and the verifier. Likely-match. Race test and tamper test. Case resolution | Rule C is inside WP5 (decision 7). WP7 and WP8 finish this column |
| 4 Rehearse | 11 to 12 | Shreyas runs Vedant's checklist items | Not a layer 2 package |

## Rules for every package

1. Read `docs/layer2/CONTEXT.md`, `SPEC.md`, `SPEC_DECISIONS.md`, and this file before editing.
2. `SPEC.md` plus `SPEC_DECISIONS.md` is the contract. A gap that affects correctness or security stops the package. Report it. Do not guess, and do not edit the SPEC to fit the code.
3. Do not change `contracts/*.yaml`, `contracts/examples/`, or any file under `bank_agent/`, `datagen/`, `outside_records/`, `evaluation/`, `contracts/fakes/`, or `docs/layer1/`.
4. No stubs, no `TODO`, no `NotImplementedError` outside abstract methods, no test-mode branch in product code, no em dashes.
5. Work only in the folders the package lists. Shared files (`Makefile`, root `pyproject.toml`) change only when the package says so.
6. The edge cases in the package are part of the package, not a follow-up. Tests are written with the code.
7. A package is not done until its done-check passes and the "Show it working" command has been run. Tests alone are not the demonstration. The hand-back includes the command output and a short explanation of how this package works with Nayan's code: what arrived, what the new code decided, what was stored or returned.
8. Then stop. The next package starts only after Shreyas says to build the next one.
9. The implementer does not declare the layer complete. The rubric is graded later, against one commit.

---

## WP0. Accept the draft contracts

**Goal.** Confirm the wire format layer 2 will implement, and record any mismatch with the bank client before any service code.

**Depends on.** Nothing.

**Create.** No product code. Notes, if any, go in `SPEC_DECISIONS.md` as the next numbered entry.

**Test against Nayan.** `make contracts-check`. Read `KeyHolderHttpClient.evaluate` (it sends `key_version: null`) and `RegistryClient` (it sends `Idempotency-Key`).

**Edge cases to account for before coding.** Null `key_version`. A UUID that is not 36 lower-case hex characters with hyphens. Duplicate `X-API-Key` or `Idempotency-Key`. Error codes the bank already classifies: 401, 409, 422, 429, 503.

**Done-check.**

```
make contracts-check
```

Exit 0. A short note lists zero open wire mismatches, or one new decision entry per mismatch.

**Show it working.** Paste the `contracts-check: OK` line. Explain, from his client source, the one request the bank will send each holder and the one request it will send the registry. No service is running yet. That is the point of WP0: the wire is confirmed before a process exists.

**Stop.** Ask whether to build WP1.

---

## WP1. Three packages, same shape as outside_records

**Goal.** Installable packages with the layer split, tooling, and an architecture test. No business behavior yet beyond `/healthz` skeletons that identify the service.

**Depends on.** WP0 accepted.

**Create.**

- `key_holder/`, `registry/`, `ledger_verifier/` as SPEC section 3.
- Makefile targets extended so `make lint` and `make typecheck` include the new trees. Do not weaken the layer 1 gates.

**Test against Nayan.** `make lint` and `make typecheck` still pass on his packages. An architecture test fails if a layer 2 `domain` module imports SQLAlchemy or FastAPI, and if `src/` imports `onepledge_contract_fakes` or `onepledge_bank_agent`.

**Edge cases.** A committed `.env` fails the existing pre-commit hook. An em dash in a new file fails that hook.

**Done-check.**

```
make lint && make typecheck
python3 -m pytest key_holder/tests registry/tests ledger_verifier/tests -q
```

**Show it working.** Serve each process and `curl` its `/healthz`. The explanation says which process is kh1, which is kh2, and that the bank is not calling them yet. Health is the proof the packages boot.

**Stop.** Ask whether to build WP2.

---

## WP2. Key holder evaluate

**Goal.** Two processes, kh1 on port 8301 and kh2 on 8302, each with its own database and its own scalar, implementing SPEC section 4.

**Depends on.** WP1.

**Create.** `key_holder/` schema, `EvaluateBlinded`, HTTP routes, CLI.

**Test against Nayan.** Start both holders. Send the JSON body `KeyHolderHttpClient` builds. With the same scalar, the response elements match `KeyHolderDouble` byte for byte. His fake tests are not modified and still pass.

**Edge cases.** Identity point. One bad element in a batch of good ones. Non-canonical base64. Unknown pinned `key_version` (409). Missing key and duplicate `X-API-Key` (401). Daily cap with `Retry-After` (429). More than 1000 elements (413 or 422 as the YAML says). A second call with the same points returns the same elements. The evaluation log has a row and no element bytes.

**Done-check.** The package tests above, plus:

```
python3 -m pytest contracts/fakes/tests/test_key_holder_double.py contracts/fakes/tests/test_key_holder_http.py -q
```

**Show it working.** `curl` one `POST /v1/evaluate` at kh1 with a blinded point, then `curl` kh2 with kh1's returned element. The explanation walks those two calls the way `TokenizeLoans` does: the bank blinds, holder 1 multiplies by its scalar, holder 2 multiplies by its scalar, and the bank unblinds. Show the evaluation log row and that the element text is not in it.

**Stop.** Ask whether to build WP3.

---

## WP3. Bank tokens match the published vector

**Goal.** Prove the bank's tokenize stage accepts the real holders.

**Depends on.** WP2.

**Create.** A test under `key_holder/tests/` that runs the bank's blind, evaluate, unblind, hash path against the two real holders. No file under `bank_agent/` changes. If the test cannot import the bank (SPEC forbids that import from `src/`, and a test may), it shells out the same operations the bank does and compares to `contracts/examples/vectors/tokenization.json`. Prefer calling the public functions the bank already uses from the test only.

**Test against Nayan.** Eight kinds per loan match the vector: `ASSET`, `VIN`, `BORROWER`, `AMOUNT_EXACT`, `AMOUNT_100`, `AMOUNT_1000`, `DATE_DAY`, `DATE_MONTH`.

**Edge cases.** Holder 2 receives holder 1's elements unchanged. The active version stays stable across the run, because a change is the bank's `KeyVersionChangedError`. An element that unblinds to the identity is the bank's `KEY_HOLDER_REJECTED`, so the holder must not return 32 zero bytes for a valid input.

**Done-check.** WP2 tests still pass, and the vector comparison exits 0.

**Show it working.** Print the eight token hex strings next to the vector. The explanation states that the tokens match because both holders used the vector's scalars, and that `bank_agent/` was not edited. The bank's own math produced the codes.

**Stop.** Ask whether to build WP4.

---

## WP4. Registry database, ledger is the truth

**Goal.** Schema, roles, insert-only ledger, empty projection, rebuild command. No submission HTTP yet.

**Depends on.** WP3.

**Create.** `registry/` migrations for SPEC section 5. `MEMBER_JOINED` events and checkpoint 1 on provision.

**Test against Nayan.** His bank migrations are untouched: the bank test suite named below still passes. Hash bytes for a hand-built event match the canonical JSON paragraph in `registry.yaml`.

**Edge cases.** `UPDATE` and `DELETE` on `ledger_events` fail as the owner and as the app role. A gap in `seq` fails the rebuild check. `prev_hash` of seq 1 other than 64 zeros fails. A `payload_hash` that does not match `payload` fails.

**Done-check.**

```
python3 -m pytest registry/tests -q
python3 -m pytest bank_agent/tests/unit -q --tb=no
```

The bank unit run is the regression check. A failure there is a layer 2 defect only if this package edited the bank. Otherwise stop and report it as his tree.

**Show it working.** Run `onepledge-registry db migrate` against an empty database, then print `seq`, `event_type` and `event_hash` for the `MEMBER_JOINED` rows and checkpoint 1. Attempt one `UPDATE` and show it failing. The explanation says the ledger is the only history, and the bank has not submitted a loan yet.

**Stop.** Ask whether to build WP5.

---

## WP5. One transaction: decide, lock, append

**Goal.** `POST /v1/submissions` per SPEC section 6, including Rules A, B and C, release, and idempotency.

**Depends on.** WP4.

**Create.** `SubmitBatch`, `ReleaseLocks`, `OpenCase`, matching rules, the transaction.

**Test against Nayan.** Submit `contracts/examples/registry/submission.pledge.request.json` and the release example. Decisions match the registry double for those bodies. His double tests stay green.

**Edge cases.** Idempotent replay returns stored bytes and `Idempotent-Replay: true`. Same key, different body, is 409. Duplicate asset token in one request is 422. Rule A. Exact Rule B. Likely Rule B (amount or date changed). Rule C, including a tie that goes to the oldest lock, and a `CLEARED_DATA_ERROR` exemption. Release by a non-holder. Two concurrent submits of one asset token: one lock. A `RELEASE` under a full daily cap still proceeds. `KEY_VERSION_MISMATCH`.

**Done-check.** Package tests, plus the registry double tests:

```
python3 -m pytest contracts/fakes/tests/test_registry_rules.py contracts/fakes/tests/test_registry_submissions.py -q
```

**Show it working.** `curl` a pledge as member A, then the same asset token as member B. Print both item results and the new ledger rows. The explanation names the rule that fired (A, B, C, or a new lock), why the second call did not create a second lock, and that a repeated `curl` with the same `Idempotency-Key` returned the stored body.

**Stop.** Ask whether to build WP6.

---

## WP6. Certificates, checkpoints, outbox

**Goal.** SPEC section 7. The bank can verify a signature with `REGISTRY_PUBLIC_KEYS`.

**Depends on.** WP5.

**Create.** `IssueCertificate`, `SignCheckpoint`, `DeliverOutbox`, the three read and ack routes those need.

**Test against Nayan.** Recompute `contracts/examples/vectors/signatures.json` with the same canonical JSON. An alert payload contains only the caller's own asset token, which is what his alert sync stores.

**Edge cases.** One flipped signature bit fails verification. A checkpoint whose `head_hash` is not the named event fails. An alert body that contains another member id fails the privacy test. Ack of an already acked message is still 204. Another member's certificate id is 404.

**Done-check.** Package tests and `make contracts-check`.

**Show it working.** `curl` the certificate, the latest checkpoint, and each member's outbox after the WP5 conflict. The explanation shows the signature checks, that each alert carries only that member's own asset token, and that this is the body his `SyncRegistryOutbox` will store.

**Stop.** Ask whether to build WP7.

---

## WP7. Remaining reads and the operator view

**Goal.** The routes in SPEC section 7 that WP6 did not finish: submission read, cases, ledger views, health, 404 and 405.

**Depends on.** WP6.

**Create.** Those routes and the access log.

**Test against Nayan.** A bank API key sees only its own submission, cases and outbox. The auditor key is the only key that gets `view=full`. That is the split his integrity and operator flows expect.

**Edge cases.** Unknown path is 404 `NOT_FOUND` in the error envelope. Wrong method is 405 with `Allow`. A repeated `X-Request-ID` is 422. Log lines contain the route template and no token.

**Done-check.** Package tests. `make contracts-check`. A dump of the registry tables, after a pledge of a known VIN's token, contains that token and does not contain the VIN.

**Show it working.** `curl` `GET /v1/ledger/events?view=full` once with a bank key and once with the auditor key. The explanation says why the bank key is refused and what the auditor is allowed to see: codes, hashes and member ids.

**Stop.** Ask whether to build WP8.

---

## WP8. Ledger verifier

**Goal.** SPEC section 8.

**Depends on.** WP7.

**Create.** `ledger_verifier/` and `onepledge-ledger-verifier verify`.

**Test against Nayan.** On a clean pledge ledger, the verifier exits 0 and his certificate check would accept the same checkpoint (public key shared via config, not via an edit to his package). Then apply the scene 9 edits on a copy of the database: in-place `payload_hash`, flipped checkpoint signature, certificate body changed without re-signing. The verifier names the broken seq. The original database is unchanged and still verifies.

**Edge cases.** A gap in `seq`. Seq 1 with a non-zero `prev_hash`. A checkpoint signed over an older head.

**Done-check.** `python3 -m pytest ledger_verifier/tests -q` exits 0.

**Show it working.** Run `onepledge-ledger-verifier verify` on the live database and show exit 0. Then run it on a copy with one `payload_hash` changed and show the broken seq. The explanation says the verifier recomputes hashes itself and does not trust the registry process. This is scene 9 without the auditor screen, which Vedant builds.

**Stop.** Ask whether to build WP9.

---

## WP9. Point the bank at these processes

**Goal.** SPEC section 9. Scenes 2, 3, 4, 6, 7, 8 and 9 against the real processes.

**Depends on.** WP8.

**Create.** README section that lists the environment variables. No edit under `bank_agent/`. A script or documented command sequence is allowed under `registry/` or `key_holder/` only if it does not wrap or patch his code.

**Test against Nayan.** Set `KEY_HOLDER_1_URL`, `KEY_HOLDER_2_URL`, `REGISTRY_URL` and the matching keys. Run his pipeline far enough to show the scenes. Separately, `make test` for his packages still passes against the doubles.

**Edge cases.** Three members. Planted exact copy. Amount change still `LIKELY`. Release, then the other member locks with no alert. 100 concurrent submits, one lock each time. Database scan finds no VIN, name, amount or date in the key holder or registry databases.

**Done-check.** The scene notes with command output, plus his `make test` exit 0 on an unmodified layer 1 tree.

**Show it working.** With his unchanged bank pointed at the three URLs, run one honest pledge and one double pledge. The explanation follows a single loan from his tokenize stage through both holders, into the registry transaction, out as a certificate and two alerts. Quote the status lines from his pipeline.

**Stop.** The layer is ready for rubric review. The implementer does not mark the rubric passed.
