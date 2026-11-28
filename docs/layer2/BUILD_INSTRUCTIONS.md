# OnePledge layer 2: build instructions for the builder and the tester

Version 1.0, 2026-11-24. Owner: Shreyas (layer 2). Written in a planning chat from files read on `main` at commit `0093a5c`. No layer 2 code existed when it was written.

This file is the lowest source in `docs/layer2/`. It restates rules from other files and cites each one. Where it disagrees with `contracts/key_holder.yaml`, `contracts/registry.yaml`, `docs/layer2/SPEC_DECISIONS.md`, `docs/layer2/SPEC.md` or `docs/layer2/WORK_PACKAGES.md`, the other file wins and this file is wrong. Report the difference to Shreyas. Do not follow this file over them.

Contents: 1 Roles. 2 Precedence and reading order. 3 Rules for both agents. 4 Environment. 5 What the bank on main sends. 6 The builder's loop. 7 The tester's loop. 8 Branches and pull requests. 9 Conflicts and open questions. 10 to 19: WP0 to WP9.

---

## 1. Roles

| Agent | Does | Never does |
|---|---|---|
| Claude, the builder | Builds one work package per chat, only in the files that package lists. Runs the package's builder commands and its "Show it working" command. Hands back, then stops. | Commit, push, open a pull request, start the next package, mark the rubric passed |
| Cursor, the tester | Tests the package the builder left in the working tree. Only when every command it is told to run has passed: commits that package, pushes its branch to the `fork` remote, opens one pull request into `NayanKanaparthi/One-Pledge` `main` | Push to `origin`, push after any failed command, edit product code to make a test pass, merge |
| Shreyas | Says when a package starts. Rules on each open item in section 9. Approves every new entry in `SPEC_DECISIONS.md` | Not an agent |

Sources: `docs/layer2/CONTEXT.md` "How a work package finishes" steps 1 to 6; `SPEC_DECISIONS.md` decision 10; `WORK_PACKAGES.md` rules 7 to 9; `docs/BUILD_PLAN.md` section 9 ("nobody merges own PR"). The commit, push and pull request split between the two agents comes from Shreyas's brief for this file.

One package is one pull request.

## 2. Precedence and reading order

Precedence, from `SPEC_DECISIONS.md` decision 1, `CONTEXT.md` "Sources, and which one wins", and `SPEC.md` "How to read this file":

1. `contracts/key_holder.yaml` and `contracts/registry.yaml` for anything on the wire.
2. `docs/layer2/SPEC_DECISIONS.md` over `docs/layer2/SPEC.md`.
3. `SPEC.md` plus `SPEC_DECISIONS.md` is the contract (`WORK_PACKAGES.md` rule 2).
4. `docs/BUILD_PLAN.md` for team rules. The 23-page PDF that `CONTEXT.md` names is not in the repository (no `*.pdf` found on `main`). Do not cite it unless Shreyas supplies it.
5. Layer 1 code for how the caller behaves.
6. `docs/layer2/RUBRIC.md` is fixed (its header) and is graded later by a reviewer, never by either agent.
7. This file.

Reading order at the start of every chat (`CONTEXT.md` line 3; `WORK_PACKAGES.md` rule 1): `docs/layer2/CONTEXT.md`, `docs/BUILD_PLAN.md`, `docs/layer2/SPEC.md`, `docs/layer2/SPEC_DECISIONS.md`, `docs/layer2/WORK_PACKAGES.md`, sections 3 to 9 of this file, the package's own section here, then every file that section names. A file not opened in the current chat is not known in that chat.

## 3. Rules for both agents

| Rule | Source |
|---|---|
| Do not edit `bank_agent/`, `datagen/`, `outside_records/`, `evaluation/`, `contracts/fakes/`, `contracts/*.yaml`, `contracts/examples/`, `docs/layer1/`. (`evaluation/` does not exist on `main` at `0093a5c`; the rule holds if it appears.) | `WORK_PACKAGES.md` rule 3; `CONTEXT.md` step 2; Shreyas's brief |
| Do not edit `docs/BUILD_PLAN.md`. | `SPEC_DECISIONS.md` decision 13 |
| Work only in the folders the package lists. `Makefile` and root `pyproject.toml` change only when the package says so. Only WP1 says so, and only for the `Makefile`. | `WORK_PACKAGES.md` rule 5 |
| Do not edit `SPEC.md`, `WORK_PACKAGES.md`, `CONTEXT.md` or `RUBRIC.md`. A ruling is appended to `SPEC_DECISIONS.md` as the next number, with Shreyas's approval. An entry is never rewritten. | `WORK_PACKAGES.md` rule 2; `SPEC_DECISIONS.md` header; `RUBRIC.md` header |
| No stubs, no `TODO`, no `NotImplementedError` outside abstract methods, no test-mode branch in product code, no em dashes. The pre-commit hook `no-em-dash` rejects U+2014 in every text file. | `WORK_PACKAGES.md` rule 4; `SPEC.md` "How to read"; `.pre-commit-config.yaml` |
| The edge cases of a package are tests, written with its code. | `WORK_PACKAGES.md` rule 6; `SPEC.md` section 10 |
| `src/` under `key_holder/`, `registry/`, `ledger_verifier/` imports none of `onepledge_bank_agent`, `onepledge_datagen`, `onepledge_outside_records`, `onepledge_evaluation`, `onepledge_contract_fakes`. A test under `tests/` may import a double. | `SPEC.md` sections 3 and 10; `SPEC_DECISIONS.md` decision 11 |
| `/healthz` omits `test_double` or sets it false. No `/_test/` routes. | `SPEC_DECISIONS.md` decision 11; `SPEC.md` section 2 |
| `domain/` has no database, web framework or network. `application/` is one class per use case. SQL only under `infrastructure/`. FastAPI only under `interface/`. | `CONTEXT.md` "The standard"; `BUILD_PLAN.md` section 9 |
| Python 3.12+, type hints, mypy strict, ruff, pytest. Coverage at least 85% on `domain` plus `application` for `onepledge_key_holder` and `onepledge_registry`. Business errors are classes whose names end in `Error`. Constructor injection, no globals for keys, clocks or repositories. | `SPEC.md` section 3 |
| Runtime dependencies: fastapi, uvicorn, pydantic, sqlalchemy, alembic, psycopg, cryptography, oprf, structlog. Dev: pytest, pytest-cov, hypothesis, ruff, mypy. Nothing else without a new decision. See C3. | `SPEC.md` section 3 |
| No raw loan value in any log line, error message or URL. The access log stores the route template, no query string, no element, no token. | `CONTEXT.md` "Boundaries"; `BUILD_PLAN.md` section 7; `SPEC_DECISIONS.md` decision 6 |
| Secrets only in the environment, and as empty placeholders in `.env.example`. Never commit a scalar, an API key or a signing seed. The pre-commit hook `no-env-file` rejects a committed `.env`. | `SPEC.md` section 3; `.pre-commit-config.yaml` |
| Never answer with a redirect and never compress a response. The bank does not follow redirects, treats a 3xx as `MALFORMED_RESPONSE`, sends `Accept-Encoding: identity` and refuses any other `Content-Encoding`. The doubles answer a trailing-slash path with 404 `NOT_FOUND`, never a redirect. | `bank_agent/.../infrastructure/http/base.py`; `.../http/classification.py`; `contracts/fakes/README.md` |
| Stop after each package. The next one starts only when Shreyas says so. | `SPEC_DECISIONS.md` decision 10; `WORK_PACKAGES.md` rule 8 |

Ports and names (`SPEC.md` section 3): kh1 on 8301, kh2 on 8302, registry on 8400. `HOLDER_ID` is `kh1` or `kh2`. Bind addresses are IP literals, as in layer 1. Console scripts are `onepledge-keyholder db provision|db migrate|serve`, `onepledge-registry db provision|db migrate|serve`, `onepledge-ledger-verifier verify`.

`SPEC.md` names no environment variable for the scalar, the member keys, the member list, the auditor key or the signing seed. The builder names them in the package README in the package that introduces them, and both agents use the README's names. The reviewer follows `key_holder/README.md` and `registry/README.md` to provision, migrate and serve (`RUBRIC.md` "Reviewer setup").

## 4. Environment

Once per machine, from the repository root (root `README.md` "Getting started"):

```
python3 -m venv .venv && . .venv/bin/activate
make setup
```

- PostgreSQL 16. Tests read `PG_ADMIN_URL` (default `postgresql://postgres@localhost:55432/postgres`) and create and drop their own `optest_` databases (root `README.md`; `tools/src/onepledge_testtools/scratch_databases.py`).
- `make setup` installs only the Makefile's `PACKAGES` (`tools contracts/fakes datagen outside_records bank_agent`). WP1 is not allowed to change that list (it may change `lint` and `typecheck` only). From WP1 on, install the layer 2 packages by hand:

