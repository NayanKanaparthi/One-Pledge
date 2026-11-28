# OnePledge layer 2: SPEC decisions (binding addendum)

Version 1.0, 2026-11-24. Written before implementation, from a comparison of `docs/BUILD_PLAN.md`, the 23-page OnePledge Build Plan PDF (17 Nov 2026), the draft contracts, and the layer 1 code already on `main`.

This file is binding for layer 2. Where it differs from `docs/layer2/SPEC.md`, this file wins. An entry is appended, never rewritten.

## Summary

| # | Topic | Decision |
|---|---|---|
| 1 | Are the three plans the same text? | No. Same design. Different detail. Precedence is fixed. |
| 2 | What makes a retry safe? | The `Idempotency-Key` header, not `X-Request-ID`. |
| 3 | Where do contracts come from? | The YAML files already in `contracts/`. They are not generated from code. |
| 4 | `facility_ref` on a lock | Not stored. It is not on the wire. |
| 5 | Ledger columns | Follow the hash object in `registry.yaml`. Extra PDF columns are not added beside the payload. |
| 6 | Access log path | Store the route template. Do not store a raw path. |
| 7 | When Rule C is built | Inside the submission transaction, with Rules A and B. |
| 8 | Sigstore Rekor | Out of layer 2. |
| 9 | FastAPI or Django | FastAPI. The repository already chose it. |
| 10 | When the next package starts | Only after the current package's tests pass and Shreyas says to continue. |
| 11 | Doubles | Behavior oracle only. Not imported by production code. |
| 12 | Key holder tables | `key_versions`, `evaluation_log`, `rate_limits`, hashed API keys. No codes, no elements. |
| 13 | `docs/layer2/` | Shreyas owns these five files, the same way layer 1 owns `docs/layer1/`. |

---

## 1. The three plans are the same design and not the same text

**Question.** Is `docs/BUILD_PLAN.md` the same document as the uploaded 23-page PDF, and the same as the layer 2 execution plan?

**Decision.** They describe one system. They are not copies of each other.