```
python3 -m pip install -e "key_holder[dev]" -e "registry[dev]" -e "ledger_verifier[dev]"
```

- `psql` and `pg_dump` are needed by several "Show it working" commands. On the planning machine neither `psql` nor `pg_isready` was on `PATH`.
- `.venv/` is not listed in `.gitignore`. Stage explicit paths only (section 7).
- Observed in the planning chat: without `make setup`, `make contracts-check` fails with `make: onepledge-contracts: No such file or directory` (the full output is in WP0).

## 5. What the bank on main sends

Everything here was read in `bank_agent/src/onepledge_bank_agent/` on `main`. Layer 2 answers this traffic. It does not change it (`CONTEXT.md` "Who you work for").

### 5.1 Every call

From `infrastructure/http/base.py`:

- Headers on every request: `X-API-Key` (that dependency's key), `Accept: application/json`, `Accept-Encoding: identity`, and `X-Request-ID` when the bank has a request id in context.
- Retries: two more attempts, after 0.2 s and 0.4 s, on a transport error, a timeout, 502, 503 or 504. A 429 is not retried inside the call.
- Bounds: an answer over `HTTP_MAX_RESPONSE_BYTES` (default 4,194,304) or a non-identity encoding is refused as `MALFORMED_RESPONSE`. Redirects are never followed.
- Only route templates are logged by the bank, never a path or query.

From `infrastructure/http/classification.py`, how the bank classes an answer that is not the success it expects:

| Status | Class | Reason |
|---|---|---|
| 429 | transient | `RATE_LIMITED` |
| 500, 502, 503, 504, any other 5xx | transient | `SERVER_ERROR` |
| 401 | permanent | `UNAUTHENTICATED` |
| 403 | permanent | `FORBIDDEN` |
| any other 4xx (409 and 422 included, unless the client names the code) | permanent | `REQUEST_REFUSED` |
| 3xx, not JSON, not the contract | permanent | `MALFORMED_RESPONSE` |

### 5.2 Key holders

From `application/tokenize_loans.py` and `infrastructure/http/key_holder_client.py`:

- `POST /v1/evaluate`, body `{"request_id": "<new uuid>", "key_version": null, "elements": [...]}`.
- Holder 1 receives the blinded elements. Holder 2 receives holder 1's `elements` list unchanged (`self._holders.second.evaluate(first.elements)`).
- The bank accepts only 200. It parses the answer strictly (`request_id`, `holder_id`, `key_version` an integer from 1, `elements` each matching `^[A-Za-z0-9+/]{43}=$`). The answer must echo the same `request_id`, hold as many elements as were sent, and every element must decode to a valid point. Otherwise `KeyHolderProtocolError`.
- The pair `KeyVersion(first.key_version, second.key_version)` must equal the version already stored for the submission's loans, or `KeyVersionChangedError`.
- The bank unblinds each answer. An element that does not unblind is `KeyHolderProtocolError`. It then derives each 64-hex token with `token_from_point`.
- Chunk size: `TOKENIZE_CHUNK_LOANS` default 100, at most 125, "at most 1,000 elements per key holder request" (`bank_agent/README.md` "Configuration").

### 5.3 Registry

From `infrastructure/http/registry_client.py`. These are the only registry routes the bank client calls:

| Call | What the bank sends | What the bank accepts |
|---|---|---|
| `POST /v1/submissions` | Header `Idempotency-Key`. Body `schema_version` 1, `kind`, `key_version`, `items` of `item_ref`, `asset_token`, and `field_tokens` only when the bank has them | 201, or 200 with `Idempotent-Replay: true`. A 200 without that header, or the header on a 201, is `RegistryProtocolError`. 409 `KEY_VERSION_MISMATCH` is `KeyVersionMismatchError`. 409 `IDEMPOTENCY_KEY_REUSED`, 401, 403, 413, 422 are `RegistryRejectedError` |
| `GET /v1/certificates/{certificate_id}` | Nothing | 200 `SignedCertificate` |
| `GET /v1/outbox?limit=` | `limit` | 200 with `messages` and `has_more`. A known type (`ALERT`, `CASE_UPDATE`, `CHECKPOINT`) is validated against its whole schema; a message that fails is quarantined, not dropped |
| `POST /v1/outbox/{message_id}/ack` | No body | 204 only |
| `POST /v1/cases/{case_id}/resolution` | `{"position": ...}` | 200 `CaseView`. 409 `CASE_ALREADY_RESOLVED` and 409 `POSITION_ALREADY_RECORDED` are named errors |
| `GET /v1/ledger/events` | `view=headers`, `after_seq`, `limit` | 200 `LedgerEventPage` |

Trust anchors (`infrastructure/crypto/signatures.py`; `bank_agent/README.md` "Configuration"): `REGISTRY_PUBLIC_KEYS` is a JSON object of signing key id to the standard base64 of a 32 byte raw Ed25519 public key. The bank never fetches a key from the registry. The published test vector key is refused.

Environment variables the bank reads to find layer 2 (`infrastructure/settings.py`; `SPEC.md` section 9): `KEY_HOLDER_1_URL`, `KEY_HOLDER_1_API_KEY`, `KEY_HOLDER_2_URL`, `KEY_HOLDER_2_API_KEY`, `REGISTRY_URL`, `REGISTRY_API_KEY`, `REGISTRY_MEMBER_ID`, `REGISTRY_PUBLIC_KEYS`.

## 6. The builder's loop (Claude)

1. Start only when Shreyas has said "build WPn" in this chat.
2. Read in the order of section 2. Open every file the package section names.
3. Check the tree and report what you see: `git status --short`, `git branch --show-current`, `git log -1 --oneline`. The tree must be clean apart from untracked `docs/layer2/` (before WP0 is merged) and the local `.venv/`. The base must be the one section 8 names. If either is wrong, stop and ask.
4. Create the package branch locally: `git switch -c l2/wpN-short-name` (name in the package section). Do not commit.
5. Build. Only the files the package section lists. Tests with the code, edge cases included.
6. Run the package's builder commands. All exit 0.
7. Run the package's "Show it working" command. Keep the output.
8. Hand back, in this order: `git status --short`; each command with its output; the explanation (`CONTEXT.md` step 4: the request that came in, the decision or calculation that ran, what was stored or returned, and which part of the bank it answers); every SPEC question; every section 9 item the package touched and what you did about it.
9. Stop. Do not commit, push or open a pull request. Ask whether to build the next package.

If a section 9 item says it blocks this package, raise it in step 3 and wait. A gap that affects correctness or security stops the package (`WORK_PACKAGES.md` rule 2).

## 7. The tester's loop (Cursor)

Run each step in order. On the first failure, stop: do not commit, do not push. Report the command and its full output to Shreyas.

1. Preconditions.

```
git branch --show-current                 # must print the package's branch name
git remote get-url --push fork            # must print Shreyas's fork; if it fails, stop and ask Shreyas
test "$(git remote get-url --push fork)" != "$(git remote get-url --push origin)"   # must exit 0
gh auth status                            # must be logged in
```

   On `main` at `0093a5c` the only remote is `origin` (`NayanKanaparthi/One-Pledge`). The `fork` remote must be added with the URL Shreyas gives. Do not guess it.

2. Scope. This must print nothing except the local `.venv/` line, if present:

```
git status --short --untracked-files=all | grep -vE '^.. (key_holder/|registry/|ledger_verifier/|docs/layer2/)'
```

   WP1 may also show `M Makefile`. Any other line means the builder left the package's folders: stop.

3. Run the package's "Cursor commands" from its section.
4. Run the layer 1 gate. Root `README.md` "Repository rules": "`make ci` must pass before a merge."

```
make ci
```

   If a layer 1 check fails and `git status --short -- bank_agent datagen outside_records evaluation contracts docs/layer1` prints nothing, the failure is in layer 1's tree, not this package (`WORK_PACKAGES.md` WP4 done-check). Do not push. Report it to Shreyas.

5. Run the package's "Show it working" command. Keep the output for the pull request.
6. Stage explicit paths only, never `git add -A` or `git add .`:

```
git add -- <the paths the package section lists>
git diff --cached --name-only             # check it is exactly the package's files
pre-commit run --files $(git diff --cached --name-only)
```

7. Commit with the pull request title as the subject: `git commit -m "<pull request title>"`.
8. Push to the fork only. Never `origin`. Never `--force`.

```
git push fork l2/wpN-short-name
```

9. Open the pull request:

```
gh pr create --repo NayanKanaparthi/One-Pledge --base main \
  --head "<fork owner>:l2/wpN-short-name" --title "<pull request title>" --body-file <file>
```

   `<fork owner>` is the account in `git remote get-url fork`.

10. Stop. Do not merge. Do not start the next package.

Pull request body: what the package implements (from its section here); every command run with its result line; the "Show it working" output and the builder's explanation; the section 9 items it touched; `git diff --stat main...HEAD`; and, if the branch is stacked, "Stacked on #N".

## 8. Branches and pull requests

- Names: `l2/wpN-short-name` (`BUILD_PLAN.md` section 9: branches `l2/...`). Each package section gives its name and pull request title.
- Base. No other file sets one, so this file does. WP0 starts from `main` (`git fetch origin && git switch -c l2/wp0-accept-contracts origin/main` with `docs/layer2/` untracked in the tree). Each later package starts from the tip of the previous package's branch, unless Shreyas says the previous pull request is merged. Then it starts from `origin/main` after `git fetch origin`. Shreyas may change this rule.
- Size. `BUILD_PLAN.md` section 9 asks for pull requests of about 400 lines or fewer. One package is one pull request. See C4.
- Merging. Nobody merges their own pull request. CI green to merge (`BUILD_PLAN.md` section 9). There is no `.github/` directory on `main`, so `make ci` run locally is the gate (section 7 step 4).

## 9. Conflicts and open questions

Each item names the files that disagree. Where the YAML or `SPEC_DECISIONS.md` settles it, the item says so. Otherwise it is open: the builder raises it at the package named and waits, and Shreyas's ruling becomes the next entry in `SPEC_DECISIONS.md`. Nothing here is a compromise invented by this file.

| Id | What disagrees | Status | Raise at |
|---|---|---|---|
| C1 | `registry.yaml` defines `POST /v1/cases/{case_id}/resolution`. The bank calls it (`RegistryHttpClient.record_position`). `SPEC.md` G2 says the registry answers every path in `registry.yaml`, and `SPEC_DECISIONS.md` decision 7 puts case resolution in WP6 to WP8. But `SPEC.md` section 7's route list, WP6 and WP7 never name this route. WP5's edge case "a `CLEARED_DATA_ERROR` exemption" needs a case in `CLEARED_DATA_ERROR`, which `registry.yaml` reaches only when both parties record `DATA_ERROR` on this route. | Open. Shreyas assigns the route to a package. | WP5 |
| C2 | `contracts/examples/vectors/tokenization.json` says "TEST VECTOR ONLY. These scalars are public. Never configure them in a running key holder." `contracts/fakes/README.md` says "The published test vector secrets are refused." But `SPEC.md` G1 and WP3 need the real holders to evaluate with the vector's scalars ("the tokens match because both holders used the vector's scalars"). `SPEC.md` section 4.2 refuses at startup only a scalar that is not 32 bytes or is all zeros, and `WORK_PACKAGES.md` rule 4 forbids a test-mode branch. | Open. Shreyas rules whether the real holder refuses the vector scalars, and how WP3 runs. | WP2 hand-back; blocks WP3 |
| C3 | `SPEC.md` section 3 allows only the listed dependencies. The package shape to copy, `outside_records/pyproject.toml`, also declares `starlette` (runtime) and `httpx`, `pytest-timeout`, `onepledge-testtools`, `PyYAML`, `jsonschema`, `types-PyYAML`, `types-jsonschema` (dev). The root `conftest.py` loads `onepledge_testtools.pytest_plugin` for every test run from the root, and the root `pyproject.toml` sets `timeout = 120`. WP3 lets a test import the bank, which needs the bank installed as a dev dependency. | Open. The builder lists each dependency it needs beyond `SPEC.md` section 3 and stops for a decision entry. | WP1, again at WP3 |
| C4 | `BUILD_PLAN.md` section 9: pull requests about 400 lines or fewer. Shreyas's brief: one package is one pull request. | Open. Cursor reports the size in every pull request body. | Every package |
| C5 | `registry.yaml` `SubmissionResult` requires `as_of_seq` and `certificate_id`, and a replay returns the stored bytes. `SPEC.md` section 6 puts `CERTIFICATE_ISSUED` and the checkpoint inside the submission. `SPEC.md` G2: "A submission is one database transaction: locks, conflicts, ledger append, certificate, checkpoint, outbox." But WP6, not WP5, lists `IssueCertificate`, `SignCheckpoint` and `DeliverOutbox` under Create. | Settled by the YAML and SPEC (they win over a package's split). WP5's transaction issues the certificate, signs the checkpoint and writes the outbox rows. WP6 adds the read and ack routes and the signature tests. The builder states this in the WP5 hand-back. | WP5 |
| C6 | WP6 says "the three read and ack routes those need", and its "Show it working" fetches the latest checkpoint. WP7's list ("submission read, cases, ledger views, health, 404 and 405") does not include `GET /v1/checkpoints/latest`. So the routes WP6 needs are four: certificate, latest checkpoint, outbox, ack. | Settled by WP6's own "Show it working". The builder reports the count in the WP6 hand-back. | WP6 |
| C7 | `SPEC.md` section 5.1 and WP4 Create: `MEMBER_JOINED` events and checkpoint 1 are written "on first provision". WP4 "Show it working" runs `db migrate` and then prints them. In the `outside_records/` shape, `db provision` creates the roles and the database and `db migrate` creates the tables (`outside_records/README.md` "Commands"), so provision runs before the ledger table exists. | Open. Before writing the genesis code, the builder reports which command writes the genesis events and how both sentences hold, or stops. | WP4 |
| C8 | `RUBRIC.md` VER-02 edits "a certificate body with `items_locked` incremented". `registry.yaml` `CertificateBody.summary` has `checked`, `locked`, `held`, `released`, `rejected`. There is no `items_locked`. The double's tamper mode changes `summary.locked` (`contracts/fakes/README.md`). | Settled by the YAML. The WP8 tamper test changes `summary.locked`. `RUBRIC.md` is fixed and is not edited. | WP8 |
| C9 | WP9 and `RUBRIC.md` JOIN-01 need the unchanged bank to run one honest pledge and one double pledge against the real URLs. On `main` the bank has no way in for a tape outside its tests. `bank_agent/src/onepledge_bank_agent/interface/` holds only `cli/` and `composition.py`. The CLI has no upload command. `bank_agent/README.md`: "Uploads arrive through the HTTP API of WP4; the tests upload through `IngestTape`." Root `README.md` lists the bank HTTP API as in progress. The bank's integration tests reach the doubles in process, not by URL. WP9 allows a script under `registry/` or `key_holder/` "only if it does not wrap or patch his code". | Open. Shreyas rules how a tape enters the bank, or WP9 waits for layer 1's HTTP API. | WP9 |
| C10 | `BUILD_PLAN.md` section 8 lists `GET /v1/submissions/{id}` as a call the bank agent makes. The bank client on `main` does not call it (section 5.3). | Information only. Layer 2 still implements it (`registry.yaml`; `SPEC.md` G2; WP7). | WP7 |
| C11 | WP2: "More than 1000 elements (413 or 422 as the YAML says)". `key_holder.yaml` puts `maxItems: 1000` on `elements` under 422 `VALIDATION_ERROR`, and lists 413 separately. The double answers 1001 elements with 422 `VALIDATION_ERROR` and issue `TOO_MANY_ITEMS`, and a body over 1 MiB with 413 `PAYLOAD_TOO_LARGE` (`key_holder/app.py`, `web/requests.py`, `tests/test_key_holder_http.py`). | Settled by the YAML and decision 11 (match the double's status codes): 1001 elements is 422 `VALIDATION_ERROR` with issue `TOO_MANY_ITEMS`. A body over the size limit is 413 `PAYLOAD_TOO_LARGE`. | WP2 |

---

## 10. WP0. Accept the draft contracts

Sources: `WORK_PACKAGES.md` WP0; `SPEC_DECISIONS.md` decisions 2 and 3; `CONTEXT.md` "What is already true".

- Branch: `l2/wp0-accept-contracts`
- Pull request title: `Layer 2 WP0: Accept the draft contracts`
- Depends on: nothing.

**What it implements, and how it meets the bank.** No service and no product code. WP0 confirms the wire before any process exists (`WORK_PACKAGES.md` WP0 "Show it working"). The two YAML files are draft `1.0.0-draft.1`. Layer 2 implements them as they are, and does not generate them from code (decision 3). From the bank's source (section 5):

- Each holder gets one request shape: `POST /v1/evaluate` with `X-API-Key`, body `request_id` (a new UUID), `key_version: null` (the active version, which `key_holder.yaml` allows: `type: [integer, 'null']`), and `elements`. Holder 2's elements are holder 1's answer, unchanged.
- The registry gets `POST /v1/submissions` with `Idempotency-Key` (64 lower-case hex, `registry.yaml`) and the body `submission_json` builds. `Idempotency-Key` is the retry identity. `X-Request-ID` is a correlation id only (decision 2).

Nothing is stored or returned by layer 2 in this package. `docs/layer2/` is untracked on `main` at `0093a5c`, so this package's pull request is the one that adds `docs/layer2/` to the repository.

**Files the builder may create or edit.**

- `docs/layer2/SPEC_DECISIONS.md`: append only, one numbered entry per wire mismatch found between the bank client and the YAML, and one per section 9 ruling Shreyas gives. Each entry quotes his ruling.
- No other file. The other five files in `docs/layer2/` are committed by Cursor as they are.

**Edge cases, copied from `WORK_PACKAGES.md` WP0.** Null `key_version`. A UUID that is not 36 lower-case hex characters with hyphens. Duplicate `X-API-Key` or `Idempotency-Key`. Error codes the bank already classifies: 401, 409, 422, 429, 503.

What the files already say about them:

- Null `key_version` is legal (`key_holder.yaml` `EvaluateRequest`) and is what the bank sends.
- UUID spelling: the doubles accept a body UUID only as 36 lower-case hex with hyphens (otherwise `PATTERN_MISMATCH`). A path id in another spelling is 404. An `X-Request-ID` in another spelling is replaced by a new id (`contracts/fakes/README.md`).
- Duplicate headers: a repeated `X-API-Key` is 401 `UNAUTHENTICATED`. A repeated `Idempotency-Key` or `X-Request-ID` is 422 with `{"field": "<header>", "issue": "DUPLICATE"}` (`contracts/fakes/README.md`).
- Classes: section 5.1 table (`classification.py`).

**Builder commands.**

```
make contracts-check
```

Then read `KeyHolderHttpClient.evaluate` (`bank_agent/src/onepledge_bank_agent/infrastructure/http/key_holder_client.py`) and `RegistryHttpClient` (`.../http/registry_client.py`), and write the note: zero open wire mismatches, or one new decision entry per mismatch (WP0 done-check). The planning chat found none between those two clients and the two YAML files. The builder re-checks; it does not copy that finding. The items in section 9 are not wire mismatches. List them in the hand-back.

**Real output from the planning chat, 2026-11-24, commit `0093a5c`.** First run, before `make setup`:

```
contracts-check: onepledge-contracts check
make: onepledge-contracts: No such file or directory
make: *** [contracts-check] Error 1
```

The checker was not installed, so no contract had been checked. It was then installed from a copy of `contracts/fakes` into a virtual environment outside the repository, so nothing in the tree was written. The same target was run again with that environment's `bin/` first on `PATH`. The last line is the shell's exit status, printed by `echo "exit=$?"`:

```
contracts-check: onepledge-contracts check
contracts-check: OK   openapi bank_agent.yaml: valid OpenAPI 3.1
contracts-check: OK   openapi key_holder.yaml: valid OpenAPI 3.1
contracts-check: OK   openapi registry.yaml: valid OpenAPI 3.1
contracts-check: OK   openapi outside_records.yaml: valid OpenAPI 3.1
contracts-check: OK   examples: 54 examples validate against their schemas
contracts-check: OK   operations: 59 operation examples validate through the operation
contracts-check: OK   references: 59 externalValue links resolve, every example is indexed
contracts-check: OK   vectors tokenization.json: every OPRF value and token recomputes
contracts-check: OK   vectors signatures.json: every hash and signature recomputes
contracts-check: OK   cleaning cases: 207 cases have the documented shape
contracts-check: OK (4 files, 54 examples)
exit=0
```

`git status --short` afterwards showed only `?? docs/layer2/`.

**Cursor commands.**

```
make contracts-check
pre-commit run --files docs/layer2/*.md
```

Then the layer 1 gate, `make ci` (section 7 step 4). Stage `docs/layer2/` (the six files).

**Show it working.** Paste the `contracts-check: OK` line. Explain, from the client source, the one request the bank sends each holder and the one request it sends the registry (the bullets above). No service is running yet. That is the point of WP0.

**Stop.** Ask whether to build WP1.

---

## 11. WP1. Three packages, same shape as outside_records

Sources: `WORK_PACKAGES.md` WP1; `SPEC.md` sections 2 and 3; `SPEC_DECISIONS.md` decision 9; `outside_records/` (shape).

- Branch: `l2/wp1-package-shape`
- Pull request title: `Layer 2 WP1: Three packages, same shape as outside_records`
- Depends on: WP0 accepted.

**What it implements, and how it meets the bank.** Three installable packages with the layer split, the tooling and an architecture test. No business behavior beyond `/healthz` bodies that identify the service. The bank does not call these processes yet. Health is the proof the packages boot (`WORK_PACKAGES.md` WP1 "Show it working").

- `onepledge_key_holder`, `onepledge_registry`, `onepledge_ledger_verifier`, each with a src layout, `py.typed`, its own `pyproject.toml` and `README.md`. `key_holder/` and `registry/` also have `Dockerfile`, `alembic.ini`, `migrations/`, and `src/<package>/{domain,application,infrastructure,interface}/` (`SPEC.md` section 3).
- Copy from `outside_records/`: the src layout, the migrations package mapped beside `src/` in `pyproject.toml` (`package-dir`), the owner and app roles, the Alembic layout (`SPEC.md` section 2).
- FastAPI and Pydantic (decision 9).
- Health bodies are the contract `Health` schemas. Key holder: `status` `ok`, `service` `key_holder`, `holder_id`, `active_key_version` (required by `key_holder.yaml`). Registry: `status` `ok`, `service` `registry`. No `test_double` true.
- `active_key_version` must come from real configuration, not a constant (`WORK_PACKAGES.md` rule 4: no stubs).

**Files the builder may create or edit.**

- `key_holder/`, `registry/`, `ledger_verifier/` (new).
- `Makefile`, the `typecheck` target only: add one mypy line per new package, in the style of the existing lines. `make lint` already runs `ruff check .` and `ruff format --check .` over the whole repository, so it covers the new trees without an edit. Do not change `PACKAGES`, `COVERAGE_PACKAGES`, `setup` or `test`, and do not weaken a layer 1 gate (`WORK_PACKAGES.md` WP1 Create).
- `docs/layer2/SPEC_DECISIONS.md`, append only, for C3 once Shreyas rules.

**Edge cases, copied from `WORK_PACKAGES.md` WP1.** A committed `.env` fails the existing pre-commit hook. An em dash in a new file fails that hook.

Plus the WP1 architecture test (from "Test against Nayan"): it fails if a layer 2 `domain` module imports SQLAlchemy or FastAPI, and if `src/` imports `onepledge_contract_fakes` or `onepledge_bank_agent`.

**Builder commands.**

```
python3 -m pip install -e "key_holder[dev]" -e "registry[dev]" -e "ledger_verifier[dev]"
make lint && make typecheck
python3 -m pytest key_holder/tests registry/tests ledger_verifier/tests -q
```

**Cursor commands.** The builder commands above, then:

```
pre-commit run --files $(git diff --name-only; git ls-files --others --exclude-standard key_holder registry ledger_verifier)
```

Then `make ci`. Stage `key_holder/ registry/ ledger_verifier/ Makefile`.

**Show it working.** Serve each process and `curl` its `/healthz`, with the variables the new READMEs list:

```
HOLDER_ID=kh1 PORT=8301 onepledge-keyholder serve &
HOLDER_ID=kh2 PORT=8302 onepledge-keyholder serve &
PORT=8400 onepledge-registry serve &
curl -s http://127.0.0.1:8301/healthz
curl -s http://127.0.0.1:8302/healthz
curl -s http://127.0.0.1:8400/healthz
```

`PORT` is the layer 1 variable name for the port (`outside_records/README.md` "Configuration"). If the README the builder writes uses another name, use the README. The explanation says which process is kh1, which is kh2, and that the bank is not calling them yet.

**Section 9 items.** C3.

**Stop.** Ask whether to build WP2.

---

## 12. WP2. Key holder evaluate

Sources: `WORK_PACKAGES.md` WP2; `SPEC.md` section 4; `key_holder.yaml`; `SPEC_DECISIONS.md` decisions 2, 6, 11, 12; `contracts/fakes/src/onepledge_contract_fakes/key_holder/double.py`.

- Branch: `l2/wp2-key-holder-evaluate`
- Pull request title: `Layer 2 WP2: Key holder evaluate`
- Depends on: WP1.

**What it implements, and how it meets the bank.** Two processes of one package, kh1 on 8301 and kh2 on 8302, each with its own database and its own scalar.

- What arrives: the body `KeyHolderHttpClient.evaluate` builds (section 5.2). At kh1 the elements are the bank's blinded points. At kh2 they are kh1's answer, unchanged.
- What is decided (`SPEC.md` section 4.2; `double.py`):
  - Authenticate `X-API-Key` by its SHA-256. Missing or unknown: 401 `UNAUTHENTICATED`.
  - `key_version` null means the active version. A pinned version that is not active or retired-readable: 409 `KEY_VERSION_UNAVAILABLE`.
  - Each element is standard base64 of 32 bytes. The identity (32 zero bytes), a value that is not a ristretto255 point, or an all-zero result rejects the whole request: 422 `INVALID_ELEMENT`, `details` naming indexes only. The example is `{"field": "elements[3]", "issue": "NOT_A_VALID_POINT"}` (`contracts/examples/key_holder/evaluate.error.invalid_element.json`). No partial result is stored or returned.
  - The result is `oprf.mask(scalar).mask(oprf.data(element_bytes))`.
  - Over the per-member daily element cap: 429 `RATE_LIMITED` with `Retry-After` of at least 1.
  - In the double, every element is evaluated first, then the cap is consumed, then the log row is appended. A rejected request therefore leaves no log row (`double.py` `evaluate`; `RUBRIC.md` KH-03).
- What is stored: one `evaluation_log` row per accepted request (`eval_id`, `member_id`, `request_id`, `item_count`, `at`), and `rate_limits` (`member_id`, `window_start`, `items_used`, UTC day window). `key_versions` (`key_version`, `status`, `created_at`) and `api_credentials` (`credential_id`, `member_id`, `key_sha256`, `active`). No element, no code. The scalar lives in the environment (`SPEC.md` section 4.1; decision 12).
- What is returned: `request_id` (echoed, which the bank checks), `holder_id`, `key_version` (the version used for every element), and `elements[i]` as the evaluation of request `elements[i]`, same length. Evaluation is pure, so a repeat returns the same elements (decision 2).
- `GET /healthz` needs no key. Startup refuses a scalar that is not 32 bytes or is all zeros (`SPEC.md` section 4.2).
- Roles: the owner role migrates. The app role inserts into the log and the rate table and does not update `evaluation_log` (`SPEC.md` section 4.1).

**Files the builder may create or edit.** `key_holder/` only: schema migrations, `EvaluateBlinded`, HTTP routes, CLI, tests, `README.md` (provision, migrate, serve, and every variable it reads), `.env.example`.

**Edge cases, copied from `WORK_PACKAGES.md` WP2.** Identity point. One bad element in a batch of good ones. Non-canonical base64. Unknown pinned `key_version` (409). Missing key and duplicate `X-API-Key` (401). Daily cap with `Retry-After` (429). More than 1000 elements (413 or 422 as the YAML says; see C11). A second call with the same points returns the same elements. The evaluation log has a row and no element bytes.

**Builder commands.**

```
python3 -m pytest key_holder/tests -q
python3 -m coverage erase
python3 -m pytest key_holder/tests -q --cov=onepledge_key_holder --cov-branch --cov-report=
python3 -m coverage report --include="*/onepledge_key_holder/domain/*,*/onepledge_key_holder/application/*" --fail-under=85
python3 -m pytest contracts/fakes/tests/test_key_holder_double.py contracts/fakes/tests/test_key_holder_http.py -q
```

The oracle comparison (`WORK_PACKAGES.md` WP2 "Test against Nayan": with the same scalar, the response elements match `KeyHolderDouble` byte for byte) is a test under `key_holder/tests/` (`SPEC.md` section 10). `key_holder/README.md` names it (`RUBRIC.md` KH-02).

**Cursor commands.** The builder commands above, then `make ci`. Stage `key_holder/`.

**Show it working.** With kh1 and kh2 provisioned, migrated and serving as `key_holder/README.md` says, and `MEMBER_KEY` set to the member key the README's setup step made:

```
mkdir -p var/wp2
E0=$(python3 -c "import json;print(json.load(open('contracts/examples/key_holder/evaluate.request.json'))['elements'][0])")
R1=$(python3 -c "import uuid;print(uuid.uuid4())")
curl -s -X POST http://127.0.0.1:8301/v1/evaluate -H "X-API-Key: $MEMBER_KEY" -H "Content-Type: application/json" \
  -d "{\"request_id\":\"$R1\",\"key_version\":null,\"elements\":[\"$E0\"]}" | tee var/wp2/kh1.json
E1=$(python3 -c "import json;print(json.load(open('var/wp2/kh1.json'))['elements'][0])")
R2=$(python3 -c "import uuid;print(uuid.uuid4())")
curl -s -X POST http://127.0.0.1:8302/v1/evaluate -H "X-API-Key: $MEMBER_KEY" -H "Content-Type: application/json" \
  -d "{\"request_id\":\"$R2\",\"key_version\":null,\"elements\":[\"$E1\"]}"
psql "<kh1 database URL from the README>" -c "SELECT member_id, request_id, item_count, at FROM evaluation_log"
pg_dump "<kh1 database URL from the README>" | grep -cF "$E0"     # prints 0
```

`var/` is ignored by `.gitignore`. The explanation walks the two calls as `TokenizeLoans` makes them: the bank blinds, holder 1 multiplies by its scalar, holder 2 multiplies by its scalar, and the bank unblinds. It shows the log row and that the element text is not in the database.

**Section 9 items.** C2 (raise in the hand-back: Shreyas rules whether startup refuses the published vector scalars). C11.

**Stop.** Ask whether to build WP3.

---

## 13. WP3. Bank tokens match the published vector

Sources: `WORK_PACKAGES.md` WP3; `SPEC.md` G1; `contracts/examples/vectors/tokenization.json`; `bank_agent/.../application/tokenize_loans.py`.

- Branch: `l2/wp3-token-vector`
- Pull request title: `Layer 2 WP3: Bank tokens match the published vector`
- Depends on: WP2, and Shreyas's ruling on C2.

**What it implements, and how it meets the bank.** A test that proves the bank's tokenize stage accepts the real holders. It runs the bank's blind, evaluate, unblind, hash path against the two real holders and compares the result with `tokenization.json`.

- What arrives at the holders: what `TokenizeLoans._tokenize_chunk` sends (section 5.2).
- Bank code the test may call (test only, never `src/`). These names were seen in `tokenize_loans.py`: `onepledge_bank_agent.domain.tokenization` (`token_inputs`, `token_from_point`, `TOKENS_PER_LOAN`, `TokenInput.oprf_input()`), the `Blinder` port (`blind`, `unblind`) in `application/registry_ports.py`, `infrastructure/crypto/blinder.py` (`decode_point`), and `KeyHolderHttpClient`.
- What the vector holds: the loan's inputs, `holder_1_scalar_b64`, `holder_2_scalar_b64`, `blind_scalar_b64`, `key_version` `1.1`, and per kind `canonical_value`, `blinded`, `after_holder_1`, `after_holder_2`, `unblinded`, `token`. The input rule and the token rule are in its `input_rule` and `token_rule` fields.
- What is returned: eight 64-hex tokens per loan, kinds `ASSET`, `VIN`, `BORROWER`, `AMOUNT_EXACT`, `AMOUNT_100`, `AMOUNT_1000`, `DATE_DAY`, `DATE_MONTH`.
- Nothing under `bank_agent/` changes.

**Files the builder may create or edit.** `key_holder/tests/` (the test), `key_holder/README.md` (names the pytest target, `RUBRIC.md` TK-01), `key_holder/pyproject.toml` only for a dev dependency Shreyas approved under C3.

**Edge cases, copied from `WORK_PACKAGES.md` WP3.** Holder 2 receives holder 1's elements unchanged. The active version stays stable across the run, because a change is the bank's `KeyVersionChangedError`. An element that unblinds to the identity is the bank's `KEY_HOLDER_REJECTED`, so the holder must not return 32 zero bytes for a valid input.

**Builder commands.**

```
python3 -m pytest key_holder/tests -q
python3 -m pytest contracts/fakes/tests/test_key_holder_double.py contracts/fakes/tests/test_key_holder_http.py -q
git diff --stat -- bank_agent          # prints nothing
```

The done-check is "WP2 tests still pass, and the vector comparison exits 0".

**Cursor commands.** The builder commands above, then `make ci`. Stage `key_holder/`.

**Show it working.** Run the WP3 test target named in `key_holder/README.md` so that it prints the eight token hex strings next to the vector's. The explanation states that the tokens match because both holders used the vector's scalars (subject to C2), that `bank_agent/` was not edited, and that the bank's own math produced the codes.

**Section 9 items.** C2 (blocks this package until ruled), C3.

**Stop.** Ask whether to build WP4.

---

## 14. WP4. Registry database, ledger is the truth

Sources: `WORK_PACKAGES.md` WP4; `SPEC.md` sections 5 and 7; `registry.yaml` "Signatures and hashes"; `SPEC_DECISIONS.md` decisions 4, 5, 6; `contracts/fakes/src/onepledge_contract_fakes/registry/double.py`, `publisher.py`, `checkpoints.py`.

- Branch: `l2/wp4-registry-ledger`
- Pull request title: `Layer 2 WP4: Registry database, ledger is the truth`
- Depends on: WP3.

**What it implements, and how it meets the bank.** Schema, roles, the insert-only ledger, empty projections and a rebuild command. No submission HTTP yet. The bank has not submitted a loan yet. This package gives it a chain to verify (`SPEC.md` section 5.1).

- `ledger_events` row: `seq`, `event_type`, `payload` (canonical JSON), `payload_hash`, `prev_hash`, `event_hash`, `schema_version`, `recorded_at`. Member and asset live inside `payload`, never in extra columns. `seq` starts at 1 with no gaps (decision 5).
- Hashes (`registry.yaml`): `payload_hash` is the SHA-256 hex of the canonical JSON of the payload. `event_hash` is the SHA-256 hex of the canonical JSON of exactly `event_type`, `payload_hash`, `prev_hash`, `recorded_at`, `schema_version`, `seq`. `prev_hash` of seq 1 is 64 zeros. Canonical JSON is `json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")`.
- Updates and deletes on `ledger_events` are refused by trigger and by privilege, the owner included (`SPEC.md` section 5.1).
- Projections, rebuilt by replay from seq 1: `members`, `api_credentials` (SHA-256 keys), `submissions` (unique `(member_id, idempotency_key)`, response bytes, body hash), `pledge_locks` (unique `asset_token` where state is `LOCKED`), `lock_field_tokens`, `match_candidates`, `conflict_cases`, `certificates`, `checkpoints`, `outbox`, `access_log`. No `facility_ref` (decision 4; `SPEC.md` section 5.2).
- Genesis, as the double does it: one `MEMBER_JOINED` with payload `{"member_id": ...}` per configured member, in list order, then a checkpoint over the head, then a `CHECKPOINT_SIGNED` event with `checkpoint_seq`, `last_event_seq`, `head_hash`, and one `CHECKPOINT` outbox message per member (`double.py` lines 125 to 127; `publisher.py` `checkpoint`). With two members that is seq 1 and 2 `MEMBER_JOINED`, checkpoint 1 with `last_event_seq` 2, and seq 3 `CHECKPOINT_SIGNED`. Checkpoint 1 is signed (Ed25519 over the canonical body, `SPEC.md` section 7), so this package already needs the signing seed and a signer.

**Files the builder may create or edit.** `registry/` only: migrations for `SPEC.md` section 5, roles, domain hash chain, repositories, the rebuild command, CLI, tests, `README.md`, `.env.example`.

**Edge cases, copied from `WORK_PACKAGES.md` WP4.** `UPDATE` and `DELETE` on `ledger_events` fail as the owner and as the app role. A gap in `seq` fails the rebuild check. `prev_hash` of seq 1 other than 64 zeros fails. A `payload_hash` that does not match `payload` fails.

Plus from "Test against Nayan": hash bytes for a hand-built event match the canonical JSON paragraph in `registry.yaml`.

**Builder commands.**

```
python3 -m pytest registry/tests -q
python3 -m coverage erase
python3 -m pytest registry/tests -q --cov=onepledge_registry --cov-branch --cov-report=
python3 -m coverage report --include="*/onepledge_registry/domain/*,*/onepledge_registry/application/*" --fail-under=85
python3 -m pytest bank_agent/tests/unit -q --tb=no
```

The bank unit run is the regression check. A failure there is a layer 2 defect only if this package edited the bank. Otherwise stop and report it as his tree (`WORK_PACKAGES.md` WP4).

**Cursor commands.** The builder commands above, then `make ci`. Stage `registry/`.

**Show it working.**

```
onepledge-registry db provision           # with the variables registry/README.md lists
onepledge-registry db migrate
psql "<registry URL from the README>" -c "SELECT seq, event_type, event_hash FROM ledger_events ORDER BY seq"
psql "<registry URL from the README>" -c "SELECT last_event_seq, head_hash, signature FROM checkpoints"
psql "<registry app role URL>"   -c "UPDATE ledger_events SET payload_hash = payload_hash WHERE seq = 1"   # fails
psql "<registry owner role URL>" -c "UPDATE ledger_events SET payload_hash = payload_hash WHERE seq = 1"   # fails
```

The explanation says the ledger is the only history, and the bank has not submitted a loan yet.

**Section 9 items.** C7 (raise before writing the genesis code).

**Stop.** Ask whether to build WP5.

---

## 15. WP5. One transaction: decide, lock, append

Sources: `WORK_PACKAGES.md` WP5; `SPEC.md` section 6; `registry.yaml` "Decision rules", "Idempotency", `SubmissionRequest`, `SubmissionResult`; `SPEC_DECISIONS.md` decisions 2 and 7; `contracts/fakes/src/onepledge_contract_fakes/registry/matching.py` and `submissions.py`.

- Branch: `l2/wp5-decide-lock-append`
- Pull request title: `Layer 2 WP5: One transaction: decide, lock, append`
- Depends on: WP4, and Shreyas's ruling on C1.

**What it implements, and how it meets the bank.** `POST /v1/submissions`.

- What arrives: the bank's `submission_json` body with `Idempotency-Key` (section 5.3). `BACK_BOOK` and `PLEDGE` items carry `field_tokens`. `RELEASE` items do not.
- What is decided, in one transaction, items in request order (`SPEC.md` section 6; `registry.yaml`):
  - Idempotency. First key: decide, store the response, 201. Same key, same canonical body: the stored bytes, 200, `Idempotent-Replay: true`, no new events. Same key, different body: 409 `IDEMPOTENCY_KEY_REUSED`. The key is unique per `(member_id, idempotency_key)`. `X-Request-ID` does not deduplicate (decision 2).
  - `key_version` other than the active pair (wire form `<holder1>.<holder2>`, for example `1.1`): 409 `KEY_VERSION_MISMATCH`.
  - 422: a duplicate `asset_token` in one request (issue `DUPLICATE_ASSET_TOKEN`), `field_tokens` on a `RELEASE`, or `field_tokens` missing on a lock request.
  - Daily item cap: `BACK_BOOK` and `PLEDGE` items only. A `RELEASE` is never refused for the cap (429 `RATE_LIMITED` with `Retry-After` otherwise).
  - Rule A: the caller holds the active lock on `asset_token`. `LOCKED`, `already_held` true, no event.
  - Rule B: another member holds it. `CONFLICT`, basis `ASSET`. `EXACT` when `BORROWER`, `AMOUNT_EXACT` and `DATE_DAY` all equal the lock's, otherwise `LIKELY`. `CONFLICT_DETECTED` and `CASE_OPENED`, or reuse the `OPEN` case for the same claimant and lock.
  - Rule C: the asset is free, another member's active lock has the same `BORROWER` and one of `(AMOUNT_EXACT, DATE_DAY)`, `(AMOUNT_EXACT, DATE_MONTH)`, `(AMOUNT_100, DATE_DAY)`. `CONFLICT`, basis `FIELDS`, kind `LIKELY`. Most equal kinds wins. A tie goes to the oldest lock. A `(claimant asset_token, lock_id)` pair cleared `CLEARED_DATA_ERROR` is exempt. In the double, candidates are scanned in lock order and replaced only on strictly more matched kinds (`matching.py` `_field_agreement`).
  - No rule: `LOCKED`, `already_held` false, `LOCK_ACQUIRED`.
  - Release by the holder: `RELEASED`, `LOCK_RELEASED`, and every `OPEN` case on that lock becomes `CLEARED_LATE_RELEASE` with `CASE_RESOLVED`. Release by anyone else: `REJECTED`, reason `NOT_HOLDER`, no lock change.
  - `kinds_matched` lists every kind whose value equals the matched lock's, in the fixed kind order, `ASSET` included when the asset token matched.
  - After the item events: one `CERTIFICATE_ISSUED`, whose seq is `as_of_seq`, then a checkpoint and `CHECKPOINT_SIGNED` (`SPEC.md` section 6; `submissions.py` docstring).
- What is stored: events, locks, field tokens, candidates, cases, the certificate, the checkpoint, the outbox rows and the stored response, all in the one transaction (C5).
- What is returned: `SubmissionResult` (`submission_id`, `member_id`, `kind`, `key_version`, `status` `COMPLETED`, `received_at`, `as_of_seq`, `certificate_id`, `results`). The bank requires 201, or 200 with `Idempotent-Replay: true`, and maps 409 `KEY_VERSION_MISMATCH` to `KeyVersionMismatchError` (section 5.3).
- Privacy: another member's submission, certificate, case or outbox message is 404, never 403 (`SPEC.md` section 6).

**Files the builder may create or edit.** `registry/` only: `SubmitBatch`, `ReleaseLocks`, `OpenCase`, the matching rules, the transaction, the route, tests, `README.md`.

**Edge cases, copied from `WORK_PACKAGES.md` WP5.** Idempotent replay returns stored bytes and `Idempotent-Replay: true`. Same key, different body, is 409. Duplicate asset token in one request is 422. Rule A. Exact Rule B. Likely Rule B (amount or date changed). Rule C, including a tie that goes to the oldest lock, and a `CLEARED_DATA_ERROR` exemption. Release by a non-holder. Two concurrent submits of one asset token: one lock. A `RELEASE` under a full daily cap still proceeds. `KEY_VERSION_MISMATCH`.

**Builder commands.**

```
python3 -m pytest registry/tests -q
python3 -m coverage erase
python3 -m pytest registry/tests -q --cov=onepledge_registry --cov-branch --cov-report=
python3 -m coverage report --include="*/onepledge_registry/domain/*,*/onepledge_registry/application/*" --fail-under=85
python3 -m pytest contracts/fakes/tests/test_registry_rules.py contracts/fakes/tests/test_registry_submissions.py -q
```

"Test against Nayan": submit `contracts/examples/registry/submission.pledge.request.json` and `submission.release.request.json`. The decisions match the registry double for those bodies.

**Cursor commands.** The builder commands above, then `make ci`. Stage `registry/`.

**Show it working.** With the registry serving on 8400, members A and B configured, and `A_KEY` and `B_KEY` set as `registry/README.md` says:

```
KEY_A=$(python3 -c "import secrets;print(secrets.token_hex(32))")
KEY_B=$(python3 -c "import secrets;print(secrets.token_hex(32))")
BODY=contracts/examples/registry/submission.pledge.request.json
curl -s -i -X POST http://127.0.0.1:8400/v1/submissions -H "X-API-Key: $A_KEY" -H "Idempotency-Key: $KEY_A" \
  -H "Content-Type: application/json" --data-binary @$BODY
curl -s -i -X POST http://127.0.0.1:8400/v1/submissions -H "X-API-Key: $B_KEY" -H "Idempotency-Key: $KEY_B" \
  -H "Content-Type: application/json" --data-binary @$BODY
curl -s -i -X POST http://127.0.0.1:8400/v1/submissions -H "X-API-Key: $A_KEY" -H "Idempotency-Key: $KEY_A" \
  -H "Content-Type: application/json" --data-binary @$BODY
psql "<registry URL from the README>" -c "SELECT seq, event_type FROM ledger_events ORDER BY seq"
```

The example body's `key_version` is `1.1`, so the registry's active pair must be `1.1` for this run. The explanation names the rule that fired (A, B, C, or a new lock), why the second call did not create a second lock, and that the repeated call with the same `Idempotency-Key` returned the stored body.

**Section 9 items.** C1 (blocks the `CLEARED_DATA_ERROR` exemption test until ruled), C5.

**Stop.** Ask whether to build WP6.

---

## 16. WP6. Certificates, checkpoints, outbox

Sources: `WORK_PACKAGES.md` WP6; `SPEC.md` section 7; `registry.yaml` `SignedCertificate`, `SignedCheckpoint`, `OutboxPage`; `contracts/examples/vectors/signatures.json`.

- Branch: `l2/wp6-certs-checkpoints-outbox`
- Pull request title: `Layer 2 WP6: Certificates, checkpoints, outbox`
- Depends on: WP5.

**What it implements, and how it meets the bank.** The routes the bank uses after a submission, and proof the bank can verify a signature with `REGISTRY_PUBLIC_KEYS`.

- What arrives: `GET /v1/certificates/{certificate_id}`, `GET /v1/outbox?limit=`, `POST /v1/outbox/{message_id}/ack` from the bank (section 5.3), and `GET /v1/checkpoints/latest` (C6).
- What is decided:
  - Certificate: `body_hash` is the SHA-256 hex of the canonical body. `signature` is Ed25519 over those bytes, standard base64. Checkpoint: `signature` is Ed25519 over the canonical checkpoint body. `head_hash` equals the `event_hash` of `last_event_seq` (`SPEC.md` section 7).
  - Outbox fan-out, as the double does it: a new case queues one `ALERT` for the holder (the holder's own asset token, role `HOLDER`) and one for the claimant (the claimant's own asset token, role `CLAIMANT`). A resolution queues one `CASE_UPDATE` per party. Every checkpoint queues one `CHECKPOINT` per member. A repeated conflict while the case is `OPEN` reuses `case_id` and queues no new alert.
  - Ack: 204, also when already acknowledged. Another member's certificate or message: 404.
- What is returned: `SignedCertificate` (`body`, `body_hash`, `signature`, `signing_key_id`), `SignedCheckpoint` (`body`, `signature`, `signing_key_id`), `OutboxPage` (`messages`, `has_more`), oldest first, at least once until acknowledged (`registry.yaml`). The bank validates each known message against its whole schema and quarantines one that fails (section 5.3).
- The signing seed is an environment variable. The README states the key id and how to export the public key for the bank's `REGISTRY_PUBLIC_KEYS`. Layer 2 does not write that variable into the bank's code (`SPEC.md` section 7). The bank refuses the published test vector key (`bank_agent/README.md`), so a running registry must not sign with the vector seed.

**Files the builder may create or edit.** `registry/` only: `IssueCertificate`, `SignCheckpoint`, `DeliverOutbox` where WP5 did not already need them (C5), the four routes, tests, `README.md`.

**Edge cases, copied from `WORK_PACKAGES.md` WP6.** One flipped signature bit fails verification. A checkpoint whose `head_hash` is not the named event fails. An alert body that contains another member id fails the privacy test. Ack of an already acked message is still 204. Another member's certificate id is 404.

Plus from "Test against Nayan": recompute `contracts/examples/vectors/signatures.json` with the same canonical JSON. An alert payload contains only the caller's own asset token, which is what his alert sync stores.

**Builder commands.**

```
python3 -m pytest registry/tests -q
python3 -m coverage erase
python3 -m pytest registry/tests -q --cov=onepledge_registry --cov-branch --cov-report=
python3 -m coverage report --include="*/onepledge_registry/domain/*,*/onepledge_registry/application/*" --fail-under=85
make contracts-check
```

**Cursor commands.** The builder commands above, then `make ci`. Stage `registry/`.

**Show it working.** After the WP5 run (A locked, B conflicted), with `CERT_ID` taken from A's 201 body:

```
mkdir -p var/wp6
curl -s http://127.0.0.1:8400/v1/certificates/$CERT_ID -H "X-API-Key: $A_KEY" | tee var/wp6/cert.json
curl -s http://127.0.0.1:8400/v1/checkpoints/latest -H "X-API-Key: $A_KEY" | tee var/wp6/checkpoint.json
curl -s "http://127.0.0.1:8400/v1/outbox?limit=100" -H "X-API-Key: $A_KEY"
curl -s "http://127.0.0.1:8400/v1/outbox?limit=100" -H "X-API-Key: $B_KEY"
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8400/v1/certificates/$CERT_ID -H "X-API-Key: $B_KEY"   # 404
```

Verify both signatures with the exported public key and the canonical JSON rule of `registry.yaml` (for example a short `python3` script using `cryptography`'s `Ed25519PublicKey.verify`), and recompute `body_hash`. The explanation shows the signature checks, that each alert carries only that member's own asset token, and that this is the body his outbox sync (named `SyncRegistryOutbox` in `WORK_PACKAGES.md` WP6) will store.

**Section 9 items.** C5, C6.

**Stop.** Ask whether to build WP7.

---

## 17. WP7. Remaining reads and the operator view

Sources: `WORK_PACKAGES.md` WP7; `SPEC.md` section 7; `registry.yaml` paths `/v1/submissions/{submission_id}`, `/v1/cases`, `/v1/ledger/events`, `/healthz`; `SPEC_DECISIONS.md` decision 6.

- Branch: `l2/wp7-reads-operator-view`
- Pull request title: `Layer 2 WP7: Remaining reads and the operator view`
- Depends on: WP6.

**What it implements, and how it meets the bank.** The routes of `SPEC.md` section 7 that WP6 did not finish: submission read, cases, ledger views, health, 404 and 405, and the access log.

- What arrives from the bank: `GET /v1/ledger/events?view=headers&after_seq=&limit=` (section 5.3). The bank uses the headers to check that a new checkpoint extends the last one it stored (`registry.yaml` `listLedgerEvents`). `GET /v1/submissions/{id}` and `GET /v1/cases` are in the contract but not called by the bank client on `main` (C10). They are Contract A reads (`BUILD_PLAN.md` section 8).
- What is decided:
  - `view=headers` (default) is open to every member and has no payloads. `view=full` adds payloads (codes only) and needs the auditor or operator key, otherwise 403 `FORBIDDEN` (`registry.yaml`).
  - A bank key sees only its own submission, cases and outbox. Another member's resource is 404.
  - Unknown route: 404 `NOT_FOUND` in the error envelope. Wrong method: 405 `METHOD_NOT_ALLOWED` with `Allow`. No framework default body reaches a caller (`SPEC.md` section 7).
  - The access log stores the route template, status, credential and time. No query string, no element, no token (decision 6; `SPEC.md` section 5.2).
- What is returned: `SubmissionResult`, `CasePage` (`items`, `total`, `limit`, `offset`, ordered by `opened_seq`), `LedgerEventPage` (`events`, `head_seq`), `Health`.

**Files the builder may create or edit.** `registry/` only: the routes, the access log, tests, `README.md` (including how the auditor key is made).

**Edge cases, copied from `WORK_PACKAGES.md` WP7.** Unknown path is 404 `NOT_FOUND` in the error envelope. Wrong method is 405 with `Allow`. A repeated `X-Request-ID` is 422. Log lines contain the route template and no token.

The double's form of the repeated header: 422 `{"field": "X-Request-ID", "issue": "DUPLICATE"}`, refused before routing (`contracts/fakes/README.md`).

**Builder commands.**

```
python3 -m pytest registry/tests -q
python3 -m coverage erase
python3 -m pytest registry/tests -q --cov=onepledge_registry --cov-branch --cov-report=
python3 -m coverage report --include="*/onepledge_registry/domain/*,*/onepledge_registry/application/*" --fail-under=85
make contracts-check
```

Plus the done-check's dump: after a pledge of a known VIN's token, a dump of the registry tables contains that token and does not contain the VIN. That is a test under `registry/tests/`, using the vector's loan as the known preimage.

**Cursor commands.** The builder commands above, then `make ci`. Stage `registry/`.

**Show it working.**

```
curl -s -i "http://127.0.0.1:8400/v1/ledger/events?view=full" -H "X-API-Key: $A_KEY"         # 403 FORBIDDEN
curl -s -i "http://127.0.0.1:8400/v1/ledger/events?view=full" -H "X-API-Key: $AUDITOR_KEY"   # 200
curl -s -i http://127.0.0.1:8400/v1/no-such-route -H "X-API-Key: $A_KEY"                     # 404 NOT_FOUND
curl -s -i -X DELETE http://127.0.0.1:8400/v1/submissions -H "X-API-Key: $A_KEY"              # 405 with Allow
```

The explanation says why the bank key is refused, and what the auditor may see: codes, hashes and member ids.

**Section 9 items.** C1 (if Shreyas assigned the resolution route here), C10.

**Stop.** Ask whether to build WP8.

---

## 18. WP8. Ledger verifier

Sources: `WORK_PACKAGES.md` WP8; `SPEC.md` section 8; `registry.yaml` "Signatures and hashes"; `SPEC_DECISIONS.md` decisions 5 and 8.

- Branch: `l2/wp8-ledger-verifier`
- Pull request title: `Layer 2 WP8: Ledger verifier`
- Depends on: WP7.

**What it implements, and how it meets the bank.** `onepledge-ledger-verifier verify`. It reads the registry database as a reader, or an exported event list, and does not import the registry write path (`SPEC.md` section 8).

- What it checks: `seq` starts at 1 with no gaps. `prev_hash` of seq 1 is 64 zeros and each later `prev_hash` equals the previous `event_hash`. `payload_hash` and `event_hash` recompute under the canonical JSON rules. Each checkpoint signature verifies and its `head_hash` equals the named event. Each certificate `body_hash` matches the stored body and its signature verifies.
- What it returns: exit 0 when the chain holds. Non-zero, naming the first broken seq, when it does not.
- How it meets the bank: on a clean pledge ledger it exits 0, and the bank's certificate check would accept the same checkpoint. The public key is shared by configuration, not by an edit to the bank (`WORK_PACKAGES.md` WP8 "Test against Nayan"). The three tamper modes are the ones layer 1 uses for scene 9: an in-place `payload_hash`, a flipped checkpoint signature bit, a certificate body changed without re-signing (`SPEC.md` section 8). For the certificate, the field to change is `summary.locked` (C8).
- No Sigstore Rekor (decision 8).

**Files the builder may create or edit.** `ledger_verifier/` only, including `ledger_verifier/README.md` with the reviewer's method for editing a copy as a superuser (`RUBRIC.md` VER-02).

**Edge cases, copied from `WORK_PACKAGES.md` WP8.** A gap in `seq`. Seq 1 with a non-zero `prev_hash`. A checkpoint signed over an older head.

Plus from "Test against Nayan": apply the scene 9 edits on a copy of the database. The verifier names the broken seq. The original database is unchanged and still verifies.

**Builder commands.**

```
python3 -m pytest ledger_verifier/tests -q
```

**Cursor commands.** The builder command above, then `make ci`. Stage `ledger_verifier/`.

**Show it working.**

```
onepledge-ledger-verifier verify          # against the live database, arguments as ledger_verifier/README.md says
echo "exit=$?"                            # exit=0
```

Then stop the registry and copy its database. PostgreSQL's `CREATE DATABASE <copy> TEMPLATE <registry db>` works only while nothing is connected to the source. As a superuser on the copy, by the README's method, set one `payload_hash` to 64 zeros, and run the verifier against the copy. It exits non-zero and names that seq. Run it again on the original: exit 0. The explanation says the verifier recomputes the hashes itself and does not trust the registry process. This is scene 9 without the auditor screen, which Vedant builds.

**Section 9 items.** C8.

**Stop.** Ask whether to build WP9.

---

## 19. WP9. Point the bank at these processes

Sources: `WORK_PACKAGES.md` WP9; `SPEC.md` section 9; `bank_agent/README.md` "Commands" and "Configuration"; `RUBRIC.md` JOIN-01 and JOIN-02.

- Branch: `l2/wp9-point-bank`
- Pull request title: `Layer 2 WP9: Point the bank at these processes`
- Depends on: WP8, and Shreyas's ruling on C9.

**What it implements, and how it meets the bank.** No new service behavior. The unchanged bank is pointed at kh1, kh2 and the registry by environment variables only, and scenes 2, 3, 4, 6, 7, 8 and 9 run against the real processes (`SPEC.md` section 9). Scenes 1 and 5 stay inside the bank. An invented loan with no title never reaches the registry.

| Bank variable | Points at |
|---|---|
| `KEY_HOLDER_1_URL`, `KEY_HOLDER_1_API_KEY` | kh1 |
| `KEY_HOLDER_2_URL`, `KEY_HOLDER_2_API_KEY` | kh2 |
| `REGISTRY_URL`, `REGISTRY_API_KEY`, `REGISTRY_MEMBER_ID` | the registry |
| `REGISTRY_PUBLIC_KEYS` | the registry signing public key |

The call order the bank already uses, which layer 2 accepts unchanged (`SPEC.md` section 9): blind each field locally; evaluate on holder 1 with `key_version` null; evaluate on holder 2 with holder 1's elements; unblind and derive the 64-hex token; `POST /v1/submissions` with `Idempotency-Key`; fetch the certificate, poll the outbox, ack, verify checkpoints.

The bank command that runs these steps is `onepledge-bank worker --once`, which advances every uploaded tape to `COMPLETED`. `onepledge-bank integrity verify` then shows what the bank verified (`bank_agent/README.md` "Running it by hand"). How a tape is uploaded into the bank is open (C9).

**Files the builder may create or edit.** A README section in `key_holder/README.md` and `registry/README.md` that lists the environment variables (layer 2 owns those folders; the root `README.md` is not layer 2's, `CONTEXT.md` "Boundaries"). A script or a documented command sequence under `registry/` or `key_holder/` only if it does not wrap or patch his code. No edit under `bank_agent/`.

**Edge cases, copied from `WORK_PACKAGES.md` WP9.** Three members. Planted exact copy. Amount change still `LIKELY`. Release, then the other member locks with no alert. 100 concurrent submits, one lock each time. Database scan finds no VIN, name, amount or date in the key holder or registry databases.

**Builder commands.**

```
make test                                  # his packages, against the doubles, not the layer 2 URLs
git diff --stat -- bank_agent datagen outside_records evaluation contracts/fakes docs/layer1 contracts/key_holder.yaml contracts/registry.yaml   # prints nothing
```

Plus the scene notes with command output (WP9 done-check).

**Cursor commands.** The builder commands above, then `make ci`. Stage the README files and any script the package added under `registry/` or `key_holder/`.

**Show it working.** With his unchanged bank pointed at the three URLs, run one honest pledge and one double pledge. The explanation follows a single loan from his tokenize stage through both holders, into the registry transaction, out as a certificate and two alerts. Quote the status lines from his pipeline. The command sequence can only be written once C9 is ruled. Until then this package cannot show it working, and is not done (`WORK_PACKAGES.md` rule 7).

**Section 9 items.** C9 (blocks this package).

**Stop.** The layer is ready for rubric review. The implementer does not mark the rubric passed.