- `docs/BUILD_PLAN.md` is the short team plan checked into the repo (178 lines). It has the nine scenes, the ownership table, the technology, the table lists in compressed form, the design rules, both contracts, the code rules, five phases, and the release checklist.
- The PDF is the long form of that plan. It adds the "why" column, full registry column lists, the per-person phase table (the markdown only spells out Nayan's column), class names for layer 2, the role-play cast, open decisions, and sources.
- The layer 2 execution plan is the order Shreyas builds in, grounded in Nayan's code. It does not replace either document. It is now `docs/layer2/WORK_PACKAGES.md`.

Precedence for layer 2:

1. `contracts/key_holder.yaml` and `contracts/registry.yaml` for anything on the wire.
2. `docs/BUILD_PLAN.md` for team rules, when it and the PDF both speak and disagree.
3. The PDF where `BUILD_PLAN.md` is silent.
4. Layer 1 code for how the caller behaves.
5. This file when those sources conflict. Each conflict below is one entry.

**Affects.** Every work package.

## 2. Retry identity is the idempotency key

**Question.** The PDF says the agent sends codes "with a request ID, so a retry can never lock anything twice." `BUILD_PLAN.md` says "an idempotency key." The bank sends both `X-Request-ID` and `Idempotency-Key`.

**Decision.** `Idempotency-Key` (64 lower-case hex characters) is the retry identity on `POST /v1/submissions`, as `registry.yaml` specifies. `X-Request-ID` is a correlation id. It is echoed. It does not deduplicate a submission. A key holder `request_id` is recorded in `evaluation_log`. Repeating an evaluation is safe because evaluation is a pure function, not because the request id is an idempotency store.

**Affects.** WP5.

## 3. Contracts stay as the YAML files

**Question.** The PDF says "the contract files are generated from the code." `BUILD_PLAN.md` says the contracts live in `contracts/` as OpenAPI files. The repo contains hand-written YAML, marked draft, plus examples and vectors. `make contracts-check` validates that tree.

**Decision.** Layer 2 implements `contracts/key_holder.yaml` and `contracts/registry.yaml` as they are. It does not generate them from Python models. A change is additive and needs Nayan, Shreyas and Vedant. Until Shreyas and Vedant approve, the files stay marked draft, and layer 2 still implements them.

**Affects.** WP0 and every later package.

## 4. No `facility_ref` on a lock

**Question.** The PDF `pledge_locks` table includes `facility_ref`. The submission body in `registry.yaml` has `schema_version`, `kind`, `key_version` and `items`. An item has `item_ref`, `asset_token`, and `field_tokens` for a lock request. There is no facility id.

**Decision.** The registry does not store a facility reference. Inventing one would keep a bank-side identifier the contract never sends.

**Affects.** WP4.

## 5. Ledger columns follow the contract hash

**Question.** The PDF lists `ledger_events` columns `event_id`, `member_id`, `asset_token`, `submission_id` beside `payload`. `registry.yaml` defines `payload_hash` as the hash of the canonical payload, and `event_hash` as the hash of exactly `event_type`, `payload_hash`, `prev_hash`, `recorded_at`, `schema_version`, `seq`. `prev_hash` of seq 1 is 64 zeros.

**Decision.** A row stores `seq`, `event_type`, `payload` (canonical JSON), `payload_hash`, `prev_hash`, `event_hash`, `schema_version`, `recorded_at`. Member and asset, when the event has them, live inside `payload`. They are not a second copy in columns that the hash ignores. `seq` starts at 1 and has no gaps.

**Affects.** WP4, WP8.

## 6. The access log stores the route template

**Question.** The PDF `access_log` column is `path`. Layer 1 logs the route template and refuses to log a raw path that could carry a value.

**Decision.** Layer 2 uses the same rule. The stored route is the template, for example `/v1/certificates/{certificate_id}`. Query strings are not stored. Blinded elements and tokens are not stored.

**Affects.** WP2, WP7.

## 7. Rule C ships inside the decision transaction

**Question.** The PDF phase table puts "likely-match rule" in phase 3 (days 8 to 10). `registry.yaml` applies Rule C on every `BACK_BOOK` and `PLEDGE` item, in the same transaction as Rules A and B.

**Decision.** WP5 implements Rules A, B and C together. A pledge that omitted Rule C would disagree with the contract and with the double. Phase 3's remaining layer 2 work is the verifier, the race test, the tamper test and case resolution, which are WP6 through WP8.

**Affects.** WP5, WP8.

## 8. Sigstore is out of this layer

**Question.** The PDF lists publishing the daily checkpoint to Sigstore Rekor as an open stretch item, to do only after the release checklist passes. `BUILD_PLAN.md` does not mention it.

**Decision.** Layer 2 does not publish to Rekor. Checkpoints are signed and stored. Banks and `ledger_verifier/` verify them. Rekor needs a later decision by all three owners.

**Affects.** WP6, WP8.

## 9. The framework decision is already closed

**Question.** The PDF leaves FastAPI plus Next.js versus Django plus HTMX as a day-1 open decision.

**Decision.** The repository is FastAPI. Layer 2 uses FastAPI and Pydantic. Layer 2 does not start a Django app. Screens stay with Vedant.

**Affects.** WP1.

## 10. Stop after a green package

**Question.** The execution plan said each step is tested against Nayan's build, and the next step waits for Shreyas.

**Decision.** That rule is binding. A package with a failing done-check is not handed forward. A passing package is not followed by the next package until Shreyas says to build the next one. His layer 1 tests are re-run as the package's done-check specifies, so a green layer 2 package has not bent his tree.

**Affects.** Every work package.

## 11. Doubles are the oracle, not the service

**Question.** Layer 1 built `KeyHolderDouble` and `RegistryDouble` so layer 2 would have an executable reference.

**Decision.** Match their decisions, status codes, error codes and canonical bytes. Do not match their process model: they keep state in memory, expose `/_test/` when configured, and set `test_double` true on `/healthz`. Layer 2 `/healthz` omits `test_double` or sets it false. Layer 2 has no `/_test/` routes. `src/` under the three layer 2 packages does not import `onepledge_contract_fakes`. A test may run a double beside the real service and compare answers.

**Affects.** WP2, WP3, WP5, WP6.

## 12. Key holder storage

**Question.** The PDF says each key holder database holds key versions and a request log, and holds no loan data and no codes. `BUILD_PLAN.md` also names `rate_limits`. The contract requires a daily element cap.

**Decision.** Tables are `key_versions` (version, status, created_at), `evaluation_log` (member, request id, item count, time), `rate_limits` (member, window start, items used), and API credentials stored as SHA-256. The OPRF scalar lives in the environment. Elements are not written to a table or a log.

**Affects.** WP2.

## 13. These docs live next to layer 1's docs

**Question.** `BUILD_PLAN.md` gives the `docs/` folder to Vedant. Nayan already added `docs/layer1/`.

**Decision.** `docs/layer2/` is Shreyas's spec set: `CONTEXT.md`, `SPEC.md`, `WORK_PACKAGES.md`, `RUBRIC.md`, `SPEC_DECISIONS.md`. It does not move or edit `docs/layer1/` or `docs/BUILD_PLAN.md`.

## 14. Test dependencies beyond SPEC section 3 (BUILD_INSTRUCTIONS C3)

**Question.** `SPEC.md` section 3 allows the dev dependencies pytest, pytest-cov, hypothesis, ruff and mypy. The root `conftest.py` loads `onepledge_testtools.pytest_plugin` for every test run from the repository root, and the root `pyproject.toml` sets `timeout = 120`, which needs pytest-timeout. An in-process call of `/healthz` needs httpx. A test that checks a response against the `Health` schema in `contracts/*.yaml` needs PyYAML and jsonschema. `outside_records/pyproject.toml`, the shape layer 2 copies, declares all of these.

**Decision.** At WP1, asked which dev dependencies layer 2 may declare, Shreyas selected the option labelled "outside_records dev set (Recommended)". That option read: "Dev only: pytest-timeout, onepledge-testtools, httpx, PyYAML, jsonschema, types-PyYAML, types-jsonschema. This is the same as the package shape SPEC says to copy. Runtime stays exactly at the SPEC list, with no starlette import in WP1." So layer 2 packages may declare, as dev dependencies only, pytest-timeout, onepledge-testtools, httpx, PyYAML, jsonschema, types-PyYAML and types-jsonschema. Each package declares only the ones its tests use. Runtime dependencies stay the `SPEC.md` section 3 list. starlette is not declared, and `src/` does not import it, until a later decision.

**Affects.** WP1 and every later package's `pyproject.toml`.
