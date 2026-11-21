# OnePledge layer 1: product contract (SPEC)

Version 1.0, 2026-11-21. Owner: Nayan (layer 1). Binding inputs: `docs/BUILD_PLAN.md`, `docs/layer1/CONTEXT.md`, `contracts/*.yaml`.

Layer 1 is `datagen/`, `outside_records/`, `bank_agent/`, `evaluation/`, and the contract test doubles in `contracts/fakes/`.

How to read this file:

- "MUST", "MUST NOT" and plain present tense statements ("the stage inserts...") are requirements. There are no optional features unless a sentence says "optional".
- Where this file and a contract YAML disagree on a wire format, the YAML wins and this file is wrong; report it instead of guessing.
- Where this file and `BUILD_PLAN.md` disagree, this file wins for layer 1. Every such difference is listed in section 24.
- Release is decided only by independent review against `docs/layer1/RUBRIC.md`. An implementer never declares completion.
- No em dashes anywhere in code, docs or output.

Contents: 1 Goals. 2 Repository and tooling. 3 Configuration. 4 Shared conventions. 5 datagen. 6 outside_records. 7 Contract test doubles. 8 Bank agent domain model. 9 Normalisation and tokenisation. 10 Tape intake and cleaning. 11 Local checks. 12 Bank database. 13 Pipeline. 14 Registry interaction. 15 Checkpoints, certificates, integrity. 16 Security. 17 Audit and lineage. 18 Bank agent HTTP API. 19 Command line interfaces. 20 evaluation. 21 Tests. 22 Docker. 23 Known limitations. 24 Differences from BUILD_PLAN.

---

## 1. Goals and non-goals

### 1.1 Goals

G1. From a fresh clone and an empty PostgreSQL, one command (`make demo`) generates the synthetic data, provisions every database, starts every layer 1 service plus the test doubles, walks scenes 1, 2, 3, 4, 5, 7 and the bank side of 8 and 9, prints a clean narrative report, and prints the scorecard.

G2. The bank agent is a restartable, idempotent pipeline: ingest, clean, check, tokenise, submit, certify. Killing the process at any instant and restarting it yields the same final state as an uninterrupted run, and the registry never receives a second distinct request for the same work.

G3. No raw loan value (VIN, borrower reference, lender loan number, amount, date) leaves a bank agent except the VIN lookups to outside records, and none appears in any log line, error response or URL of the bank agent.

G4. Integrity is enforced by the database, not by convention: `loans_raw`, `audit_log` and the other evidence tables are insert-only by trigger and by privilege, the runtime role is not the owner, and the audit log is hash chained.

G5. Everything a risk, security or audit reader would ask for is visible: a per loan lineage view, a bank audit log, an integrity report, signed certificates and checkpoints that the bank verifies itself.

G6. The scorecard measures, per planted case type, what was caught, what was missed and what was a false alarm, against an answer key that only `evaluation/` reads. The system contains no expected answers.

G7. Teammates can build on it: contracts with examples and vectors, test doubles that behave as the contract says, a README that works from a fresh clone, Dockerfiles and a compose file.

### 1.2 Non-goals

N1. The real key holder, registry and ledger verifier (layer 2). Only test doubles are built, in `contracts/fakes/`.
N2. Any web screen (layer 3). Layer 1 exposes HTTP APIs and command line tools only.
N3. An LLM adapter for alert explanations. Layer 1 ships the `AlertExplainer` interface and the deterministic template implementation only (see 23).
N4. Key rotation as a full protocol. `key_version` is stored and checked; migrating existing locks to a new key is not built (see 23).
N5. TLS, replication, partitioning, streaming, multi-node deployment, GitHub Actions workflows (layer 3 owns CI; `make ci` is the layer 1 gate).
N6. Real data. Every value is invented.

---

## 2. Repository layout and tooling

### 2.1 Layout

```
onepledge/
  README.md                      layer 1 quickstart (fresh clone to demo)
  Makefile
  pyproject.toml                 root: tool configuration only (ruff, mypy, pytest, coverage). Not a package.
  .env.example  .gitignore  .pre-commit-config.yaml  .dockerignore  CODEOWNERS
  docker-compose.layer1.yml
  contracts/
    README.md  bank_agent.yaml  key_holder.yaml  registry.yaml  outside_records.yaml
    examples/                    JSON examples, index.json, vectors/, tapes/ (20 loan example, see 5.9)
    fakes/                       package onepledge_contract_fakes (TEST DOUBLES)
      pyproject.toml  README.md  Dockerfile
      src/onepledge_contract_fakes/{key_holder,registry,contracts_check}/
      tests/
  datagen/                       package onepledge_datagen
    pyproject.toml  README.md
    src/onepledge_datagen/{domain,application,infrastructure,interface}/
    tests/
  outside_records/               package onepledge_outside_records
    pyproject.toml  README.md  Dockerfile  alembic.ini  migrations/
    src/onepledge_outside_records/{domain,application,infrastructure,interface}/
    tests/
  bank_agent/                    package onepledge_bank_agent
    pyproject.toml  README.md  Dockerfile  alembic.ini  migrations/
    src/onepledge_bank_agent/{domain,application,infrastructure,interface}/
    tests/{unit,integration,contract}/
  evaluation/                    package onepledge_evaluation
    pyproject.toml  README.md
    src/onepledge_evaluation/{runtime,replay,scorecard,demo,interface}/
    tests/
  docs/layer1/                   CONTEXT.md SPEC.md RUBRIC.md WORK_PACKAGES.md ARCHITECTURE.md THREAT_MODEL_L1.md
  var/                           gitignored: data/, log/, run/, reports/
```

Rules:

- One installable Python package per component, src layout, each with its own `pyproject.toml` (build backend `setuptools>=68`), `requires-python = ">=3.12"`, a `py.typed` marker, and console scripts as listed in section 19.
- Every runtime dependency is declared in the `pyproject.toml` of the package that imports it. Test and dev dependencies are in the `dev` optional dependency group of that package.
- Import boundaries (checked by a test in each package, see 21.6):
  - `onepledge_bank_agent` imports none of the other four packages in `src/`. Its tests may import `onepledge_contract_fakes` and `onepledge_outside_records`.
  - `onepledge_datagen`, `onepledge_outside_records`, `onepledge_contract_fakes` import none of the others.
  - `onepledge_evaluation` talks to services over HTTP and starts them as subprocesses by console script name. It may import `onepledge_datagen` (to generate data). It imports no bank agent module.
  - Inside each service package: `domain` imports only the standard library; `application` imports `domain`; `infrastructure` imports `domain` and `application` interfaces; `interface` wires everything. SQL (SQLAlchemy, psycopg, text queries) appears only under `infrastructure/`. FastAPI appears only under `interface/`. `httpx` appears only under `infrastructure/` (and in `onepledge_evaluation`).
- Allowed third party runtime packages: fastapi, uvicorn, pydantic, sqlalchemy, alembic, psycopg, argon2-cffi, PyJWT, cryptography, oprf, structlog, httpx, python-multipart, PyYAML. Dev only: pytest, pytest-cov, hypothesis, ruff, mypy, openapi-spec-validator, jsonschema, pre-commit. Nothing else without a line in section 24.
- Command line parsing uses `argparse` (standard library).

### 2.2 Quality configuration (root `pyproject.toml`)

- ruff: `target-version = "py312"`, `line-length = 110`, lint rules `E, F, W, I, B, UP, N, S, C4, SIM, RET, PTH, RUF, ANN, T20, PL`, with `S101` ignored under `tests/` only, and `PLR2004` ignored under `tests/` only. `ruff format` is the formatter. No `# noqa` without a rule code and a reason on the same line.
- mypy: `strict = true`, `python_version = "3.12"`, `warn_unreachable = true`, run over every `src/` and `tests/` tree. The only `ignore_missing_imports` override is for `oprf` and `oblivious` (they ship no type information; verified on 2026-11-21). No `type: ignore` without an error code and a reason.
- Complexity limits (ruff `PLR0912`, `PLR0915`, `C901`): max branches 12, max statements 50, max complexity 10. No module in `src/` longer than 400 lines. No function longer than 60 lines. No class with more than 15 public methods.
- pytest: markers `unit`, `integration`, `contract`, `e2e`; `--strict-markers`; `-ra`; warnings are errors except those a third party library raises that cannot be fixed here (each listed explicitly in `filterwarnings`).
- coverage: branch coverage on. Gate: at least 85% line coverage on `onepledge_bank_agent.domain` plus `onepledge_bank_agent.application` combined, and at least 85% on each of `onepledge_datagen`, `onepledge_outside_records`, `onepledge_contract_fakes`, `onepledge_evaluation.scorecard`.
- pre-commit: ruff (lint and format check), mypy, and a hook that rejects any em dash character (U+2014) and any committed `.env` file.

### 2.3 Makefile targets

All targets run from the repository root and work on the build machine without Docker. `PYTHON ?= python3`, `PIP_FLAGS ?=` (the README tells a user on an externally managed system Python to pass `PIP_FLAGS=--break-system-packages`, or to use a virtual environment), `SEED ?= 20261117`, `PROFILE ?= full`.

| Target | Does |
|---|---|
| `help` (default) | Lists targets with one line each. |
| `setup` | `pip install $(PIP_FLAGS) -e` each of the five packages with `[dev]`, then `make env` if `.env` is missing. |
| `env` | `onepledge-eval env init` (creates `.env` from `.env.example` with fresh random secrets, see 3.2). Never overwrites an existing `.env` unless `FORCE=1`. |
| `lint` | `ruff check .` and `ruff format --check .` |
| `format` | `ruff format .` and `ruff check --fix .` |
| `typecheck` | `mypy` over all `src/` and `tests/` trees. |
| `contracts-check` | `onepledge-contracts check` (see 7.4). |
| `test` | All tests of all five packages, all markers, with the coverage gates of 2.2. |
| `test-fast` | Only `unit` and `contract` markers. |
| `ci` | `lint`, `typecheck`, `contracts-check`, `test`, in that order, stopping at the first failure. |
| `db-up` | Provisions roles and databases for the three banks and outside records on the server at `PG_ADMIN_URL` (idempotent). With `DOCKER=1` it first runs `docker compose -f docker-compose.layer1.yml up -d postgres`. |
| `migrate` | Runs Alembic to head on all four databases as their owner roles. |
| `db-reset` | Drops the four databases and their roles (only names starting with `ONEPLEDGE_DB_PREFIX`), then `db-up` and `migrate`. |
| `data` | `onepledge-datagen generate --seed $(SEED) --profile $(PROFILE) --out var/data/$(PROFILE)-$(SEED)`. Skips with one line when that directory already holds a complete data set whose checksums verify; `FORCE=1` regenerates. |
| `seed` | Loads outside records, bank reference data (lenders, facilities) and demo users from `var/data/$(PROFILE)-$(SEED)`. Idempotent. |
| `up` / `down` / `status` | Start, stop, show the local stack (3 bank agents, outside records, 2 key holder doubles, registry double) via `onepledge-eval stack`. |
| `demo` | `onepledge-eval demo --seed $(SEED) --profile $(PROFILE)`: from a fresh database to the narrative report and scorecard (20.5). |
| `scorecard` | `onepledge-eval scorecard --seed $(SEED) --profile $(PROFILE)`: fresh database, replay every tape, print and write the scorecard (20.4). |
| `clean` | Removes `var/`, caches and build artefacts. Does not touch databases. |

Every target prints what it is doing in one line per step and exits non-zero on failure. No target prints a Python traceback for an expected failure (missing `.env`, PostgreSQL unreachable, port in use): it prints one sentence saying what is wrong and what to run.

### 2.4 Documentation deliverables

| File | Content |
|---|---|
| `README.md` | What layer 1 is in five lines; prerequisites; fresh clone to `make demo` in numbered steps that work verbatim on the build machine and (with Docker) on a laptop; the make targets; where reports and logs go; demo logins; how to run a second stack side by side by changing `ONEPLEDGE_DB_PREFIX` and `ONEPLEDGE_PORT_BASE` in `.env`; troubleshooting for the three expected failures (no `.env`, PostgreSQL unreachable, port in use); links to the documents below. |
| `<package>/README.md` (five) | Purpose, layout, commands, configuration variables, how to run its tests. The doubles' README repeats that they are test doubles. |
| `docs/layer1/ARCHITECTURE.md` | Component diagram (text), the path of one tape through the stages, the layering rule with one example per layer, the table of tables, how to add a cleaning rule, a local check and an endpoint. |
| `docs/layer1/THREAT_MODEL_L1.md` | For each threat in BUILD_PLAN section 7 that touches layer 1: the control, where it is implemented (file), and the test or rubric id that proves it. Plus the limitations of section 23 that are security relevant. |

Documentation states only what is implemented. No future tense promises, no marketing words.

---

## 3. Configuration

### 3.1 Principles

- Configuration comes only from environment variables. Each service has one immutable `Settings` class (pydantic `BaseModel`, `frozen=True`) built by `Settings.from_env(environ: Mapping[str, str])`. `os.environ` is referenced in exactly one module per package, `settings.py`, which also offers `load_settings()` (it calls `Settings.from_env(os.environ)`). Missing or malformed required settings stop startup with one line naming the variable (never its value).
- Secrets are never committed. `.env` is gitignored. `.env.example` contains every variable with a comment, and the literal placeholder `CHANGE_ME` for every secret.
- `Settings.__repr__` and any dump of settings mask every secret.

### 3.2 Root `.env` (written by `onepledge-eval env init`)

`env init` copies `.env.example`, replaces every `CHANGE_ME` with a fresh value from `secrets` (32 random bytes, base64 or hex as the variable needs), derives the public values, and writes the file with mode 0600.

| Variable | Default or rule | Meaning |
|---|---|---|
| `PG_ADMIN_URL` | `postgresql://postgres@localhost:55432/postgres` | Superuser URL, used only for provisioning and by tests. |
| `PG_HOST`, `PG_PORT` | `localhost`, `55432` | Used to build per service URLs. |
| `ONEPLEDGE_DB_PREFIX` | `onepledge` | Prefix of every database and role name. |
| `ONEPLEDGE_PORT_BASE` | `8000` | Ports are base plus the offsets below. |
| `BANK_<CODE>_DB_OWNER_PASSWORD`, `BANK_<CODE>_DB_APP_PASSWORD` | secret | For `<CODE>` in HARBOR, NORTHGATE, CALDER. |
| `BANK_<CODE>_JWT_SECRET` | secret, 32 bytes base64 | Token signing secret of that bank. |
| `BANK_<CODE>_MEMBER_ID` | random UUID | Registry member id of that bank. |
| `BANK_<CODE>_REGISTRY_API_KEY`, `BANK_<CODE>_KH1_API_KEY`, `BANK_<CODE>_KH2_API_KEY`, `BANK_<CODE>_OUTSIDE_API_KEY` | secret, `opk_` plus 43 url-safe characters | Outbound service keys of that bank. |
| `OUTSIDE_DB_OWNER_PASSWORD`, `OUTSIDE_DB_APP_PASSWORD` | secret | |
| `FAKE_KH1_SCALAR_B64`, `FAKE_KH2_SCALAR_B64` | `oprf.mask.random().to_base64()` | Secret scalars of the two key holder doubles, key version 1. |
| `FAKE_REGISTRY_SIGNING_SEED_B64` | secret, 32 bytes | Ed25519 seed of the registry double. |
| `REGISTRY_SIGNING_KEY_ID` | `registry-local-1` | |
| `REGISTRY_SIGNING_PUBLIC_KEY_B64` | derived from the seed | Trust anchor given to every bank agent. |
| `FAKE_AUDITOR_API_KEY` | secret | Auditor key for `view=full` on the registry double. |
| `FAKE_TEST_CONTROL_KEY` | secret | Guards `/_test/` endpoints of the doubles. |
| `DEMO_USER_PASSWORD` | secret, 24 url-safe characters | Password of the nine seeded demo users. |
| `LOG_LEVEL` | `INFO` | |

Port offsets from `ONEPLEDGE_PORT_BASE`: bank agents HARBOR +101, NORTHGATE +102, CALDER +103; outside records +200; key holder 1 +301; key holder 2 +302; registry +400. All services bind `127.0.0.1` locally (and `0.0.0.0` in containers via `BIND_HOST`).

Database and role names: bank database `{prefix}_bank_{code_lower}`, outside records `{prefix}_outside`; roles `{database}_owner` and `{database}_app`.

### 3.3 Per process environment

The stack launcher (20.2) and the compose file translate the root `.env` into the un-prefixed variables each process reads.

Bank agent: `BANK_CODE`, `BANK_NAME`, `BIND_HOST`, `PORT`, `DATABASE_URL` (app role), `MIGRATION_DATABASE_URL` (owner role; read only by `onepledge-bank db migrate` and never by `serve` or `worker`), `DB_APP_ROLE`, `JWT_SECRET`, `REGISTRY_URL`, `REGISTRY_API_KEY`, `REGISTRY_MEMBER_ID`, `REGISTRY_PUBLIC_KEYS` (JSON object key id to base64 public key), `KEY_HOLDER_1_URL`, `KEY_HOLDER_1_API_KEY`, `KEY_HOLDER_2_URL`, `KEY_HOLDER_2_API_KEY`, `OUTSIDE_RECORDS_URL`, `OUTSIDE_RECORDS_API_KEY`, `LOG_LEVEL`, and the tunables below.

| Tunable | Default | Meaning |
|---|---|---|
| `PIPELINE_WORKER_ENABLED` | `true` | Run the pipeline worker thread inside `serve`. |
| `PIPELINE_POLL_SECONDS` | `1` | Worker idle poll interval. |
| `PIPELINE_MAX_ATTEMPTS` | `8` | Failed attempts at one stage before FAILED. |
| `OUTBOX_POLL_SECONDS` | `2` | Registry outbox poll interval. |
| `CHECK_CHUNK_LOANS` | `100` | Loans per check transaction. |
| `TOKENIZE_CHUNK_LOANS` | `100` | Loans per key holder round (800 elements). |
| `SUBMIT_BATCH_ITEMS` | `500` | Items per registry request (contract maximum). |
| `HTTP_CONNECT_TIMEOUT_SECONDS`, `HTTP_READ_TIMEOUT_SECONDS` | `2`, `10` | Outbound HTTP timeouts. |
| `TITLE_PENDING_WINDOW_DAYS` | `30` | See 11. |
| `FIRST_PAYMENT_GRACE_DAYS` | `45` | See 11. |
| `PAYMENT_WINDOW_DAYS` | `90` | See 11. |
| `CONTRACT_DATE_TOLERANCE_DAYS` | `3` | See 11. |
| `ACCESS_TOKEN_TTL_SECONDS` | `900` | |
| `LOGIN_MAX_FAILURES_PER_EMAIL`, `LOGIN_MAX_FAILURES_PER_ADDRESS`, `LOGIN_WINDOW_SECONDS` | `5`, `20`, `900` | See 16.2. |
| `HALT_ON_REGISTRY_INTEGRITY_FAILURE` | `true` | See 15.4. |
| `CORS_ALLOWED_ORIGINS` | empty | Comma separated origins for the console. Empty means no CORS headers. |
| `EXPOSE_API_DOCS` | `false` | Serve `/docs` and `/openapi.json`. |

Outside records: `BIND_HOST`, `PORT`, `DATABASE_URL` (app role, read-only), `MIGRATION_DATABASE_URL`, `DB_APP_ROLE`, `LOG_LEVEL`. Client keys live hashed in its database (6.2).

Key holder double: `HOLDER_ID` (`kh1` or `kh2`), `PORT`, `FAKE_KH_KEYS` (JSON object version to base64 scalar), `FAKE_KH_ACTIVE_VERSION`, `FAKE_KH_MEMBERS` (JSON list of `{member_id, api_key_sha256}`), `FAKE_KH_DAILY_ELEMENT_CAP` (default 1000000), `FAKE_TEST_CONTROLS` (`true` or `false`), `FAKE_TEST_CONTROL_KEY`.

Registry double: `PORT`, `FAKE_REGISTRY_SIGNING_SEED_B64`, `REGISTRY_SIGNING_KEY_ID`, `FAKE_REGISTRY_MEMBERS` (JSON list of `{member_id, api_key_sha256}`), `FAKE_REGISTRY_AUDITOR_KEY_SHA256`, `FAKE_REGISTRY_KEY_VERSION` (default `1.1`), `FAKE_REGISTRY_DAILY_ITEM_CAP` (default 1000000), `FAKE_TEST_CONTROLS`, `FAKE_TEST_CONTROL_KEY`.

---

## 4. Shared conventions

- IDs are UUID version 4, lower case with hyphens. Exceptions are stated where they occur.
- Times are timezone aware UTC. In JSON they are RFC 3339 with `Z`. Clocks are injected (`Clock` interface with `now() -> datetime`); no code under `domain/` or `application/` calls `datetime.now` directly.
- Dates are ISO `YYYY-MM-DD`.
- Money is `Money`, integer cents, never a float. Parsing uses `decimal.Decimal`.
- Statuses and codes are upper snake case members of `enum.StrEnum` classes.
- Canonical JSON (used for every hash and signature): `json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")`. Floats are forbidden in canonical input (the function raises `TypeError` on a float).
- Hashes are SHA-256, lower case hex. Signatures are Ed25519, standard base64 with padding.
- Service API keys: generated as `opk_` plus 43 url-safe base64 characters (256 bits). A server stores only `sha256(key)` hex and compares with `hmac.compare_digest`.
- Error responses of every layer 1 service use the `ErrorResponse` shape of its contract. `message` is a fixed sentence per `code`, stored in one table in the code base. No error message is built by string formatting with request data.
- Every HTTP service accepts `X-Request-ID` (a UUID; anything else is replaced), binds it into the log context, returns it as a response header, and puts it in every error body. The bank agent forwards its request id on outbound calls.
- Business errors are named exception classes deriving from one `DomainError` per package, each with a `code` attribute. Exceptions never carry a raw loan value in their message or arguments.

---

## 5. datagen

Purpose: produce, from a seed, everything the demo and the scorecard need: the lenders' tape files exactly as a lender would send them, the outside records, the reference data, a timeline, and the answer key.

### 5.1 Command

```
onepledge-datagen generate --seed INT --profile {full,small,example} --out DIR
onepledge-datagen verify --data-dir DIR
```

`generate` refuses to write into a non-empty directory unless `--force`. `verify` re-checks every invariant of 5.8 on an existing directory and exits non-zero with a list of violated invariants.

### 5.2 Determinism

- All randomness comes from `random.Random` instances derived from the seed: `Random(f"{seed}:{stream_name}")`. No wall clock, no `uuid.uuid4()`, no `os.urandom`, no reliance on set or dict iteration order of unsorted data. UUIDs are `uuid.UUID(int=rng.getrandbits(128), version=4)`.
- Same seed, same profile, same generator version: every output file is byte identical. `checksums.json` lists the SHA-256 of every other output file.
- Different seeds give different VINs, borrowers, amounts and case placements, with the same counts per case type.

### 5.3 Output directory

```
DIR/
  manifest.json          timeline of tape submissions (no fraud labels)
  reference.json         banks, lenders, facilities, demo users (no passwords)
  checksums.json
  tapes/<BANK_CODE>/<STEP>_<FACILITY_CODE>_<KIND>.csv
  outside_records/titles.csv  payments.csv  contracts.csv
  answer_key/answer_key.json
```

Tape file names and `manifest.json` carry no hint of which rows are planted. Only code under `onepledge_evaluation` may open anything under `answer_key/` (rule enforced by RUBRIC and by the test of 21.6).

`manifest.json`:

```json
{"generator_version": "1.0.0", "seed": 20261117, "profile": "full", "timeline_end": "2026-11-16",
 "steps": [{"step_id": "S01", "scene": 1, "bank_code": "HARBOR", "facility_code": "HARBOR-SUMMIT",
            "kind": "BACK_BOOK", "as_of_date": "2026-09-30", "file": "tapes/HARBOR/S01_HARBOR-SUMMIT_BACK_BOOK.csv",
            "uploader_role": "ANALYST", "concurrent_group": null, "data_rows": 1131}]}
```

`reference.json`: `banks` (`bank_code`, `bank_name`), `lenders` (`lender_id`, `lender_code`, `legal_name`, `lei`), `facilities` (`facility_id`, `facility_code`, `bank_code`, `lender_code`, `name`, `credit_limit_cents`, `advance_rate_bps`, `opened_on`), `demo_users` (`bank_code`, `email`, `display_name`, `role`).

### 5.4 Entities

Banks: `HARBOR` Harbor Bank, `NORTHGATE` Northgate Bank, `CALDER` Calder Credit Fund.

Lenders: `SUMMIT` Summit Auto Finance LLC, LEI `00000ONEPLEDGESUM134` (cheats); `LAKESIDE` Lakeside Motor Credit Inc, LEI `00000ONEPLEDGELAK198` (honest). Both LEIs are invented and carry valid ISO 17442 check digits.

Facilities (one credit line each): `HARBOR-SUMMIT`, `HARBOR-LAKESIDE`, `NORTHGATE-SUMMIT`, `NORTHGATE-LAKESIDE`, `CALDER-SUMMIT`. Advance rate 8500 bps for Summit lines, 9000 for Lakeside lines. Credit limit 75,000,000.00 dollars each. `opened_on` 2025-03-01.

Demo users per bank: `analyst@<bank>.example` (ANALYST), `officer@<bank>.example` (CREDIT_OFFICER), `admin@<bank>.example` (ADMIN), where `<bank>` is the lower case bank code.

Loan (one car loan):

| Attribute | Rule |
|---|---|
| VIN | 17 characters. WMI from a fixed list of at least 20 real world manufacturer prefixes (for example `1HG`, `1FT`, `2T1`, `3VW`, `5YJ`, `JTD`, `KMH`, `WBA`); positions 4 to 8 from the VIN alphabet; position 9 the ISO 3779 check digit (5.5); position 10 the model year code of `vehicle_year`; position 11 a plant character; positions 12 to 17 a zero padded serial. Unique in the data set. |
| borrower_ref | Summit: `C` plus 7 digits. Lakeside: `LK-` plus 6 digits. Unique per lender except in the two-car cases. |
| lender_loan_number | Summit: `SAF-<7 digits>-<2 digits>`. Lakeside: `LMC<8 digits>`. |
| origination_date | Uniform between 2024-01-02 and 35 days before the as-of date of the loan's first tape, unless a case rule says otherwise. |
| original_amount_cents | Dollars from a triangular distribution (low 8,000, mode 21,000, high 55,000), plus uniform random cents 0 to 99. |
| term_months, apr | Term from {36, 48, 60, 72, 84}. APR Summit uniform 11.9 to 24.9 percent, Lakeside 4.9 to 10.9 percent, one decimal. |
| current_balance_cents | Standard amortisation after the whole months elapsed at the tape's as-of date, floor 1 cent, never above the original amount. |
| vehicle_year, make, model, title_state | Year 2015 to 2026 consistent with the VIN year code; make consistent with the WMI; model from a list per make; state from {NJ, NY, PA, CT, FL, TX, GA, OH} with fixed weights. |

Outside records for an honest loan:

- Title: one record. `title_status` CLEAN. `lienholder_name` is one of four spellings of the lender's legal name chosen at random (exact legal name; upper case with `, L.L.C.` or `, INC.`; with a comma before the suffix; with a trailing space). `lien_recorded_on` is origination plus 5 to 28 days.
- Contract: one record. `lender_name` is the legal name or the upper case spelling. `amount_cents` equals the original amount. `signed_on` is origination minus 0, 1 or 2 days. `document_sha256` is the SHA-256 of `contract:<contract_id>`.
- Payments: one per month. First due date is origination plus 30 days. Each payment is the level amortisation payment, paid on the due date plus a jitter of -3 to +5 days, up to `timeline_end`. 4 percent of honest loans skip exactly one month (never creating a 90 day gap). `deposit_bank_code` is the bank of the loan's first tape.

### 5.5 VIN check digit (ISO 3779, as used by 49 CFR 565)

Alphabet: digits and upper case letters except I, O, Q. Transliteration: A=1 B=2 C=3 D=4 E=5 F=6 G=7 H=8, J=1 K=2 L=3 M=4 N=5, P=7, R=9, S=2 T=3 U=4 V=5 W=6 X=7 Y=8 Z=9; digits are their value. Weights for positions 1 to 17: 8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2. Check value is the weighted sum modulo 11; the check digit (position 9) is that value, or `X` for 10. Test vector: `1HGCM82633A004352` is valid.

datagen and the bank agent each implement this independently (no shared code), so each is a check on the other.

### 5.6 Timeline and sizes

`timeline_end` is 2026-11-16. Steps, in processing order:

| Step | Scene | Bank | Facility | Kind | As-of date | Honest baseline loans (full / small) |
|---|---|---|---|---|---|---|
| S01 | 1 | HARBOR | HARBOR-SUMMIT | BACK_BOOK | 2026-09-30 | 1050 / 88 |
| S02 | 1 | HARBOR | HARBOR-LAKESIDE | BACK_BOOK | 2026-09-30 | 780 / 65 |
| S03 | 1 | NORTHGATE | NORTHGATE-SUMMIT | BACK_BOOK | 2026-09-30 | 780 / 65 |
| S04 | 1 | NORTHGATE | NORTHGATE-LAKESIDE | BACK_BOOK | 2026-09-30 | 780 / 65 |
| S05 | 1 | CALDER | CALDER-SUMMIT | BACK_BOOK | 2026-09-30 | 560 / 47 |
| S06 | 2 | HARBOR | HARBOR-LAKESIDE | PLEDGE | 2026-10-15 | 200 / 17 |
| S07 | 2 | HARBOR | HARBOR-SUMMIT | PLEDGE | 2026-10-15 | 150 / 13 |
| S08 | 3 | NORTHGATE | NORTHGATE-SUMMIT | PLEDGE | 2026-10-22 | 110 / 10 |
| S09 | 4 | CALDER | CALDER-SUMMIT | PLEDGE | 2026-10-29 | 90 / 8 |
| S10 | 5 | HARBOR | HARBOR-SUMMIT | PLEDGE | 2026-11-05 | 90 / 8 |
| S11 | 7 | HARBOR | HARBOR-SUMMIT | RELEASE | 2026-11-12 | 0 / 0 |
| S12 | 7 | NORTHGATE | NORTHGATE-SUMMIT | PLEDGE | 2026-11-13 | 40 / 4 |
| S13 | 7 | HARBOR | HARBOR-SUMMIT | PLEDGE | 2026-11-13 | 30 / 3 |
| S14 | 8 | HARBOR | HARBOR-SUMMIT | PLEDGE | 2026-11-16 | 20 / 2 |
| S15 | 8 | CALDER | CALDER-SUMMIT | PLEDGE | 2026-11-16 | 20 / 2 |

Totals: 4,700 honest baseline loans (full), 397 (small). With the planted loans below the full profile holds about 5,000 distinct loans. S14 and S15 share `concurrent_group: "RACE"`; every other step has `null`. RELEASE steps have `uploader_role: "CREDIT_OFFICER"`, all others `"ANALYST"`. S06 contains honest rows only and no unfixable messiness.

The `example` profile is one bank (HARBOR), one facility (HARBOR-SUMMIT), one PLEDGE tape of exactly 20 data rows with as-of date 2026-10-15, containing: 12 clean honest rows, 3 honest rows with fixable messiness (lower case VIN, VIN with separators, `DD-Mon-YYYY` date), 2 rows with unfixable messiness (VIN check digit invalid, amount unparseable), 1 invented car with no title, 1 loan whose title names another lienholder, 1 loan with no payment in 90 days. It has no cross bank cases.

### 5.7 Planted cases

Every case below is recorded in the answer key. "Original" means an honest baseline loan of the named step that is used by exactly one case and by no other (originals of copies, of MOVED_AFTER_RELEASE and of REFINANCED are baseline loans). Loans created for a case (invented cars, altered-VIN copies, both cars of a two-car pair, race pairs, title pending loans, other-lienholder and no-payment loans, refinanced new loans, the S07 originals of the title pending copies) are additional to the baseline counts of 5.6. The expected outcome is what the system honestly produces under sections 9 to 14; nothing here is aspirational.

Fraud cases (lender SUMMIT):

| Case type | Count full / small | Construction | Where it is caught | Expected outcome |
|---|---|---|---|---|
| `EXACT_COPY` | 40 / 6 | Row copied unchanged from an original at HARBOR (S01). 15 / 2 copies go into Northgate's back book (S03), 25 / 4 into S08. | Registry, Rule B | Copy: CONFLICT, EXACT, basis ASSET, all eight kinds matched. Original: stays LOCKED, HOLDER alert EXACT. |
| `COPY_AMOUNT_CHANGED` | 25 / 4 | Copy of an original at HARBOR (S01) placed in S09 with the original amount moved up or down by 500.00 to 3,000.00 dollars (the 100 dollar bucket always changes) and the balance scaled. The lender also deposits a second contract record with the changed amount. | Registry, Rule B | Copy: CONFLICT, LIKELY, basis ASSET; matched includes ASSET, VIN, BORROWER, DATE_DAY, DATE_MONTH; AMOUNT_EXACT and AMOUNT_100 not matched. Original: LOCKED, HOLDER alert LIKELY. |
| `COPY_AMOUNT_CHANGED_NO_CONTRACT` | 10 / 2 | As above but no second contract record exists. Placed in S09. | Bank, local check | Copy: HELD_LOCAL; failed checks exactly {CONTRACT_PRESENT} with reason CONTRACT_TERMS_MISMATCH. Nothing sent. Original: LOCKED, no alert. |
| `COPY_DATE_CHANGED` | 25 / 4 | Copy of an original at NORTHGATE (S03) placed in S09 with the origination date moved 7 to 60 days (never after the as-of date). Second contract record with the moved date. | Registry, Rule B | Copy: CONFLICT, LIKELY, basis ASSET; DATE_DAY not matched. Original: LOCKED, HOLDER alert LIKELY. |
| `COPY_VIN_ALTERED_CHECK_DIGIT_INVALID` | 15 / 3 | Copy of an original at HARBOR (S01) placed in S09 with one VIN character (not position 9) replaced by a character of a different transliteration value. | Bank, cleaning | Copy row: REJECTED, problem VIN_CHECK_DIGIT_INVALID. Nothing sent. Original: LOCKED, no alert. |
| `COPY_VIN_ALTERED_NO_TITLE` | 15 / 3 | Copy of an original at HARBOR (S01, originated more than 60 days before 2026-10-29) placed in S09 with one VIN character (not position 9) replaced by a different character of the same transliteration value, so the check digit is still valid. Contract record fabricated for the altered VIN. No title, no payments exist for the altered VIN. | Bank, local check | Copy: HELD_LOCAL; failed checks exactly {TITLE_EXISTS, PAYMENTS_RECEIVED}. Nothing sent. Original: LOCKED, no alert. |
| `COPY_VIN_ALTERED_TITLE_PENDING` | 10 / 2 | Original is an honest loan added to S07 for this case, originated 2026-10-02 to 2026-10-12, with a title record and a contract. Copy placed in S09 with the same-value VIN alteration and a fabricated contract. The copy is inside the 30 day title pending window and before its first payment is due, so local checks cannot refute it. | Registry, Rule C | Copy: CONFLICT, LIKELY, basis FIELDS; matched includes BORROWER, AMOUNT_EXACT, DATE_DAY; ASSET and VIN not matched. Original: LOCKED, HOLDER alert LIKELY. |
| `INVENTED_NO_TITLE` | 30 / 4 | A car that does not exist: fresh valid VIN, origination 120 to 400 days before the as-of date, fabricated contract, no title, no payments. Placed in S10. | Bank, local check | HELD_LOCAL; failed checks exactly {TITLE_EXISTS, PAYMENTS_RECEIVED}. Nothing sent. |
| `TITLE_OTHER_LIENHOLDER` | 20 / 3 | Real car with payments and a Summit contract, but the title names `Crestline Auto Credit Corp` as lienholder. Placed in S10. | Bank, local check | HELD_LOCAL; failed checks exactly {LIENHOLDER_MATCH}. Nothing sent. |
| `NO_PAYMENTS_90D` | 25 / 4 | Title and contract in order, origination at least 240 days before the as-of date, last payment 100 to 200 days before the as-of date. Placed in S10. | Bank, local check | HELD_LOCAL; failed checks exactly {PAYMENTS_RECEIVED}. Nothing sent. |
| `RACE_PAIR` | 10 / 2 | A new honest-looking Summit loan (title, payments, contract in order) placed unchanged in both S14 and S15, which are uploaded at the same instant. | Registry, lock uniqueness | Exactly one of the two rows LOCKED and the other CONFLICT, EXACT, basis ASSET. The locked one gets a HOLDER alert EXACT. Either bank may win. |

Known blind spot (planted so the scorecard reports it honestly, never counted as a catch or a miss):

| Case type | Count | Construction | Expected outcome |
|---|---|---|---|
| `BLIND_SPOT_INVENTED_IN_PENDING_WINDOW` | 5 / 1 | Invented car, origination 5 to 20 days before the as-of date, fabricated contract, no title, no payments. Placed in S10. | LOCKED, no alert. Layer 1 cannot tell this from an honest new loan until the title window closes (see 23). |

Honest look-alikes (must produce no alert and no hold):

| Case type | Count full / small | Construction | Expected outcome |
|---|---|---|---|
| `TWO_CARS_ONE_BORROWER` | 40 / 5 pairs | One Summit borrower_ref on two different cars. Car A in S01, car B in S03 or S05. Half the pairs (rounded up) are "near": same origination month and same 1,000 dollar band, but a different day and a different 100 dollar band. The rest differ in month and band. | Both LOCKED, no alert. |
| `MOVED_AFTER_RELEASE` | 30 / 4 | A loan locked at HARBOR in S01, released in S11, pledged unchanged at NORTHGATE in S12. | S11 row RELEASED. S12 row LOCKED, no alert. |
| `REFINANCED` | 30 / 4 | A loan locked at HARBOR in S01 and released in S11. A new loan on the same car and borrower (new loan number, origination 2026-11-01 to 2026-11-10, new amount, second contract record) is pledged at NORTHGATE in S12 (half, rounded up) or back at HARBOR in S13 (the rest). | S11 row RELEASED. New row LOCKED, no alert. At HARBOR a new loan row is created next to the RELEASED one. |
| `TITLE_PENDING_HONEST` | 60 / 6 | Real new loans originated 3 to 15 days before the as-of date of S10, S14 or S15, with a contract but no title record yet and no payment due. | LOCKED, no alert. |

Data quality rows ("messiness"), all recorded in the answer key:

| Kind | Expected | Construction |
|---|---|---|
| `M_VIN_LOWERCASE` | FIXED `VIN_CASE_NORMALISED` | VIN in lower case. |
| `M_VIN_SEPARATORS` | FIXED `VIN_SEPARATORS_REMOVED` | Spaces or hyphens inside the VIN. |
| `M_VIN_OIQ` | FIXED `VIN_CHARACTER_SUBSTITUTED` | One `0` written as `O`, or one `1` written as `I`. |
| `M_WHITESPACE` | FIXED `WHITESPACE_TRIMMED` | Leading and trailing spaces on several cells. |
| `M_DATE_ALT_FORMAT` | FIXED `DATE_FORMAT_NORMALISED` | `14-Aug-2026`. |
| `M_AMOUNT_ALT_FORMAT` | FIXED `AMOUNT_FORMAT_NORMALISED` | `23450` or `$23,450` or `23450.5`. |
| `M_BORROWER_LOWERCASE` | FIXED `BORROWER_REF_NORMALISED` | Lower case borrower reference. |
| `M_VIN_MISSING` | REJECTED `MISSING_REQUIRED_FIELD` | Empty VIN cell. |
| `M_VIN_SHORT` | REJECTED `VIN_LENGTH_INVALID` | 16 characters. |
| `M_VIN_TYPO` | REJECTED `VIN_CHECK_DIGIT_INVALID` | Two adjacent characters of different value swapped. |
| `M_DATE_GARBAGE` | REJECTED `DATE_UNPARSEABLE` | `13/45/2026`. |
| `M_DATE_FUTURE` | REJECTED `DATE_IN_FUTURE` | Origination after the as-of date. |
| `M_AMOUNT_GARBAGE` | REJECTED `AMOUNT_UNPARSEABLE` | `N/A`. |
| `M_AMOUNT_NEGATIVE` | REJECTED `AMOUNT_OUT_OF_RANGE` | `-23450.00`. |
| `M_BALANCE_GT_ORIGINAL` | REJECTED `BALANCE_EXCEEDS_ORIGINAL` | Balance above the original amount. |
| `M_FORMULA_CELL` | REJECTED `UNSAFE_CHARACTERS` | Loan number `=HYPERLINK("http://x","y")` or borrower `@SUM(1+1)`. |
| `M_DUPLICATE_ROW` | first row accepted, second REJECTED `DUPLICATE_VIN_IN_FILE` | The same row twice in one file. |
| `M_TOTALS_ROW` | REJECTED `NON_DATA_ROW` | Last row of a Summit tape: `TOTAL` in the first cell, a sum in the balance cell, other cells empty. |

Fixable kinds are applied to about 3 percent of honest rows (full: at least 10 rows per kind; small: at least 1 per kind) and never to a row that belongs to a fraud case. A row gets at most one messiness kind. Unfixable kinds are extra rows built from loans that appear nowhere else (full: at least 3 rows per kind, 48 rows in total not counting totals rows; small: at least 1 per kind). Every Summit tape of kind BACK_BOOK or PLEDGE ends with one totals row, except S06 is Lakeside and has none.

### 5.8 Invariants the generator enforces before writing (and `verify` re-checks)

1. Every VIN not planted as invalid passes the check digit. Every VIN planted as invalid fails it. No VIN contains I, O or Q except `M_VIN_OIQ` rows.
2. A VIN appears in more than one tape row only through a planted case or `M_DUPLICATE_ROW`.
3. No original is used by two cases. No fraud row carries messiness.
4. Every copy's original is in an earlier step at a different bank (RACE_PAIR excepted), and is not released before the copy's step.
5. Each case satisfies the preconditions of its expected outcome computed from the rules of sections 9 to 11 by an independent implementation inside datagen (title window ages, payment windows, bucket changes, contract matches, same-value VIN substitutions, Rule C non-triggers for every honest pair).
6. Honest baseline loans pass all four local checks at their step's as-of date.
7. Row counts in `manifest.json` equal the files.

### 5.9 Tape files as lenders send them

Two lender formats. Both are CSV with a header row; cells containing commas or quotes are quoted with `"`.

| | Summit Auto Finance | Lakeside Motor Credit |
|---|---|---|
| Encoding, line ends | UTF-8 with BOM, CRLF | UTF-8 without BOM, LF |
| Header | `Loan #,VIN,Cust ID,Orig Date,Orig Amt,Curr Bal,APR,Term,Year,Make,Model,State` | `loan_id,vehicle_vin,borrower_id,origination_date,original_amount,current_balance,apr_pct,term_months,vehicle_year,vehicle_make,vehicle_model,title_state` |
| Dates | `MM/DD/YYYY` | `YYYY-MM-DD` |
| Amounts | `$23,450.17` (quoted) | `23450.17` |
| Extras | One trailing totals row; one trailing blank line | None |

RELEASE tapes use the lender's same header; only the VIN and loan number cells are filled.

The `example` profile output is committed under `contracts/examples/tapes/` as `example_tape_20.csv`, `example_outside_records/{titles,payments,contracts}.csv` and `example_tape_20.expected.json` (per row: expected disposition, cleaning issues, and for accepted rows the four check results with reason codes and the expected local state PENDING or HELD_LOCAL). A datagen test regenerates the profile with seed 20261117 and asserts the committed files are byte identical. A bank agent contract test runs intake, cleaning and local checks on the example and asserts the expected file.

### 5.10 Answer key

`answer_key/answer_key.json`:

```json
{"generator_version": "1.0.0", "seed": 20261117, "profile": "full",
 "cases": [
   {"case_id": "EXACT_COPY-0001", "case_type": "EXACT_COPY", "category": "FRAUD",
    "rows": [
      {"role": "ORIGINAL", "bank_code": "HARBOR", "step_id": "S01", "file": "tapes/HARBOR/S01_HARBOR-SUMMIT_BACK_BOOK.csv", "row_number": 412,
       "expected": {"disposition": "ACCEPTED", "cleaning_problems": [], "outcome": "LOCKED", "failed_checks": [],
                    "match_kind": null, "match_basis": null, "kinds_matched_include": [], "kinds_matched_exclude": [],
                    "alert": {"role": "HOLDER", "match_kind": "EXACT"}}},
      {"role": "COPY", "bank_code": "NORTHGATE", "step_id": "S08", "file": "...", "row_number": 37,
       "expected": {"disposition": "ACCEPTED", "cleaning_problems": [], "outcome": "CONFLICT", "failed_checks": [],
                    "match_kind": "EXACT", "match_basis": "ASSET",
                    "kinds_matched_include": ["ASSET","VIN","BORROWER","AMOUNT_EXACT","AMOUNT_100","AMOUNT_1000","DATE_DAY","DATE_MONTH"],
                    "kinds_matched_exclude": [], "alert": {"role": "CLAIMANT", "match_kind": "EXACT"}}}],
    "race_group": null}],
 "messiness": [{"kind": "M_VIN_LOWERCASE", "bank_code": "HARBOR", "step_id": "S01", "file": "...", "row_number": 9,
                "expected_action": "FIXED", "expected_problem": "VIN_CASE_NORMALISED"}],
 "baseline": {"expected": {"disposition": "ACCEPTED", "outcome": "LOCKED", "alert": null}}}
```

- `category` is one of FRAUD, BLIND_SPOT, HONEST_LOOKALIKE.
- `expected.outcome` is one of `REJECTED` (with `disposition: "REJECTED"`), `HELD_LOCAL`, `LOCKED`, `CONFLICT`, `RELEASED`.
- `row_number` counts data rows from 1 after the header, skipping lines that are completely blank, exactly as the bank agent numbers them (10.2).
- For a RACE_PAIR case `race_group` is set and both rows carry `expected.outcome: "LOCKED_OR_CONFLICT"`; the scorer applies the exactly-one rule of 5.7.
- Every row not named in `cases` or `messiness` is baseline: accepted, LOCKED, no alert, no hold. Rows named only in `messiness` with `expected_action: "FIXED"` are also expected LOCKED with no alert.

---

## 6. outside_records

A small read-only service that stands in for title offices, payment deposits and a contract vault. Contract: `contracts/outside_records.yaml`.

### 6.1 Database (`{prefix}_outside`)

| Table | Columns |
|---|---|
| `title_records` | `vin char(17) PK check (vin ~ '^[A-HJ-NPR-Z0-9]{17}$')`, `state char(2) not null`, `title_status text not null check in (CLEAN, SALVAGE, JUNK)`, `lienholder_name text null`, `lien_recorded_on date null` |
| `payments` | `payment_id uuid PK`, `vin char(17) not null`, `paid_on date not null`, `amount_cents bigint not null check > 0`, `deposit_bank_code text not null`; index `(vin, paid_on desc, payment_id)` |
| `contracts` | `contract_id uuid PK`, `vin char(17) not null`, `lender_name text not null`, `amount_cents bigint not null check > 0`, `signed_on date not null`, `document_sha256 char(64) not null`; index `(vin, signed_on, contract_id)` |
| `api_clients` | `client_id uuid PK`, `name text unique not null`, `api_key_sha256 char(64) unique not null`, `active boolean not null default true`, `created_at timestamptz not null default now()` |

Additions to the plan: `api_clients` (authentication), the indexes, and `contracts` allows several rows per VIN (a refinance has two contracts).

Roles: `{db}_owner` owns the schema and runs migrations and seeding. `{db}_app` has `CONNECT`, `USAGE` on the schema and `SELECT` on the four tables, nothing else. The API process connects only as `{db}_app`, so it cannot write even if the code tried.

### 6.2 Behaviour

- `GET /v1/titles/{vin}`, `GET /v1/payments`, `GET /v1/contracts/{vin}` exactly as the contract says, including ordering, paging and 404 codes.
- Authentication: `X-API-Key`, looked up by SHA-256 in `api_clients` where `active`. Missing or unknown key: 401 `UNAUTHENTICATED`.
- A path or query VIN that does not match the pattern: 422 `VALIDATION_ERROR` with `details: [{"field": "vin", "issue": "PATTERN_MISMATCH"}]` and no echo of the value.
- The service does not validate the check digit (a title office answers for whatever string it is asked).
- Logging: JSON lines; the access log records method, route template (for example `/v1/titles/{vin}`), status, duration, request id, client name. It MUST NOT record the path, the query string or the VIN. uvicorn's own access log is disabled.
- `GET /healthz` needs no key.

### 6.3 Commands

```
onepledge-outside db provision          create roles and database (needs PG_ADMIN_URL), idempotent
onepledge-outside db migrate            alembic upgrade head as owner
onepledge-outside seed --data-dir DIR   replace the three data tables from DIR/outside_records/*.csv in one transaction (owner role)
onepledge-outside clients add --name NAME --key-env VAR    upsert a client whose key is read from environment variable VAR
onepledge-outside serve
```

`seed` prints the row count per table and is idempotent (same input, same table contents).

---

## 7. Contract test doubles (`contracts/fakes/`)

Package `onepledge_contract_fakes`. Every module docstring, the package README and both `/healthz` responses state that these are TEST DOUBLES for layer 2 and not production code.

Structure: plain classes `KeyHolderDouble` and `RegistryDouble` hold all behaviour and state in memory behind one `threading.RLock` each, usable in-process with no HTTP. `create_key_holder_app(config) -> FastAPI` and `create_registry_app(config) -> FastAPI` wrap them. Console scripts `onepledge-fake-keyholder serve` and `onepledge-fake-registry serve` run them with uvicorn (one worker, access log off). State is lost on restart; this is stated in the README.

### 7.1 Key holder double

Implements `contracts/key_holder.yaml` in full:

- Authenticates `X-API-Key` against the configured member list (SHA-256 compare).
- For each element: decode standard base64 to exactly 32 bytes; reject the identity (32 zero bytes); compute `oprf.mask(scalar).mask(oprf.data(element_bytes))`; a `RuntimeError` from the library (invalid point) or an all zero result is `INVALID_ELEMENT`. Any invalid element rejects the whole request with 422 and the list of failing indexes.
- Starts only if every configured scalar decodes to 32 bytes and is not zero.
- `key_version` pinned to a version that is not configured: 409 `KEY_VERSION_UNAVAILABLE`.
- Per member daily element cap (UTC day): exceeding it returns 429 with `Retry-After`.
- Records `(member_id, request_id, element count, time)` per accepted request. Never stores or logs elements, except in the test-only capture below.

### 7.2 Registry double

Implements every path of `contracts/registry.yaml` with the decision rules, idempotency, privacy and signature rules written in that file's description. Additional exact behaviour:

- At start it appends one `MEMBER_JOINED` event per configured member, in list order, then signs checkpoint 1.
- A submission is decided atomically under the lock, items in request order. Events appended: `LOCK_ACQUIRED` per new lock; `CONFLICT_DETECTED` and `CASE_OPENED` per new case; `LOCK_RELEASED` per release; `CASE_RESOLVED` per case closed by that release; then one `CERTIFICATE_ISSUED`. `as_of_seq` is the seq of that `CERTIFICATE_ISSUED` event. Then a checkpoint is signed over the head and a `CHECKPOINT_SIGNED` event is appended.
- A repeated conflict of the same claimant asset token against the same lock while the case is OPEN returns the same `case_id`, appends no case events and queues no new alert.
- Outbox fan-out: a new case queues one `ALERT` for the holder (with the holder's asset token and role HOLDER) and one for the claimant (with the claimant's asset token and role CLAIMANT). A case resolution queues one `CASE_UPDATE` for each party. Every checkpoint queues one `CHECKPOINT` for every member. Positions are per member, strictly increasing.
- Body comparison for idempotency uses the SHA-256 of the canonical JSON of the parsed body.
- `key_version` different from `FAKE_REGISTRY_KEY_VERSION`: 409 `KEY_VERSION_MISMATCH`.
- Per member daily item cap: 429 with `Retry-After`.
- `GET /v1/ledger/events?view=full` requires the auditor key, else 403.
- A member asking for another member's submission, certificate, case or outbox message gets 404 (never 403), so existence is not revealed.

### 7.3 Test controls

Both doubles expose `/_test/` endpoints only when `FAKE_TEST_CONTROLS=true`, and only to callers presenting `X-Test-Control-Key`. They are not part of any contract.

| Endpoint | Double | Behaviour |
|---|---|---|
| `GET /_test/stats` | both | Key holder: total requests and elements, and per member. Registry: submissions, items, active locks, open cases, events, checkpoints, in total and per member. |
| `GET /_test/received` | both | Every request body received on contract endpoints, as raw text, in order (kept only when test controls are on). Used by the privacy scan. |
| `POST /_test/fail-next` | both | Body `{"count": n, "status": 503, "when": "BEFORE_PROCESSING" or "AFTER_COMMIT"}`. The next n contract calls fail with that status. `AFTER_COMMIT` (registry `POST /v1/submissions` only) processes and stores the submission, then returns the failure, which simulates a lost response. |
| `GET /_test/dump` | registry | Full state: members, locks, field tokens, cases, events with payloads, certificates, checkpoints. Codes and registry identifiers only. |
| `POST /_test/tamper` | registry | Body `{"mode": ...}`. `REWRITE_HISTORY` with `seq`: changes that event's payload, recomputes every hash from there to the head, signs and distributes a new checkpoint over the new head. `EDIT_IN_PLACE` with `seq`: changes that event's stored `payload_hash` and nothing else. `BAD_CHECKPOINT_SIGNATURE`: signs and distributes a checkpoint whose signature has one bit flipped. `CERTIFICATE_BODY` with `certificate_id`: changes `summary.locked` in the stored certificate without re-signing. |
| `POST /_test/checkpoint` | registry | Signs and distributes a checkpoint now. |

Response shapes of `GET /_test/stats`. Key holder: `{"total": {"requests": n, "elements": n}, "by_member": {"<member_id>": {"requests": n, "elements": n}}}`. Registry: `{"total": {"submissions": n, "items": n, "active_locks": n, "open_cases": n, "events": n, "checkpoints": n}, "by_member": {"<member_id>": {"submissions": n, "items": n, "active_locks": n, "open_cases": n}}}`. `submissions` counts distinct stored submissions (an idempotent replay does not add one). `GET /_test/received` returns `{"requests": [{"path": "...", "member_id": "...", "body": "<raw text>"}]}`.

### 7.4 Contract checker

`onepledge-contracts check [--contracts-dir contracts]` (module `onepledge_contract_fakes.contracts_check`):

1. Parses each of the four YAML files and validates it with `openapi-spec-validator` as OpenAPI 3.1.
2. Validates every entry of `examples/index.json` against its component schema (JSON Schema 2020-12 with format checking).
3. Checks that every `externalValue` in the YAML resolves to a file and every file under `examples/{bank_agent,key_holder,registry,outside_records}/` is in the index.
4. Recomputes `examples/vectors/tokenization.json` and `examples/vectors/signatures.json` from their stated inputs and compares.

It prints one line per check and a final `contracts-check: OK (N files, M examples)` or the list of failures, and exits 0 or 1.

---

## 8. Bank agent: domain model

Package `onepledge_bank_agent`. `domain/` holds plain, typed, immutable-where-possible classes with no I/O. Names below are binding.

### 8.1 Value objects (frozen dataclasses, validated in `__post_init__` or a `parse` class method)

| Class | Content and invariants |
|---|---|
| `Vin` (all value objects are importable from `onepledge_bank_agent.domain`) | `value: str`, exactly 17 characters of the VIN alphabet with a valid ISO 3779 check digit (5.5). `Vin.parse(text)` raises `InvalidVin(reason)` with reason `LENGTH`, `CHARACTER` or `CHECK_DIGIT`. `__repr__` and `__str__` return `Vin(***)`; the raw string is only available through `.value`. |
| `Money` | `cents: int`, `0 <= cents <= 10**13`. `Money.parse_decimal(text)` uses `Decimal`, accepts at most 2 fraction digits. Supports `+`, `-`, comparison. No float anywhere. `__repr__` masks the amount. |
| `BorrowerRef` | `value: str` matching `^[A-Z0-9]{4,32}$`. Masked repr. |
| `LenderLoanNumber` | `value: str` matching `^[A-Za-z0-9][A-Za-z0-9\-_/]{0,39}$`. Masked repr. |
| `Lei` | 20 characters `^[A-Z0-9]{18}[0-9]{2}$` with valid ISO 17442 check digits (mod 97 equals 1). |
| `Sha256Hex` | 64 lower case hex characters. |
| `Token` | `kind: TokenKind`, `value: Sha256Hex`, `key_version: KeyVersion`. |
| `KeyVersion` | `holder_1: int >= 1`, `holder_2: int >= 1`; string form `"<h1>.<h2>"`. |
| `LoanTerms` | `vin`, `borrower_ref`, `origination_date`, `original_amount`. Immutable; the token inputs. |
| `CheckResult` | `check_type: CheckType`, `outcome: CheckOutcome`, `reason: CheckReason`, `evidence: Mapping[str, JsonValue]`. |
| `CleaningIssue` | `field: str`, `problem: CleaningProblem`, `action: CleaningAction`. |

Enums (`StrEnum`): `TapeKind` (BACK_BOOK, PLEDGE, RELEASE), `TapeSource` (FILE_UPLOAD, API_INSTRUCTION), `TapeStatus`, `FailureCode`, `LoanState`, `RowDisposition`, `CheckType`, `CheckOutcome` (PASS, FAIL, UNKNOWN), `CheckReason` (11.2), `CleaningProblem` (10.4), `CleaningAction` (FIXED, REJECTED), `TokenKind`, `MatchKind`, `MatchBasis`, `AlertRole` (HOLDER, CLAIMANT), `AlertStatus`, `CaseStatus`, `BankPosition` (DATA_ERROR, CONFIRMED_DOUBLE_PLEDGE), `RegistryItemResult`, `Role`, `AuditAction` (17.2), `AuditOutcome`.

### 8.2 Entities

`TapeSubmission`, `Loan`, `Alert`, `User`, `Facility`, `Lender`, `RegistryRequest`, `Certificate`, `Checkpoint`. Each entity that has a state machine exposes one method per transition; a method raises `IllegalTransition` (a `DomainError`) when the transition is not in the tables below. No code outside the entity assigns a status field.

### 8.3 State machine: `TapeSubmission.status`

Stages run in this order: RECEIVED, CLEANED, CHECKED, TOKENIZED, SUBMITTED, COMPLETED. A status names the last stage that completed.

| From | To | When |
|---|---|---|
| (none) | RECEIVED | Intake committed (10.1). |
| RECEIVED | CLEANED | Clean stage committed. |
| CLEANED | CHECKED | Check stage committed (for RELEASE the stage is a no-op and still advances). |
| CHECKED | TOKENIZED | Tokenise stage committed. |
| TOKENIZED | SUBMITTED | Every planned registry request has a stored response. |
| SUBMITTED | COMPLETED | Every certificate fetched, verified and stored. |
| RECEIVED, CLEANED, CHECKED, TOKENIZED, SUBMITTED | FAILED | Permanent error, or `PIPELINE_MAX_ATTEMPTS` transient failures at one stage. `resume_status` keeps the status it had; `failed_stage` is the stage that was being attempted; `failure_code` says why. |
| FAILED | its `resume_status` | `POST /v1/tapes/{id}/retry` when the failure code is retryable (13.8). |

No other transition exists. COMPLETED is terminal.

### 8.4 State machine: `Loan.state`

| From | To | When |
|---|---|---|
| (none) | PENDING | Clean stage creates the loan from an accepted row. |
| PENDING | HELD_LOCAL | A local check FAILs or is a blocking UNKNOWN (11.3). |
| PENDING | LOCKED | Registry result LOCKED. |
| PENDING | CONFLICT | Registry result CONFLICT. |
| HELD_LOCAL | PENDING | The lender presents the same car again on a later PLEDGE or BACK_BOOK tape (10.5). |
| CONFLICT | LOCKED | Resubmission after every case on the loan was cleared, registry result LOCKED (14.5). |
| LOCKED | RELEASED | Registry result RELEASED for a release item. |

RELEASED is terminal for that row. The same car pledged again later becomes a new loan row. A HOLDER alert never changes the state: the holder keeps its lock and stays LOCKED. A CONFLICT loan whose resubmission conflicts again stays CONFLICT (no transition). The same table is enforced in the database by trigger (12.4).

### 8.5 State machine: `Alert.status`

| From | To | When | Who |
|---|---|---|---|
| (none) | NEW | Alert created from a registry ALERT message or a CONFLICT result (14.2). | system |
| NEW | REVIEWING | Action START_REVIEW. | ANALYST, CREDIT_OFFICER |
| REVIEWING | REVIEWING | Action SUBMIT_POSITION (records `bank_position` once; a different second position is refused). | CREDIT_OFFICER |
| NEW, REVIEWING | CLEARED | Registry CASE_UPDATE with CLEARED_DATA_ERROR or CLEARED_LATE_RELEASE. | system |
| NEW, REVIEWING | ESCALATED | Registry CASE_UPDATE with CONFIRMED. | system |

CLEARED and ESCALATED are terminal. Only the registry's case outcome closes an alert; a bank user cannot.

### 8.6 Application layer (one class per use case, constructor injection, interfaces defined in `application/ports.py`)

Pipeline: `IngestTape`, `CleanTape`, `RunLocalChecks`, `TokenizeLoans`, `SubmitPledge` (plans and sends BACK_BOOK and PLEDGE requests), `ReleaseLoans` (plans and sends RELEASE requests), `CollectCertificates`, `AdvanceSubmission` (runs the next stage of one submission), `RunPipelineWorker`.
Registry side: `SyncRegistryOutbox`, `VerifyCheckpoint`, `VerifyCertificate`, `ResubmitLoan`, `ReviewAlert`, `RunDeepVerification`, `GetIntegrityReport`.
Read side: `ListFacilities`, `ListTapes`, `GetTape`, `ListTapeItems`, `ListLoans`, `SearchLoans`, `GetLoan`, `GetLoanLineage`, `ListAlerts`, `GetAlert`, `ListCertificates`, `GetCertificate`, `ListAuditLog`.
Access: `Login`, `AuthenticateRequest`, `ListUsers`, `CreateUser`, `UpdateUser`, `CreateReleaseInstruction`, `RetryTape`.

Ports (abstract base classes or `Protocol`s): `UnitOfWork` (transaction scope exposing repositories), `TapeRepository`, `RawRowRepository`, `LoanRepository`, `CheckRepository`, `TokenRepository`, `RegistryRequestRepository`, `CertificateRepository`, `AlertRepository`, `CheckpointRepository`, `UserRepository`, `LoginAttemptRepository`, `AuditLog` (append and read), `TitleRecordsClient`, `PaymentsClient`, `ContractsClient`, `KeyHolderClient`, `RegistryClient`, `AlertExplainer`, `PasswordHasher`, `TokenIssuer`, `SignatureVerifier`, `Blinder` (wraps `oprf`), `Clock`, `IdGenerator`, `FaultInjector` (no-op in production; tests inject one that raises at named points, see 21.3).

Rules: a use case does one thing and is at most about 150 lines. No use case imports from `infrastructure` or `interface`. All authorisation decisions are made by one `AccessPolicy` class in `domain/` (18.2), called by the interface layer before the use case and again by use cases that branch on role (`IngestTape` for RELEASE, `ReviewAlert` for SUBMIT_POSITION).

---

## 9. Normalisation and tokenisation

### 9.1 Canonical field forms (the only forms ever tokenised or stored in `loans`)

| Field | Canonical form |
|---|---|
| VIN | 17 characters, upper case, VIN alphabet, valid check digit. |
| Borrower reference | Upper case, every character outside `A-Z0-9` removed, then must match `^[A-Z0-9]{4,32}$`. |
| Lender loan number | Trimmed; must match the `LenderLoanNumber` pattern. Not tokenised. |
| Origination date | `YYYY-MM-DD`. |
| Amounts | Integer cents as a decimal string with no sign, no leading zeros, no separators. |
| Lender namespace | The facility lender's LEI (20 characters). |

### 9.2 Token inputs

Let `V` be the canonical VIN, `B = LEI + "|" + borrower_ref`, `C` the original amount in cents, `D` the origination date.

| Kind | Canonical value string |
|---|---|
| `ASSET` | `"AUTO|" + V` |
| `VIN` | `V` |
| `BORROWER` | `B` |
| `AMOUNT_EXACT` | `B + "|" + str(C)` |
| `AMOUNT_100` | `B + "|" + str(C // 10000)` (100 dollar bucket, floor) |
| `AMOUNT_1000` | `B + "|" + str(C // 100000)` (1,000 dollar bucket, floor) |
| `DATE_DAY` | `B + "|" + D` |
| `DATE_MONTH` | `B + "|" + D[:7]` |

Design notes that are requirements:

- `ASSET` is the lock key: one car, one lock, whatever the amount or date says. `ASSET` and `VIN` are derived from the same VIN under different prefixes; `ASSET` carries an asset class namespace (`AUTO`) so other collateral types can be added later, `VIN` is the field code used in `kinds_matched`. In layer 1 they always match or differ together.
- The borrower is namespaced by lender LEI because a borrower reference is a lender's own customer number.
- Amount and date codes are bound to the borrower string. The registry can then compare amount and date only between loans of the same borrower, and cannot build a histogram of amounts or dates across the book.
- The current balance is never tokenised (it changes every month).

### 9.3 Domain separation and the OPRF

Verified against `oprf` 5.0.0 on 2026-11-21 (`oprf.data`, `oprf.mask`; group ristretto255; `data.hash` is SHA-512 then hash-to-group; `mask.random()`, `mask.mask(data)`, `mask.unmask(data)`, `to_base64()`, `from_base64()`; an invalid point raises `RuntimeError` when masked; a zero scalar yields 32 zero bytes).

For one (kind `K`, canonical value string `S`):

1. Input bytes: `b"onepledge:v1:" + K.encode("ascii") + b"\x1f" + S.encode("utf-8")`.
2. Point: `P = oprf.data.hash(input_bytes)`.
3. Blind: `r = oprf.mask.random()` (fresh per element, never reused, never persisted, never logged); `X = r.mask(P)`.
4. Holder 1: send `X.to_base64()` in `elements`; receive `Y1`. Holder 2: send `Y1` unchanged; receive `Y2`. For every returned element: it must be 44 base64 characters decoding to 32 bytes, not all zero, and the response must have the same length as the request; otherwise `KeyHolderProtocolError` (permanent, failure code KEY_HOLDER_REJECTED).
5. Unblind: `U = r.unmask(oprf.data.from_base64(Y2))`. Then `U = k2 * k1 * P` (tested: equals direct evaluation, in both holder orders).
6. Token: `sha256(b"onepledge:v1:token\x1f" + K.encode("ascii") + b"\x1f" + bytes(U)).hexdigest()` (64 lower case hex characters).

Test vector: `contracts/examples/vectors/tokenization.json` gives fixed scalars, a fixed blind, every intermediate value and the eight tokens for one loan. The `Blinder` adapter MUST reproduce it (with the blind injected), and a Hypothesis test MUST show that for random inputs and random scalars the two-holder result equals `k2.mask(k1.mask(P))` and is independent of the blind.

Batching: one round sends all elements of up to `TOKENIZE_CHUNK_LOANS` loans (8 per loan, at most 1,000 per request) to holder 1 in one request, then the returned list to holder 2 in one request. Order is preserved, so element `i` of the response belongs to element `i` of the request.

### 9.4 Key version

- Each holder returns the `key_version` it used. The token's `key_version` is `"<holder 1>.<holder 2>"`.
- Within one tokenise round both responses must each report a single version. Within one submission all rounds must report the same pair; if a later round reports a different pair, the stage raises `KeyVersionChanged`, the submission becomes FAILED with `KEY_VERSION_MISMATCH`, and no token of the new version is stored.
- `loan_tokens` rows are immutable and keyed by `(loan_id, token_kind, key_version)`.
- A registry answer of 409 `KEY_VERSION_MISMATCH` makes the submission FAILED with `KEY_VERSION_MISMATCH`. This failure is not retryable (see 23, key rotation).
- A RELEASE reuses the loan's stored ASSET token and its `key_version`.

### 9.5 Encoding summary

| Thing | On the wire | In storage |
|---|---|---|
| Blinded and evaluated elements | Standard base64 with padding, 44 characters | Never stored |
| Tokens | 64 lower case hex characters | `char(64)` in `loan_tokens.token` |
| `key_version` | String `"1.1"` | `text` |
| Blind scalars | Never leave the process | Never stored |

---

## 10. Tape intake and cleaning

### 10.1 Intake (`IngestTape`, the RECEIVED stage)

Input: facility id, kind, as-of date, file name, file bytes, acting user. Steps, all in one database transaction:

1. Authorise (18.2). Facility must exist (404 `FACILITY_NOT_FOUND`).
2. Whole file validation. Any failure rejects the upload, stores nothing except one audit entry `TAPE_UPLOAD_REJECTED` with the error code, and returns the error:

| Condition | Status | Code |
|---|---|---|
| More than 5 MiB (5,242,880 bytes), checked on `Content-Length` and again while reading | 413 | `TAPE_TOO_LARGE` |
| Not valid UTF-8 (a leading BOM is allowed and stripped) | 422 | `TAPE_NOT_UTF8` |
| Contains a NUL character, or the CSV parser (strict mode, delimiter `,`, quote `"`) raises | 422 | `TAPE_MALFORMED` |
| No header row, or zero data rows after the header | 422 | `TAPE_EMPTY` |
| Header has an empty name, a duplicate name, two names mapping to the same canonical field, or lacks a required field (10.3) | 422 | `TAPE_HEADER_INVALID` |
| More than 20,000 data rows, more than 64 columns, or any cell longer than 512 characters | 413 | `TAPE_TOO_LARGE` |
| `as_of_date` after today (UTC) or before 2000-01-01 | 422 | `VALIDATION_ERROR` |

3. `file_sha256` is the SHA-256 of the uploaded bytes exactly as received. If a submission with the same `(facility_id, kind, file_sha256)` exists: 409 `TAPE_DUPLICATE` with `details: [{"field": "submission_id", "issue": "<existing id>"}]`, audit `TAPE_UPLOAD_REJECTED`. Nothing else changes.
4. `file_name` is reduced to its base name, every character outside `[A-Za-z0-9._-]` becomes `_`, and it is cut to 255 characters. The declared content type of the part is ignored.
5. Insert the `tape_submissions` row (status RECEIVED) and one `loans_raw` row per data row.
6. Audit `TAPE_UPLOADED`. Commit. Return 202.

### 10.2 Raw rows

- A data row is a CSV record after the header. Records that are completely blank lines are skipped and not numbered. `row_number` starts at 1.
- `raw_payload` is a JSON object mapping each header cell text exactly as it appears in the file (BOM removed, nothing trimmed) to the cell text exactly as it appears. Extra cells beyond the header get keys `__extra_1`, `__extra_2`, and so on. Missing trailing cells are simply absent.
- `row_sha256 = sha256(canonical_json(raw_payload))`. `tape_submissions.rows_sha256 = sha256("\n".join(row_sha256 in row_number order))`.
- `loans_raw` is never updated or deleted (12.4). Cleaning reads it and writes only derived tables.

### 10.3 Header mapping

Header names are matched after trimming, lower casing and collapsing inner whitespace.

| Canonical field | Accepted header names | Required for BACK_BOOK, PLEDGE | Required for RELEASE |
|---|---|---|---|
| `lender_loan_number` | `loan #`, `loan_id`, `loan number`, `loan_number` | yes | no |
| `vin` | `vin`, `vehicle_vin` | yes | yes |
| `borrower_ref` | `cust id`, `borrower_id`, `customer id`, `borrower_ref` | yes | no |
| `origination_date` | `orig date`, `origination_date` | yes | no |
| `original_amount` | `orig amt`, `original_amount` | yes | no |
| `current_balance` | `curr bal`, `current_balance` | yes | no |

All other columns are kept in `raw_payload` and ignored by cleaning.

### 10.4 Cleaning rules (`CleanTape`, the CLEANED stage)

Cleaning is a pure function `clean_row(raw_payload, header_map, kind, as_of_date) -> CleanedRow | RejectedRow` in `domain/`, plus the file level and database level rules of 10.5. It is deterministic and idempotent: cleaning the canonical output again yields the same output and no issues.

Every cell is first trimmed of leading and trailing whitespace; if trimming changed a required cell, one issue `WHITESPACE_TRIMMED` (FIXED) is recorded for that field. Then, per field:

| Field | Accepted without an issue | FIXED (problem) | REJECTED (problem) |
|---|---|---|---|
| any required | | | empty: `MISSING_REQUIRED_FIELD` |
| row | | | all cells empty, or first cell is `TOTAL`, `TOTALS`, `GRAND TOTAL` or `SUBTOTAL` (case-insensitive): `NON_DATA_ROW`; cell count differs from the header: `COLUMN_COUNT_MISMATCH` |
| any required text | | | first character is one of `=`, `+`, `@`, tab, carriage return, or the cell contains a control character: `UNSAFE_CHARACTERS` (a leading `-` is handled by the amount rules) |
| `vin` | 17 upper case alphabet characters, valid check digit | lower case letters: `VIN_CASE_NORMALISED`; spaces or hyphens removed: `VIN_SEPARATORS_REMOVED`; `O` to `0`, `Q` to `0`, `I` to `1` when the result then passes the check digit: `VIN_CHARACTER_SUBSTITUTED` | length not 17 after removing separators: `VIN_LENGTH_INVALID`; a character outside the alphabet after the fixes: `VIN_CHARACTER_INVALID`; check digit fails: `VIN_CHECK_DIGIT_INVALID` |
| `borrower_ref` | already canonical | lower case or separators removed: `BORROWER_REF_NORMALISED` | does not match the pattern after normalising: `BORROWER_REF_INVALID` |
| `lender_loan_number` | matches the pattern | | otherwise: `LOAN_NUMBER_INVALID` |
| `origination_date` | `YYYY-MM-DD` or `MM/DD/YYYY` | `DD-Mon-YYYY` with an English three letter month, any case: `DATE_FORMAT_NORMALISED` | not a real date in an accepted format: `DATE_UNPARSEABLE`; after the as-of date: `DATE_IN_FUTURE`; before 2000-01-01: `DATE_TOO_OLD` |
| `original_amount`, `current_balance` | `d+.dd`, or `$` optional with thousands commas in the right places and exactly two decimals | no decimals, one decimal, or `$` and commas without decimals: `AMOUNT_FORMAT_NORMALISED` | anything else, including more than two decimals, parentheses and text: `AMOUNT_UNPARSEABLE`; a leading `-` before an otherwise accepted amount, or original outside 100.00 to 250,000.00: `AMOUNT_OUT_OF_RANGE`; balance is zero: `ZERO_BALANCE`; balance above original: `BALANCE_EXCEEDS_ORIGINAL` |

A row with at least one REJECTED issue is rejected; all its issues (fixed and rejected) are recorded. Field rules are evaluated for every field (no stop at the first problem), so the review list shows everything wrong with a row. For a RELEASE tape only the row rules and the `vin` rules apply.

### 10.5 File level and database level rules (same stage, same transaction)

Applied to rows that passed 10.4, in row order:

1. Duplicate VIN inside the file: the first row is kept, each later one is REJECTED with `DUPLICATE_VIN_IN_FILE` (field `vin`).
2. For BACK_BOOK and PLEDGE, look up the facility's loan for that VIN that is not RELEASED:

| Existing loan | Disposition | Effect |
|---|---|---|
| none | `ACCEPTED_NEW` | Insert a loan in PENDING with these terms. `first_submission_id` is this submission. |
| PENDING or HELD_LOCAL, no tokens stored for it | `ACCEPTED_REPRESENTED` | Update terms, loan number and balance from the row; HELD_LOCAL goes to PENDING. |
| PENDING or HELD_LOCAL, tokens stored, same terms | `ACCEPTED_REPRESENTED` | Update balance; HELD_LOCAL goes to PENDING. |
| PENDING or HELD_LOCAL, tokens stored, different terms | `REJECTED`, `TERMS_CHANGED_AFTER_TOKENIZATION` (field `row`) | None. |
| LOCKED, same terms | `ALREADY_LOCKED` | Update `current_balance_cents` only. Nothing is checked, tokenised or sent for this row. |
| LOCKED, different terms | `REJECTED`, `TERMS_CHANGED_WHILE_LOCKED` (field `row`) | None. The lender must release first. |
| CONFLICT | `REJECTED`, `OPEN_CONFLICT` (field `row`) | None. The loan moves only through resubmission (14.5). |

"Terms" are VIN, borrower reference, origination date and original amount.

3. For RELEASE: the facility's LOCKED loan for that VIN gives `RELEASE_REQUESTED`; otherwise `REJECTED` with `RELEASE_LOAN_NOT_LOCKED` (field `vin`). For a release instruction created through `POST /v1/releases` the raw rows are `{"loan_id": "<uuid>"}` and are resolved by loan id with the same rule.

The stage runs in a single transaction: it inserts all `cleaning_issues` and `submission_items`, inserts or updates loans, writes one `LOAN_STATE_CHANGED` audit entry per state change and one `TAPE_STAGE_ADVANCED` entry, and sets status CLEANED. A crash before commit leaves nothing; the stage simply runs again.

The review list is every `submission_items` row with disposition REJECTED together with its issues (`GET /v1/tapes/{id}/items?disposition=REJECTED`).

---

## 11. Local checks (`RunLocalChecks`, the CHECKED stage)

### 11.1 What is checked

For every item of the submission with disposition `ACCEPTED_NEW` or `ACCEPTED_REPRESENTED`, all four checks always run (no short circuit) and four `local_checks` rows are stored with evidence. "Age" is `as_of_date - origination_date` in days. All windows are measured from the tape's `as_of_date`, never from the wall clock.

Lender name normalisation (`normalise_lender_name`, in `domain/`): upper case; `&` becomes ` AND `; every character outside `A-Z0-9` and space becomes a space; collapse spaces; then repeatedly drop a trailing token sequence from this list: `LLC`, `L L C`, `INC`, `INCORPORATED`, `CORP`, `CORPORATION`, `CO`, `COMPANY`, `LTD`, `LIMITED`, `LP`, `L P`. Two names match when their normalised forms are equal and not empty.

| Check | Source call | Outcome and reason |
|---|---|---|
| `TITLE_EXISTS` | `GET /v1/titles/{vin}` | 200: PASS `TITLE_FOUND`. 404 and age <= `TITLE_PENDING_WINDOW_DAYS`: UNKNOWN `TITLE_PENDING_WINDOW`. 404 otherwise: FAIL `NO_TITLE_RECORD`. |
| `LIENHOLDER_MATCH` | same response | Title found and its lienholder matches the facility lender's legal name: PASS `LIENHOLDER_MATCHES`. Title found, lienholder null or different: FAIL `LIENHOLDER_MISMATCH`. No title and TITLE_EXISTS was UNKNOWN: UNKNOWN `TITLE_PENDING_WINDOW`. No title and TITLE_EXISTS was FAIL: UNKNOWN `NO_TITLE_RECORD`. |
| `PAYMENTS_RECEIVED` | `GET /v1/payments?vin=..&from=<as_of - PAYMENT_WINDOW_DAYS>&to=<as_of>&limit=200` | Age < `FIRST_PAYMENT_GRACE_DAYS`: UNKNOWN `FIRST_PAYMENT_NOT_DUE` (the call is still made and its count recorded). Otherwise at least one payment in the window: PASS `PAYMENT_IN_WINDOW`; none: FAIL `NO_PAYMENT_IN_WINDOW`. |
| `CONTRACT_PRESENT` | `GET /v1/contracts/{vin}` | 404: FAIL `NO_CONTRACT_RECORD`. No contract whose lender matches the facility lender: FAIL `CONTRACT_LENDER_MISMATCH`. A lender-matching contract exists but none has `amount_cents` equal to the original amount and `signed_on` within `CONTRACT_DATE_TOLERANCE_DAYS` of the origination date: FAIL `CONTRACT_TERMS_MISMATCH`. Otherwise PASS `CONTRACT_MATCHES`. |

### 11.2 Evidence

`evidence` is a JSON object stored in the bank database (never logged, never sent out). Required keys: TITLE_EXISTS `{source, found, title_status, title_state, age_days, window_days}`; LIENHOLDER_MATCH `{source, lienholder_on_title, lienholder_normalised, lender_normalised}`; PAYMENTS_RECEIVED `{source, window_days, window_from, window_to, payments_in_window, last_paid_on, deposit_bank_codes, age_days, grace_days}`; CONTRACT_PRESENT `{source, contracts_found, lender_matching_contracts, matching_contract_id, signed_on_tolerance_days}`.

### 11.3 Decision

- Any FAIL: the loan goes PENDING to HELD_LOCAL. Nothing about it is tokenised or sent.
- UNKNOWN is tolerated only with reason `TITLE_PENDING_WINDOW` or `FIRST_PAYMENT_NOT_DUE`. An UNKNOWN `NO_TITLE_RECORD` on LIENHOLDER_MATCH always comes with a FAIL on TITLE_EXISTS.
- No FAIL: the loan stays PENDING and is eligible for tokenisation.

### 11.4 Source failures

A transport error, timeout, 429 or 5xx from outside records is not a check result. After the client's own retries (16.6) it raises `OutsideRecordsUnavailable` (transient): the current chunk's transaction is not committed, the stage attempt fails, and the worker retries with backoff (13.7). A 401 or 422 from outside records is permanent: FAILED with `OUTSIDE_RECORDS_UNAVAILABLE`. An outage therefore never turns honest loans into held loans.

### 11.5 Processing

Loans are processed in `row_number` order in chunks of `CHECK_CHUNK_LOANS`. For each chunk: do all HTTP calls first with no write transaction open, then in one transaction insert the check rows (skipping loans that already have four rows for this submission), apply the decision, write audits, commit. After the last chunk, in one transaction, set status CHECKED. For a RELEASE submission the stage does nothing but advance the status.

---

## 12. Bank database (one per bank)

PostgreSQL 16. SQLAlchemy 2 (typed `Mapped` models under `infrastructure/db/`), Alembic migrations under `bank_agent/migrations/`. Every table and column of BUILD_PLAN section 5 is present with the same name. Additions are marked (+) and justified in 12.3.

Conventions: every `*_id` is `uuid`, every time is `timestamptz`, money is `bigint` cents, hashes are `char(64)` with `CHECK (col ~ '^[0-9a-f]{64}$')`. Every status or code column is `text` with a named `CHECK (col IN (...))` constraint listing the enum values (a fixed enum that can grow by an additive migration). All columns are `NOT NULL` unless marked `null`. Foreign keys are `ON DELETE RESTRICT`.

### 12.1 Tables

**lenders**: `lender_id` PK; `legal_name text` unique; `lei char(20)` unique, check `^[A-Z0-9]{18}[0-9]{2}$`; `created_at`.

**facilities**: `facility_id` PK; `lender_id` FK lenders; `code text` unique (+); `name text`; `credit_limit_cents bigint` check >= 0; `advance_rate_bps int` check 0 to 10000; `status` in (ACTIVE, SUSPENDED, CLOSED); `opened_on date`.

**users**: `user_id` PK; `email text` unique, check `email = lower(email)`; `display_name text`; `role` in (ANALYST, CREDIT_OFFICER, ADMIN); `password_hash text`; `active boolean`; `token_version int` default 0 (+); `created_at` (+); `updated_at` (+).

**login_attempts** (+): `attempt_id bigint` identity PK; `email_sha256 char(64)`; `client_addr inet`; `succeeded boolean`; `at`. Index `(email_sha256, at)`, `(client_addr, at)`.

**tape_submissions**: `submission_id` PK; `facility_id` FK; `kind` in (BACK_BOOK, PLEDGE, RELEASE); `source` in (FILE_UPLOAD, API_INSTRUCTION) (+); `file_name text`; `file_sha256`; `rows_sha256` (+); `as_of_date date` (+); `row_count int` check >= 1; `status` in the seven statuses; `resume_status` null (+); `failed_stage` null (+); `failure_code` null (+); `attempts int` default 0 (+); `next_attempt_at` null (+); `uploaded_by` FK users; `uploaded_at`; `updated_at` (+); `completed_at` null (+). Unique `(facility_id, kind, file_sha256)`. Checks: `(status = 'FAILED') = (failure_code IS NOT NULL)`; `(status = 'FAILED') = (resume_status IS NOT NULL)`; `(status = 'COMPLETED') = (completed_at IS NOT NULL)`. Index `(status, uploaded_at)`.

**loans_raw**: `raw_id` PK; `submission_id` FK; `row_number int` check >= 1; `raw_payload jsonb`; `row_sha256` (+); `received_at`. Unique `(submission_id, row_number)`. Insert-only.

**cleaning_issues**: `issue_id` PK; `raw_id` FK loans_raw; `field text`; `problem text` in the `CleaningProblem` values; `action` in (FIXED, REJECTED). Unique `(raw_id, field, problem)`. Insert-only.

**submission_items** (+): `item_id` PK; `submission_id` FK; `raw_id` FK loans_raw, unique; `row_number int`; `disposition` in (ACCEPTED_NEW, ACCEPTED_REPRESENTED, ALREADY_LOCKED, RELEASE_REQUESTED, REJECTED); `loan_id` FK loans null. Check `(disposition = 'REJECTED') = (loan_id IS NULL)`. Unique `(submission_id, row_number)`; unique `(submission_id, loan_id)`; index `(loan_id)`. Insert-only.

**loans**: `loan_id` PK; `facility_id` FK; `first_submission_id` FK tape_submissions; `lender_loan_number text` (+); `vin char(17)` check alphabet pattern; `borrower_ref text` check pattern; `origination_date date`; `original_amount_cents bigint` check > 0; `current_balance_cents bigint` check > 0 and <= original; `state` in the five states; `created_at` (+); `updated_at`. Unique index `(facility_id, vin) WHERE state <> 'RELEASED'`. Index `(facility_id, state)`, `(vin)`.

**local_checks**: `check_id` PK; `loan_id` FK; `submission_id` FK; `check_type` in the four types; `result` in (PASS, FAIL, UNKNOWN); `reason_code text` (+) in the `CheckReason` values; `evidence jsonb`; `checked_at`. Unique `(loan_id, submission_id, check_type)`. Insert-only.

**loan_tokens**: `loan_id` FK; `token_kind` in the eight kinds; `key_version text` check `^[1-9][0-9]*\.[1-9][0-9]*$`; `token char(64)`; `created_at`. PK `(loan_id, token_kind, key_version)`. Index `(token_kind, token)`. Insert-only.

**registry_requests**: `request_id` PK; `submission_id` FK; `origin` in (TAPE, RESUBMIT) (+); `batch_index int` (+); `idempotency_key char(64)` unique; `kind` in (BACK_BOOK, PLEDGE, RELEASE); `key_version text` (+); `item_count int` check 1 to 500; `attempts int` default 0; `sent_at` null (first attempt); `last_attempt_at` null (+); `response_status int` null; `responded_at` null (+); `registry_submission_id uuid` null (+); `as_of_seq bigint` null (+); `certificate_id uuid` null; `response_body jsonb` null (+, codes only). Unique `(submission_id, batch_index) WHERE origin = 'TAPE'`.

**registry_request_items** (+): `request_id` FK; `item_ref uuid`; `position int`; `loan_id` FK; `result` null in (LOCKED, CONFLICT, RELEASED, REJECTED); `already_held boolean` null; `match_kind` null; `match_basis` null; `kinds_matched text[]` default `{}`; `registry_case_id uuid` null; `lock_id uuid` null; `reason text` null. PK `(request_id, item_ref)`; unique `(request_id, loan_id)`; unique `(request_id, position)`; index `(loan_id)`.

**certificates** (+, the plan mentions certificates only as an id): `certificate_id` PK (the registry's id); `request_id` FK registry_requests, unique; `registry_submission_id uuid`; `kind`; `as_of_seq bigint`; `issued_at`; `body jsonb`; `body_hash`; `signature text`; `signing_key_id text`; `verified boolean`; `verified_at`; `failure_reason text` null in (BAD_SIGNATURE, UNKNOWN_SIGNING_KEY, BODY_HASH_MISMATCH, CONTENT_MISMATCH); `received_at`. Check `verified = (failure_reason IS NULL)`. Insert-only.

**alerts**: `alert_id` PK; `registry_case_id uuid`; `loan_id` FK; `role` in (HOLDER, CLAIMANT) (+); `match_kind` in (EXACT, LIKELY); `match_basis` in (ASSET, FIELDS) (+); `kinds_matched text[]`; `kinds_not_matched text[]` (+); `explanation_text text`; `explanation_source` in (TEMPLATE, MODEL) (+); `status` in (NEW, REVIEWING, CLEARED, ESCALATED); `case_status` in the four case statuses (+); `bank_position` null in (DATA_ERROR, CONFIRMED_DOUBLE_PLEDGE) (+); `position_submitted_at` null (+); `note text` null check length <= 500 (+); `received_at`; `reviewed_by` FK users null; `reviewed_at` null. Unique `(registry_case_id, loan_id)`. Index `(status, received_at)`.

**checkpoints_seen**: `checkpoint_id` PK (+, surrogate, so a forged checkpoint with an already seen seq can be recorded as evidence); `checkpoint_seq bigint`; `last_event_seq bigint`; `head_hash`; `signed_at` (+); `signature text`; `signing_key_id text` (+); `received_at`; `verified boolean`; `verified_at`; `failure_reason text` null in (BAD_SIGNATURE, UNKNOWN_SIGNING_KEY, NON_MONOTONIC, FORK, CHAIN_BREAK, HEAD_MISMATCH) (+); `first_bad_event_seq bigint` null (+). Unique `(checkpoint_seq, head_hash, signature)`. Unique `(checkpoint_seq) WHERE verified`. Insert-only.

**audit_log**: `audit_id` PK; `seq bigint` unique (+); `at`; `actor_type` in (USER, SYSTEM) (+); `actor_user_id` FK users null; `action text`; `object_type text`; `object_id uuid` null; `outcome` in (SUCCESS, DENIED, FAILURE) (+); `request_id text` null (+); `detail jsonb`; `prev_hash` (+); `entry_hash` unique (+). Insert-only.

**integrity_runs** (+): `run_id` PK; `ran_at`; `triggered_by` FK users null; `status` in (OK, BROKEN); `report jsonb`. Insert-only.

### 12.2 Roles and privileges

Two roles per bank database, created by `onepledge-bank db provision` with `PG_ADMIN_URL`:

- `{db}_owner`: `LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE`. Owns the database and every object. Used only by `db migrate` and `seed-reference`.
- `{db}_app`: `LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE`. Used by `serve`, `worker` and every other command.

Provisioning also runs `REVOKE ALL ON DATABASE {db} FROM PUBLIC`, `GRANT CONNECT ON DATABASE {db} TO {db}_app`, `REVOKE CREATE ON SCHEMA public FROM PUBLIC`, `GRANT USAGE ON SCHEMA public TO {db}_app`. One bank's roles therefore cannot connect to another bank's database.

Grants to `{db}_app` (made in a migration, role name from `DB_APP_ROLE`):

| Privilege | Tables |
|---|---|
| SELECT, INSERT | `loans_raw`, `audit_log`, `cleaning_issues`, `submission_items`, `local_checks`, `loan_tokens`, `certificates`, `checkpoints_seen`, `integrity_runs`, `login_attempts` |
| SELECT, INSERT, UPDATE | `tape_submissions`, `loans`, `registry_requests`, `registry_request_items`, `alerts`, `users` |
| SELECT | `lenders`, `facilities`, `alembic_version` |
| none | DELETE and TRUNCATE on every table; any DDL |

### 12.3 Why each addition exists

`facilities.code`: stable human key used by the manifest and seeding. `users.token_version`: revoke tokens on role, password or active change. `login_attempts`: rate limiting that survives restarts. `tape_submissions.source, rows_sha256, as_of_date, resume_status, failed_stage, failure_code, attempts, next_attempt_at, updated_at, completed_at`: release by instruction, raw data verification, deterministic check windows, and resumable failure handling. `loans_raw.row_sha256`: detect edits by a privileged user. `submission_items`: the link from a raw row to a loan (lineage, per row outcome, resumability). `loans.lender_loan_number, created_at`: display and audit. The partial unique index on loans replaces the plan's plain unique `(facility_id, vin)` so a released car can be pledged again as a new row while history is kept. `local_checks.reason_code`: every answer names the rule it rests on. `registry_requests` extras and `registry_request_items`: stored responses for idempotent replay and per item results. `certificates`: store and re-verify what the registry signed. `alerts` extras: the bank's side of the case. `checkpoints_seen` extras: keep failed checkpoints as evidence with the reason. `audit_log` extras: hash chain and attribution. `integrity_runs`: a record of every deep verification.

### 12.4 Integrity mechanisms in the database

1. Insert-only triggers. Function `onepledge_forbid_mutation()` raises `EXCEPTION 'table % is insert-only'` with `ERRCODE = 'OP001'`. For each insert-only table one statement level trigger `BEFORE UPDATE OR DELETE OR TRUNCATE ... FOR EACH STATEMENT` calls it, so the statement is refused even when it would touch zero rows. Triggers fire for the owner as well.
2. Privileges of 12.2: the app role has no UPDATE, DELETE or TRUNCATE on insert-only tables, so it is refused with SQLSTATE `42501` before any trigger runs.
3. Loan state trigger `onepledge_loans_guard()` (`BEFORE UPDATE ON loans`): raises SQLSTATE `'OP002'` unless `(OLD.state, NEW.state)` is equal or one of the transitions of 8.4; raises `'OP003'` when any of `vin`, `borrower_ref`, `origination_date`, `original_amount_cents`, `facility_id` changes while a `loan_tokens` row exists for the loan; always raises `'OP003'` when `loan_id` or `first_submission_id` changes.
4. Tape status trigger `onepledge_tape_guard()` (`BEFORE UPDATE ON tape_submissions`): raises `'OP004'` unless the status change is one of 8.3, and when any of `facility_id`, `kind`, `file_sha256`, `rows_sha256`, `row_count`, `as_of_date`, `uploaded_by`, `uploaded_at` changes.
5. Audit chain: `audit_log.seq` is gapless from 1 and `entry_hash` chains to `prev_hash` (17.1). A trigger `onepledge_audit_guard()` (`BEFORE INSERT ON audit_log`) raises `'OP005'` unless `NEW.seq` equals the current maximum plus 1 and `NEW.prev_hash` equals the previous `entry_hash` (64 zeros for seq 1).
6. The integrity report (15.5) reads `pg_trigger`, `pg_class`, `pg_roles` and `has_table_privilege` to show that these controls are in force right now.

### 12.5 Migrations

- `0001_schema`: tables, constraints, indexes. `0002_guards`: functions and triggers of 12.4. `0003_grants`: grants of 12.2 (reads the app role name from `DB_APP_ROLE`; fails with a clear message if it is unset or the role does not exist).
- Every migration has a working `downgrade`. `alembic upgrade head`, `alembic downgrade base`, `alembic upgrade head` leaves an identical schema (tested).
- `serve` and `worker` refuse to start (one line, exit 1) when the database is not at head.
- A test asserts that the SQLAlchemy models and the migrated schema agree (Alembic autogenerate produces no diff).

---

## 13. The pipeline

### 13.1 Worker

`RunPipelineWorker` runs in a daemon thread inside `serve` (when `PIPELINE_WORKER_ENABLED`) and as `onepledge-bank worker [--once]`. Loop:

1. `SyncRegistryOutbox` if `OUTBOX_POLL_SECONDS` elapsed since the last sync.
2. Pick the next submission: status not COMPLETED and not FAILED, `next_attempt_at` null or in the past, ordered by `uploaded_at, submission_id`, skipping any submission whose facility has an older unfinished (not COMPLETED, not FAILED) submission. This keeps tapes of one facility in upload order while other facilities proceed.
3. Take a session level advisory lock `pg_try_advisory_lock(hashtextextended('onepledge:submission:' || submission_id, 0))` on a dedicated connection. If it is not granted, another worker has it: skip. The lock is released after the stage, and by PostgreSQL itself if the process dies.
4. Run exactly one stage (`AdvanceSubmission`), then loop. With nothing to do, sleep `PIPELINE_POLL_SECONDS`.

`--once` processes until no submission is runnable, then exits 0. Shutdown (SIGTERM, SIGINT) finishes the current transaction and stops; no stage is left half committed because every stage commits in units that are each safe to repeat.

Rule for every stage: no network call is made while a write transaction is open. Read what is needed, call out, then open one transaction, write, audit, commit.

### 13.2 Stage table

| Stage (from status) | Work unit | Idempotency on re-run | Crash at any instant leaves |
|---|---|---|---|
| Intake (to RECEIVED) | One transaction for the whole file | Unique `(facility_id, kind, file_sha256)`: a repeat upload is refused with 409 | Either nothing, or a complete RECEIVED submission |
| Clean (RECEIVED to CLEANED) | One transaction for the whole file | Nothing was committed, so the stage starts from scratch | Status RECEIVED, no derived rows |
| Check (CLEANED to CHECKED) | One transaction per chunk of loans, then one for the status | Loans with four check rows for this submission are skipped | Some chunks done; the rest are done on resume; no loan is checked twice |
| Tokenise (CHECKED to TOKENIZED) | One transaction per chunk, then one for the status | Loans with all eight tokens at the submission's key version are skipped; inserts use `ON CONFLICT DO NOTHING` | Some loans tokenised; blinds were only in memory and are drawn again |
| Submit (TOKENIZED to SUBMITTED) | One transaction to plan all requests; then per request: send, then one transaction to store the response and apply results; then one for the status | Plan is skipped if requests exist; a request with a stored response is never sent again; an unanswered request is re-sent with the same idempotency key and the same body | Plan absent or complete; each request either unanswered (will be re-sent, registry replays or processes once) or fully applied |
| Certify (SUBMITTED to COMPLETED) | Per request: fetch, verify, one transaction to store; then outbox sync; then one transaction for the status | Requests that already have a certificate row are skipped | Some certificates stored; the rest fetched on resume |

### 13.3 Tokenise stage

Eligible loans: items of this submission with disposition ACCEPTED_NEW or ACCEPTED_REPRESENTED whose loan is PENDING. For a RELEASE submission nothing is tokenised (stored tokens are reused) and the stage only advances. Per chunk: build the 8 inputs per loan, blind, call holder 1 then holder 2 (9.3), unblind, then one transaction inserting 8 rows per loan. A loan that already has all eight tokens with other terms cannot exist (12.4 point 3).

Failures: transport error, timeout, 429, 5xx from a holder are transient (`KeyHolderUnavailable`). 401, 409, 413, 422 or a malformed response are permanent: FAILED with `KEY_HOLDER_REJECTED` (or `KEY_VERSION_MISMATCH` for a version change, 9.4).

### 13.4 Submit stage

Plan (one transaction, only if the submission has no TAPE request yet):

- BACK_BOOK and PLEDGE: eligible loans are PENDING loans of this submission's accepted items that have all eight tokens. RELEASE: loans of items with disposition RELEASE_REQUESTED that are LOCKED.
- Order by `row_number`, split into batches of `SUBMIT_BATCH_ITEMS`. For each batch insert one `registry_requests` row (origin TAPE, `batch_index` from 0) and its `registry_request_items` rows, each with a fresh `item_ref` and its `position`.
- Zero eligible loans means zero requests: the stage advances and nothing leaves the bank.

Idempotency key of a request (stored at plan time, never recomputed):

```
items_digest    = sha256("\n".join(asset_token of each item in position order)).hexdigest()
idempotency_key = sha256(("onepledge:idem:v1|" + member_id + "|" + submission_id + "|" + origin + "|" + kind
                          + "|" + str(batch_index) + "|" + items_digest).encode("utf-8")).hexdigest()
```

For a resubmission (14.5) `str(batch_index)` is replaced by `"case:" + ",".join(sorted(cleared case ids))`.

Request body: built only from stored rows (`item_ref`, tokens, `key_version`, kind), items in position order, so every attempt sends the same body.

Send, for each request without a stored response, in `batch_index` order: increment `attempts` and set `sent_at`/`last_attempt_at` in a small transaction; `POST /v1/submissions` with `Idempotency-Key`; then:

| Registry answer | Action |
|---|---|
| 201, or 200 with `Idempotent-Replay: true` | One transaction: store `response_status`, `responded_at`, `registry_submission_id`, `as_of_seq`, `certificate_id`, `response_body`; apply every item result (13.5); audit `REGISTRY_RESPONSE_RECEIVED`. The response is validated first: same number of results as items, every `item_ref` known, every `asset_token` equal to the one sent; otherwise permanent `REGISTRY_REJECTED_REQUEST`. |
| Transport error, timeout, 429, 500, 502, 503, 504 | Transient `RegistryUnavailable`: the stage attempt fails; earlier requests of this submission keep their applied results; the request is sent again later with the same key. |
| 409 `KEY_VERSION_MISMATCH` | FAILED with `KEY_VERSION_MISMATCH`. |
| 401, 409 `IDEMPOTENCY_KEY_REUSED`, 413, 422, any other 4xx | FAILED with `REGISTRY_REJECTED_REQUEST`. |

Partial registry failure therefore means: batches that were answered are final and their loans have their results; unanswered batches wait at status TOKENIZED and are retried; if the submission ends FAILED their loans stay PENDING and are picked up when the submission is retried or the lender sends them again.

Before sending anything the stage checks the halt rule of 15.4.

### 13.5 Applying item results

| Kind | Item result | Loan | Also |
|---|---|---|---|
| BACK_BOOK, PLEDGE | LOCKED (either `already_held` value) | PENDING to LOCKED | Audit `LOAN_STATE_CHANGED`. |
| BACK_BOOK, PLEDGE | CONFLICT | PENDING to CONFLICT | Create the CLAIMANT alert for `case_id` if it does not exist (14.2), audit. |
| RELEASE | RELEASED | LOCKED to RELEASED | Audit. |
| RELEASE | REJECTED (`NOT_HOLDER`) | stays LOCKED | Audit `REGISTRY_ITEM_REJECTED` with outcome FAILURE. The mismatch (the bank believes it holds a lock the registry does not know) is shown on the tape item. |
| any | REJECTED, or any unknown result value | unchanged | Stored as REJECTED with `reason` (or `UNKNOWN_RESULT`), audit `REGISTRY_ITEM_REJECTED`. |

### 13.6 Certify stage

For each answered request of the submission without a `certificates` row: `GET /v1/certificates/{certificate_id}`, verify (15.2), store the row (verified or not) and audit `CERTIFICATE_VERIFIED` or `CERTIFICATE_REJECTED`. If any certificate of the submission failed verification: FAILED with `CERTIFICATE_INVALID` (loan states are kept; the integrity report turns BROKEN). Otherwise run `SyncRegistryOutbox` once and set COMPLETED.

### 13.7 Failure handling

- Transient failure of a stage attempt: `attempts += 1`, `next_attempt_at = now + min(2 ** attempts, 60)` seconds, audit `TAPE_STAGE_ATTEMPT_FAILED` with the error code. At `PIPELINE_MAX_ATTEMPTS` the submission becomes FAILED with the matching code (`OUTSIDE_RECORDS_UNAVAILABLE`, `KEY_HOLDER_UNAVAILABLE`, `REGISTRY_UNAVAILABLE`).
- Permanent failure: FAILED at once with its code.
- An unexpected exception: logged with type and stack (no values, 16.5), FAILED with `INTERNAL_ERROR`.
- A completed stage resets `attempts` to 0.

### 13.8 Retry

`POST /v1/tapes/{id}/retry` (CREDIT_OFFICER): allowed when status is FAILED and `failure_code` is one of `OUTSIDE_RECORDS_UNAVAILABLE`, `KEY_HOLDER_UNAVAILABLE`, `REGISTRY_UNAVAILABLE`, `REGISTRY_INTEGRITY_BROKEN`, `INTERNAL_ERROR`. It sets status to `resume_status`, clears the failure fields and `attempts`, audits `TAPE_RETRY_REQUESTED`. Other codes: 409 `TAPE_NOT_RETRYABLE`.

---

## 14. Registry interaction on the bank side

### 14.1 Outbox sync (`SyncRegistryOutbox`)

Runs from the worker every `OUTBOX_POLL_SECONDS`, at the end of the certify stage, from `POST /v1/registry/sync` and from `onepledge-bank registry sync`. It takes the advisory lock `onepledge:outbox` so only one sync runs per bank.

Loop: `GET /v1/outbox?limit=100`; for each message in position order: process it in one transaction, commit, then `POST /v1/outbox/{id}/ack`. Repeat while `has_more`. A crash between commit and ack causes a redelivery; every handler below is idempotent. A transient registry error ends the sync quietly (it runs again on the next tick). An unknown message `type` is acknowledged and audited as `REGISTRY_MESSAGE_IGNORED`.

### 14.2 ALERT messages and alert creation

- Find the loan: the `loan_tokens` row with `token_kind = 'ASSET'` and `token = payload.asset_token`; if several loans share it (a released row and a new row for the same car), the one that is not RELEASED, else the newest. No loan: audit `ALERT_UNMATCHED` (outcome FAILURE) and acknowledge.
- Upsert on `(registry_case_id, loan_id)`: if the alert exists (created earlier from the submission response) nothing changes. Otherwise insert with `status NEW`, `case_status OPEN`, the payload's role, match kind, basis and kinds, `kinds_not_matched` (the remaining kinds, in the fixed kind order), and the explanation (14.3). Audit `ALERT_RECEIVED`.
- An alert never changes a loan's state. Only a stored registry response does (13.5). If the bank crashed before storing the response, the retried request replays it.

### 14.3 Explanation (`AlertExplainer`)

Interface: `explain(alert_facts: AlertFacts) -> Explanation(text, source)`. `AlertFacts` holds only role, match kind, match basis, kinds matched and kinds not matched. It has no loan value, so no implementation can leak one. Layer 1 ships `TemplateAlertExplainer` only (source TEMPLATE). It is deterministic.

Field wording: vehicle (ASSET or VIN matched), borrower (BORROWER), original amount (finest matched of AMOUNT_EXACT "exactly", AMOUNT_100 "to the nearest 100 dollars", AMOUNT_1000 "to the nearest 1,000 dollars"), origination date (DATE_DAY "same day", DATE_MONTH "same month"). The text is built from these sentences:

1. Opening by role and basis. CLAIMANT, ASSET: "Another registry member already holds a lock on this vehicle, so this pledge was not locked." CLAIMANT, FIELDS: "This vehicle code is not locked by anyone, but another registry member holds a lock on a loan that agrees with this one on several fields, so this pledge was held and not locked." HOLDER, ASSET: "Another registry member tried to pledge the vehicle that this bank holds a lock on. This bank's lock is unchanged." HOLDER, FIELDS: "Another registry member tried to pledge a loan that agrees with this locked loan on several fields but carries a different vehicle code. This bank's lock is unchanged."
2. Agreement: "The other pledge agrees with this loan on: <list>." and, when something differs, "It differs on: <list>." For EXACT: "The other pledge agrees with this loan on every compared field: vehicle, borrower, original amount and origination date."
3. Reading. EXACT: "This is the pattern of the same loan pledged twice." LIKELY, ASSET: "This is the pattern of a copy of the loan with some details changed." LIKELY, FIELDS: "This is the pattern of a copy of the loan with the vehicle identifier changed. It can also be a coincidence, for example one borrower financing two cars on the same day for the same amount."
4. Closing: "The registry does not reveal who the other member is. Review the loan with the lender and record the bank's position on the case."

### 14.4 CASE_UPDATE messages

Find the alert by `(registry_case_id, loan via asset token)`. If the message's status equals the stored `case_status`, do nothing. Otherwise set `case_status`, and `status` CLEARED (for CLEARED_DATA_ERROR, CLEARED_LATE_RELEASE) or ESCALATED (for CONFIRMED) when the alert is NEW or REVIEWING. Audit `ALERT_CASE_UPDATED`. The loan state does not change here.

### 14.5 Review, position, resubmission

`ReviewAlert`:

- START_REVIEW: NEW to REVIEWING; sets `reviewed_by`, `reviewed_at`, optional `note`; audit `ALERT_REVIEW_STARTED`. Any other status: 409 `ALERT_TRANSITION_INVALID`.
- SUBMIT_POSITION (CREDIT_OFFICER; alert REVIEWING; `position` required): if `bank_position` is already set to a different value, 409 `POSITION_ALREADY_RECORDED`. Call `POST /v1/cases/{case_id}/resolution`. On 200 store `bank_position`, `position_submitted_at`, reviewer and note; if the returned status is no longer OPEN apply it as in 14.4; audit `ALERT_POSITION_SUBMITTED`. Registry 409 `CASE_ALREADY_RESOLVED`: run a sync and return the alert. Transient registry failure: 503 `DEPENDENCY_UNAVAILABLE`, nothing stored.

`ResubmitLoan` (`POST /v1/loans/{id}/resubmit`, CREDIT_OFFICER): allowed when the loan is CONFLICT and every alert on it is CLEARED; otherwise 409 `LOAN_NOT_RESUBMITTABLE`. It creates (or finds, by idempotency key) one `registry_requests` row with origin RESUBMIT, kind PLEDGE, one item, attached to the loan's latest submission; sends it; applies the result (LOCKED: CONFLICT to LOCKED; CONFLICT: stays CONFLICT and a new alert is created for the new case); fetches and verifies the certificate; audits `LOAN_RESUBMITTED`. Because the key is derived from the cleared case ids, repeating the call replays the same registry decision.

### 14.6 Release

A release is a submission of kind RELEASE, from a lender's release file (`POST /v1/tapes`, CREDIT_OFFICER) or from `POST /v1/releases` (CREDIT_OFFICER). `CreateReleaseInstruction` validates that every loan id belongs to the facility and is LOCKED (else 409 `LOAN_NOT_LOCKED` listing the ids), builds the canonical JSON `{"as_of_date", "facility_id", "loan_ids": sorted}` as the "file" (`file_name` `release-instruction.json`, `file_sha256` of those bytes, one raw row per loan id), and stores it exactly like an upload. The pipeline then runs: clean (resolve loans), check (no-op), tokenise (no-op), submit (kind RELEASE, asset tokens only), certify. After RELEASED the car can be pledged by any bank without an alert, and here it would be a new loan row.

---

## 15. Checkpoints, certificates and the integrity report

### 15.1 Trust anchor

The registry's Ed25519 public keys come only from `REGISTRY_PUBLIC_KEYS` (environment). The bank agent never fetches a verification key from the registry.

### 15.2 Certificate verification (`VerifyCertificate`)

In this order; the first failure gives `failure_reason`:

1. `signing_key_id` is configured, else `UNKNOWN_SIGNING_KEY`.
2. `sha256(canonical_json(body)) == body_hash`, else `BODY_HASH_MISMATCH`.
3. The Ed25519 signature over `canonical_json(body)` verifies, else `BAD_SIGNATURE`.
4. Content matches what the bank sent and was told: `body.certificate_id` is the id requested; `body.member_id` is `REGISTRY_MEMBER_ID`; `body.submission_id`, `kind`, `key_version`, `as_of_seq` equal the stored response; `body.items` has the same asset tokens in the same order as the request, with status LOCKED, HELD, RELEASED, REJECTED corresponding to the stored results LOCKED, CONFLICT, RELEASED, REJECTED; `summary` equals the counts of `items`. Else `CONTENT_MISMATCH`.

The row is stored either way (`verified` true or false). A certificate links to its submission through `certificates.request_id -> registry_requests.submission_id`, and to loans through `registry_request_items`.

### 15.3 Checkpoint verification (`VerifyCheckpoint`)

Input: a signed checkpoint from a CHECKPOINT message. `prev` is the stored verified checkpoint with the highest `checkpoint_seq` (none at first).

1. A stored row with the same `(checkpoint_seq, head_hash, signature)`: duplicate delivery, acknowledge, store nothing.
2. `signing_key_id` not configured: store with `UNKNOWN_SIGNING_KEY`.
3. Signature over `canonical_json(body)` does not verify: `BAD_SIGNATURE`.
4. A verified row exists with the same `checkpoint_seq` and a different `head_hash` or `last_event_seq`: `FORK`.
5. With `prev`: `checkpoint_seq <= prev.checkpoint_seq` or `last_event_seq < prev.last_event_seq`: `NON_MONOTONIC`.
6. Hash continuity. Fetch event headers with `GET /v1/ledger/events?view=headers&after_seq=<prev.last_event_seq or 0>&limit=1000`, paging until `last_event_seq`. Verify in order: seqs are consecutive starting at `prev.last_event_seq + 1` (or 1); the first event's `prev_hash` equals `prev.head_hash` (or 64 zeros); each event's `prev_hash` equals the previous event's `event_hash`; each `event_hash` equals the SHA-256 of the canonical header (rule in `registry.yaml`). Any violation: `CHAIN_BREAK` with `first_bad_event_seq`. Then the last event's `event_hash` must equal `head_hash`, else `HEAD_MISMATCH` with `first_bad_event_seq = last_event_seq`. If `last_event_seq == prev.last_event_seq`, `head_hash` must equal `prev.head_hash`, else `HEAD_MISMATCH`.
7. Store the row `verified = true`; audit `CHECKPOINT_VERIFIED`.

Every failure is stored as a row with `verified = false`, the reason and (where known) `first_bad_event_seq`, audited as `CHECKPOINT_REJECTED` (outcome FAILURE), and acknowledged. Failed rows are never removed. A transient failure while fetching headers stores nothing and does not acknowledge, so the message is retried.

### 15.4 Halt rule

When `HALT_ON_REGISTRY_INTEGRITY_FAILURE` is true and any `checkpoints_seen` row has `verified = false`, the submit stage sends nothing and the submission becomes FAILED with `REGISTRY_INTEGRITY_BROKEN` (retryable only in the sense that an operator may retry after setting the variable to false, a deliberate act that is audited at startup as `HALT_RULE_DISABLED`).

### 15.5 Integrity report (`GET /v1/integrity`)

Shape: `IntegrityReport` in `bank_agent.yaml`. Sections:

| Section | `GET /v1/integrity` computes | BROKEN when |
|---|---|---|
| `registry_chain` | Counts of checkpoints received and verified, the last verified checkpoint, every failed row | Any failed checkpoint row exists, or the last deep verification found a mismatch. NOT_VERIFIED when no checkpoint was received yet. |
| `certificates` | Counts and failed rows | Any certificate row has `verified = false`, or the last deep verification failed one. |
| `audit_log` | Entry count, last seq, and the result of the last deep verification | The last deep verification found a bad seq. NOT_VERIFIED before the first deep verification. |
| `raw_data` | The result of the last deep verification | The last deep verification found a submission whose rows do not hash to `rows_sha256`. NOT_VERIFIED before the first. |
| `database_controls` | Live catalogue queries | Any control is false. |

`database_controls.controls` has exactly these names: `LOANS_RAW_IMMUTABILITY_TRIGGER_ENABLED`, `AUDIT_LOG_IMMUTABILITY_TRIGGER_ENABLED`, `EVIDENCE_TABLES_IMMUTABILITY_TRIGGERS_ENABLED` (the other insert-only tables), `LOAN_STATE_GUARD_TRIGGER_ENABLED`, `AUDIT_CHAIN_GUARD_TRIGGER_ENABLED`, `APP_ROLE_CANNOT_UPDATE_LOANS_RAW`, `APP_ROLE_CANNOT_DELETE_LOANS_RAW`, `APP_ROLE_CANNOT_UPDATE_AUDIT_LOG`, `APP_ROLE_CANNOT_DELETE_AUDIT_LOG`, `APP_ROLE_IS_NOT_OWNER_OR_SUPERUSER`, `SCHEMA_AT_MIGRATION_HEAD`. "Enabled" means the trigger exists and `pg_trigger.tgenabled` is `O` or `A`.

Top level `status` is BROKEN when any section is BROKEN, else OK. The endpoint always returns 200.

### 15.6 Deep verification (`RunDeepVerification`, `POST /v1/integrity/verify`, `onepledge-bank integrity verify`)

Recomputes from stored data, without trusting stored flags:

1. Audit log: walk all entries in seq order; seqs gapless from 1; each `prev_hash` equals the previous `entry_hash`; each `entry_hash` equals the recomputed hash (17.1). Report `first_bad_seq`.
2. Raw data: for every submission recompute each `row_sha256` from `raw_payload` and `rows_sha256` from the rows, and compare `row_count`. Report `bad_submission_ids`.
3. Certificates: re-run 15.2 steps 1 to 3 on every stored certificate.
4. Checkpoints: re-verify every stored signature; fetch all event headers from seq 1 to the highest stored `last_event_seq`; verify the whole chain; for every stored verified checkpoint, the event at its `last_event_seq` must have `event_hash == head_hash`. The first violation is reported as a failure with `first_bad_event_seq` (for a head mismatch: the previous good checkpoint's `last_event_seq + 1`, the start of the range that contains the change). This catches a history rewrite that happened before the bank's latest checkpoint.
5. Database controls as in 15.5.

The report is stored in `integrity_runs` (insert-only), audited as `INTEGRITY_VERIFICATION_RUN`, and returned. If the registry cannot be reached: 503 `DEPENDENCY_UNAVAILABLE` and nothing is stored.

---

## 16. Security

### 16.1 Passwords

- Argon2id through `argon2-cffi` `PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4, hash_len=32, salt_len=16)`. The encoded hash string is stored in `users.password_hash`.
- Minimum length 12, maximum 256. No other composition rule. The password is never logged, never audited, never returned.
- On a successful login `check_needs_rehash` is honoured (the hash is replaced when parameters changed).
- An unknown email still runs one verification against a fixed dummy hash, so timing does not reveal whether an account exists.

### 16.2 Login and tokens

- `POST /v1/auth/login`: the email is trimmed and lower cased. Unknown email, wrong password and inactive user all return 401 `INVALID_CREDENTIALS` with the same body.
- Rate limit, checked before the password is verified: failed attempts for this email hash since the later of (now minus `LOGIN_WINDOW_SECONDS`) and the last successful login for it are counted; at `LOGIN_MAX_FAILURES_PER_EMAIL` or more, or `LOGIN_MAX_FAILURES_PER_ADDRESS` or more failures from this client address in the window: 429 `LOGIN_RATE_LIMITED` with `Retry-After` (seconds until the oldest counted failure leaves the window). Every attempt is one `login_attempts` row and one audit entry (`LOGIN_SUCCEEDED`, `LOGIN_FAILED`, `LOGIN_RATE_LIMITED`) carrying the email hash, never the email of an unknown account and never the password.
- Token: JWT, `HS256`, signed with `JWT_SECRET` (at least 32 bytes, else startup fails). Claims: `iss` `onepledge-bank-agent`, `aud` the bank code, `sub` the user id, `role`, `tv` (the user's `token_version`), `iat`, `nbf`, `exp` (`iat` plus `ACCESS_TOKEN_TTL_SECONDS`), `jti`.
- Verification on every request: algorithm list exactly `["HS256"]`; `iss`, `aud`, `exp`, `iat`, `sub` required; leeway 5 seconds; then the user is loaded and must be active with the same role and the same `token_version`. Any failure: 401 `UNAUTHENTICATED` with header `WWW-Authenticate: Bearer`.
- No refresh tokens. A change of role, active flag or password increments `token_version`, which invalidates every token of that user at once.
- A token of one bank is rejected by another bank's agent (different secret and different audience).

### 16.3 Authorisation

One class `AccessPolicy` holds the permission matrix of 18.2. A request with a valid token but an insufficient role gets 403 `FORBIDDEN` and an audit entry `ACCESS_DENIED` (actor, method, route template). The matrix in `bank_agent.yaml` (`x-roles`), in `AccessPolicy` and in section 18.2 MUST be identical; a contract test compares the first two.

### 16.4 Service to service

- Outbound only: the bank agent presents `X-API-Key` to the registry, each key holder and outside records. Each key is distinct. Keys are read from the environment, held in memory, never logged.
- Inbound: none. No endpoint of the bank agent accepts a service key; everything except login and health needs a user token.
- The receiving services store only SHA-256 hashes of keys (4).

### 16.5 Logging and redaction

- structlog, JSON, one object per line on stdout. Fixed keys: `ts`, `level`, `event`, `service`, `bank_code` (bank agent), `request_id` (when in a request).
- `event` is a fixed lower snake case name from one enumeration in the code (for example `tape_stage_advanced`). It is never a formatted string.
- Redaction processor (`RedactionProcessor`, last before rendering, applied to every record including records from the standard `logging` module, uvicorn and SQLAlchemy, which are routed through structlog):
  1. Key allow-list. Only these keys pass: `ts`, `level`, `event`, `service`, `bank_code`, `request_id`, `logger`, `submission_id`, `loan_id`, `alert_id`, `registry_request_id`, `certificate_id`, `checkpoint_seq`, `user_id`, `role`, `stage`, `status`, `kind`, `count`, `rows`, `attempts`, `duration_ms`, `http_status`, `method`, `route`, `error_code`, `failure_code`, `check_type`, `result`, `reason_code`, `batch_index`, `item_count`, `exception_type`, `stack`, `dependency`. Every other key is dropped and its name is added to a `redacted_keys` list.
  2. Value shape. A passing value must be an int, a bool, null, or a string matching `^[A-Za-z0-9_.:{}/\- ]{0,128}$` (`stack` is exempt from the length and may contain newlines). Anything else becomes `"[REDACTED]"`.
  3. Value scrub on every remaining string, including `stack`: any run matching the case-insensitive VIN shape (17 characters of the VIN alphabet with at least 3 digits, on word boundaries) becomes `[REDACTED]`.
- `stack` contains frame locations (file, line, function) only. Exception messages and local variables are never rendered. `route` is always the route template, never the path.
- SQLAlchemy engines are created with `hide_parameters=True`. The `httpx`, `httpcore` and `uvicorn.access` loggers are set to WARNING, and uvicorn's access log is off; the service writes its own access record (method, route template, status, duration, user id, role).
- A test (21.5) runs a tape with known canary values through the whole pipeline and through failing paths, captures all log output of the bank agent and outside records, and asserts that no canary VIN (any case), borrower reference, loan number, amount (cents and dollar forms) or date (three formats) appears in any field other than `ts`.

### 16.6 Input validation and outbound calls

- Every request body and parameter is validated by a pydantic model with `extra="forbid"` and explicit bounds (lengths, ranges, patterns) equal to the contract. Validation errors return 422 `VALIDATION_ERROR` with `details` naming field and issue code; the handler strips pydantic's `input` and `ctx` so no submitted value is echoed.
- JSON bodies above 64 KiB: 413. Tape uploads: limits of 10.1, enforced while streaming (the server never buffers more than the limit plus one chunk).
- CSV injection: cells are data, never evaluated; every cleaned text field must match a strict pattern (8.1) and formula-leading cells are rejected (10.4); API output is JSON only. The raw text is kept unchanged in `loans_raw` and shown only as JSON string values in the lineage view.
- Outbound HTTP (`infrastructure/http/`): one `httpx.Client` per dependency with connect and read timeouts from settings, no redirects, connection pooling. Each call is retried up to 2 more times (0.2 s, 0.4 s) on transport errors, timeouts, 502, 503, 504; every call the bank agent makes is idempotent by design. After that the typed transient error is raised. Responses are parsed into pydantic models with `extra="ignore"` (additive evolution) and required fields enforced; a malformed response is a permanent protocol error.
- Response headers on every bank agent response: `Cache-Control: no-store`, `X-Content-Type-Options: nosniff`, `X-Request-ID`.
- Unhandled exceptions return 500 `INTERNAL_ERROR` with the fixed message and the request id. Nothing else.

### 16.7 Secrets

Only from the environment (3). No default value for any secret in code. `.env` is gitignored and written 0600. The repository contains no key, password, token or scalar except the published test vectors under `contracts/examples/vectors/`, which are marked as public test data and are rejected at startup by the doubles' and bank agent's settings if someone configures them as real secrets (the settings loader compares against the vector values).

---

## 17. Auditability and transparency

### 17.1 Bank audit log

Every state changing user action, every denied or failed access attempt, and every system step that changes or verifies something writes one `audit_log` row in the same transaction as the change it records (attempts that change nothing, such as a failed login, are written in their own transaction).

Hash chain: `seq` starts at 1 and has no gaps. `prev_hash` is the previous entry's `entry_hash` (64 zeros for seq 1). `entry_hash = sha256(canonical_json({"action", "actor_type", "actor_user_id", "at", "audit_id", "detail", "object_id", "object_type", "outcome", "prev_hash", "request_id", "seq"}))`, where `at` is rendered `YYYY-MM-DDTHH:MM:SS.ffffffZ` (always six fraction digits; the API returns the same string) and ids are lower case strings or null.

Writer (`infrastructure/audit.py`): inside the caller's transaction it takes `pg_advisory_xact_lock(hashtextextended('onepledge:audit', 0))`, reads the last `(seq, entry_hash)`, and inserts the new entries. Use cases collect their audit entries and the unit of work appends them as the last statements before commit, so the lock is held only briefly. The database trigger of 12.4 point 5 rejects a wrong `seq` or `prev_hash`.

`detail` holds identifiers, codes, counts and hashes only. It MUST NOT hold a VIN, borrower reference, loan number, amount, date of a loan, email of an unknown account, password, token, API key, or free text typed by a user.

### 17.2 Audit actions (`AuditAction`)

| Action | Object type | Written when |
|---|---|---|
| `LOGIN_SUCCEEDED`, `LOGIN_FAILED`, `LOGIN_RATE_LIMITED` | USER (object id null for an unknown email) | Every login attempt. |
| `ACCESS_DENIED` | ROUTE | A 403. |
| `USER_CREATED`, `USER_UPDATED` | USER | User administration (detail lists changed field names, never values). |
| `TAPE_UPLOADED`, `TAPE_UPLOAD_REJECTED` | TAPE_SUBMISSION | Intake accepted or refused (detail: kind, row count, file hash, or error code). |
| `RELEASE_REQUESTED` | TAPE_SUBMISSION | `POST /v1/releases`. |
| `TAPE_STAGE_ADVANCED` | TAPE_SUBMISSION | Each status change (detail: from, to, counts of that stage). |
| `TAPE_STAGE_ATTEMPT_FAILED`, `TAPE_FAILED`, `TAPE_RETRY_REQUESTED` | TAPE_SUBMISSION | Failure handling. |
| `LOAN_STATE_CHANGED` | LOAN | Each loan state change (detail: from, to, submission id, reason code such as the failed check types or the registry result). |
| `REGISTRY_REQUEST_SENT`, `REGISTRY_RESPONSE_RECEIVED`, `REGISTRY_ITEM_REJECTED` | REGISTRY_REQUEST | Submit stage and resubmission. |
| `CERTIFICATE_VERIFIED`, `CERTIFICATE_REJECTED` | CERTIFICATE | Certify stage. |
| `ALERT_RECEIVED`, `ALERT_UNMATCHED`, `ALERT_REVIEW_STARTED`, `ALERT_POSITION_SUBMITTED`, `ALERT_CASE_UPDATED` | ALERT | Alert life cycle. |
| `LOAN_RESUBMITTED` | LOAN | Resubmission. |
| `CHECKPOINT_VERIFIED`, `CHECKPOINT_REJECTED` | CHECKPOINT | Every checkpoint received. |
| `REGISTRY_MESSAGE_IGNORED` | REGISTRY_MESSAGE | Unknown outbox message type. |
| `REGISTRY_SYNC_REQUESTED` | REGISTRY | A user triggered sync. |
| `INTEGRITY_VERIFICATION_RUN` | INTEGRITY_RUN | Deep verification (detail: status per section). |
| `LINEAGE_VIEWED`, `AUDIT_LOG_VIEWED` | LOAN, AUDIT_LOG | Reads of the two most sensitive views. |
| `HALT_RULE_DISABLED` | CONFIG | Startup with the halt rule off. |

### 17.3 Lineage view (`GET /v1/loans/{id}/lineage`, `onepledge-bank lineage show`)

Shape `LoanLineage`. For one loan it shows, per tape the loan appeared on, oldest first: the tape (file name, file hash, as-of date), the raw row exactly as sent with its row hash, the cleaning issues, the four checks with reason codes and evidence, the registry request (idempotency key, attempts, status) with this loan's item result, and the certificate with its verification status; then the loan's tokens (codes and key version, never inputs), its alerts, and the audit entries about the loan, its alerts and its submissions (at most 200, oldest first). Every element carries the identifier needed to look it up on its own.

### 17.4 What a demo audience can point at

| Property | Where it is visible |
|---|---|
| Every answer names what it rests on | Check `reason_code` and evidence; alert `match_basis`, `kinds_matched`, explanation; certificate `as_of_seq`. |
| History cannot be edited quietly | The app role is refused by PostgreSQL (shown in the demo); the audit chain and raw row hashes expose an edit by a superuser (`integrity verify`). |
| Others can check the registry without trusting it | Stored signed checkpoints, chain verification, `/v1/integrity` turning BROKEN with the reason and seq. |
| Nothing raw leaves the bank | Tape counts (`items_submitted`), the doubles' received bodies scanned by the scorecard, the log scan. |
| Who did what | `/v1/audit-log`, `onepledge-bank audit show`. |

---

## 18. Bank agent HTTP API

### 18.1 Contract

`contracts/bank_agent.yaml` is the definition: paths, parameters, schemas, status codes, error codes. The implementation MUST match it exactly. A contract test (21.4) checks that the FastAPI app's routes, methods and response status codes equal the YAML, validates live responses of every endpoint against the YAML schemas, and validates every example.

General rules:

- All routes are under `/v1` except `/healthz` and `/readyz`. FastAPI's generated docs are off unless `EXPOSE_API_DOCS=true`.
- Paging: `limit` 1 to 200 (default 50), `offset` >= 0; envelope `{items, total, limit, offset}`; out of range values are 422. Orderings are as stated per path in the YAML and are total (ties broken by id).
- Unknown id of the right shape: 404 with the specific code where the YAML names one, else `NOT_FOUND`. A malformed id: 422.
- A route never contains a rule. It validates, builds a command, calls `AccessPolicy`, calls one use case, maps the result.

### 18.2 Permission matrix

Y means allowed. Everything else is 403 `FORBIDDEN` (with a valid token) or 401 (without).

| Endpoint | ANALYST | CREDIT_OFFICER | ADMIN |
|---|---|---|---|
| `POST /v1/auth/login`, `GET /healthz`, `GET /readyz` | public | public | public |
| `GET /v1/auth/me` | Y | Y | Y |
| `GET /v1/facilities` | Y | Y | |
| `POST /v1/tapes` with kind BACK_BOOK or PLEDGE | Y | Y | |
| `POST /v1/tapes` with kind RELEASE | | Y | |
| `GET /v1/tapes`, `GET /v1/tapes/{id}`, `GET /v1/tapes/{id}/items` | Y | Y | |
| `POST /v1/tapes/{id}/retry` | | Y | |
| `POST /v1/releases` | | Y | |
| `GET /v1/loans`, `POST /v1/loans/search`, `GET /v1/loans/{id}`, `GET /v1/loans/{id}/lineage` | Y | Y | |
| `POST /v1/loans/{id}/resubmit` | | Y | |
| `GET /v1/alerts`, `GET /v1/alerts/{id}` | Y | Y | |
| `POST /v1/alerts/{id}/review` action START_REVIEW | Y | Y | |
| `POST /v1/alerts/{id}/review` action SUBMIT_POSITION | | Y | |
| `GET /v1/certificates`, `GET /v1/certificates/{id}` | Y | Y | |
| `GET /v1/integrity` | Y | Y | Y |
| `POST /v1/integrity/verify`, `POST /v1/registry/sync` | | Y | Y |
| `GET /v1/audit-log` | | Y | Y |
| `GET /v1/users`, `POST /v1/users`, `PATCH /v1/users/{id}` | | | Y |

ADMIN has no access to loan, tape, alert or certificate data. An ADMIN cannot deactivate or demote the last active ADMIN (409 `LAST_ADMIN`) and cannot change their own role.

### 18.3 Status codes and error codes

| Status | Codes |
|---|---|
| 401 | `INVALID_CREDENTIALS` (login only), `UNAUTHENTICATED` |
| 403 | `FORBIDDEN` |
| 404 | `NOT_FOUND`, `FACILITY_NOT_FOUND` |
| 409 | `TAPE_DUPLICATE`, `TAPE_NOT_RETRYABLE`, `LOAN_NOT_LOCKED`, `LOAN_NOT_RESUBMITTABLE`, `ALERT_TRANSITION_INVALID`, `POSITION_ALREADY_RECORDED`, `USER_EMAIL_TAKEN`, `LAST_ADMIN` |
| 413 | `TAPE_TOO_LARGE`, `VALIDATION_ERROR` (JSON body too large) |
| 422 | `VALIDATION_ERROR`, `TAPE_NOT_UTF8`, `TAPE_EMPTY`, `TAPE_HEADER_INVALID`, `TAPE_MALFORMED` |
| 429 | `LOGIN_RATE_LIMITED` |
| 500 | `INTERNAL_ERROR` |
| 503 | `DEPENDENCY_UNAVAILABLE`, `NOT_READY` |

Fixed messages (one table in code, used verbatim; examples): `INVALID_CREDENTIALS` "The email or password is incorrect."; `FORBIDDEN` "Your role does not allow this action."; `TAPE_DUPLICATE` "This file was already uploaded for this facility and kind."; `TAPE_HEADER_INVALID` "The file header is missing a required column or has a duplicate column."; `VALIDATION_ERROR` "The request is not valid. See details."; `DEPENDENCY_UNAVAILABLE` "A service this action depends on is not reachable. Nothing was changed. Try again."; `INTERNAL_ERROR` "An unexpected error occurred. Quote the request id when reporting it." Every other code has an equally plain sentence. Messages end with a full stop, contain no identifiers and no request data.

---

## 19. Command line interfaces

All commands use `argparse`, print human readable aligned text by default and JSON with `--json`, write errors to stderr as one sentence, never print a traceback for an expected error, never print a secret, and exit 0 on success, 1 on failure, 2 on usage error.

```
onepledge-bank db provision | db migrate | db status
onepledge-bank seed-reference --data-dir DIR        lenders and facilities of BANK_CODE from reference.json (owner role), idempotent
onepledge-bank users create --email E --name N --role R --password-env VAR
onepledge-bank users list
onepledge-bank serve
onepledge-bank worker [--once]
onepledge-bank registry sync
onepledge-bank tapes list | tapes show SUBMISSION_ID [--items] [--rejected-only]
onepledge-bank loans show LOAN_ID
onepledge-bank lineage show LOAN_ID
onepledge-bank alerts list
onepledge-bank integrity report | integrity verify
onepledge-bank audit show [--after-seq N] [--limit N] [--action A]
```

Read commands (`tapes`, `loans`, `lineage`, `alerts`, `integrity report`, `audit show`) call the same use cases as the API and print tables with a title line, column headers, aligned columns, counts and a closing line. Hashes are shown as the first 12 characters followed by `...` unless `--full`. `loans show` and `lineage show` display raw values (they are the bank's own data on the bank's own terminal); nothing else does. Every CLI invocation that reads loan data writes the same audit entries as the API with `actor_type` SYSTEM and detail `{"via": "cli"}`.

Other packages: `onepledge-datagen` (5.1), `onepledge-outside` (6.3), `onepledge-fake-keyholder serve`, `onepledge-fake-registry serve`, `onepledge-contracts check` (7.4), `onepledge-eval` (20).

---

## 20. evaluation

Package `onepledge_evaluation`: the layer 1 harness. Sub-packages `runtime` (environment file, provisioning, local stack), `replay` (walk the manifest through the bank APIs), `scorecard` (the only code that reads the answer key), `demo` (the narrative runner), `interface` (CLI).

### 20.1 Commands

```
onepledge-eval env init [--force]
onepledge-eval stack up | down | status
onepledge-eval stack env SERVICE          print shell export lines of that service's process environment (for eval in a developer shell)
onepledge-eval seed --data-dir DIR
onepledge-eval replay --data-dir DIR [--until-step SXX]
onepledge-eval score --data-dir DIR [--no-enforce]
onepledge-eval scorecard --seed N --profile P [--keep-running] [--no-enforce]
onepledge-eval demo --seed N --profile P [--keep-running]
```

### 20.2 Runtime

- `LocalStack` starts seven processes with `subprocess.Popen` using the console scripts: registry double, key holder doubles 1 and 2, outside records, three bank agents. Service names (used for log and pid files): `registry-fake`, `keyholder-1-fake`, `keyholder-2-fake`, `outside-records`, `bank-harbor`, `bank-northgate`, `bank-calder`. `up` is idempotent: it starts only the services that are not running, so it also restarts a service that died. Each gets its own environment (3.3), its stdout and stderr appended to `var/log/<service>.jsonl`, and a pid file in `var/run/`. It waits for `/healthz` (and `/readyz` for bank agents) with a 30 second limit per service, and reports which service failed and the last 5 log lines if one does not come up. `down` sends SIGTERM, waits 10 seconds, then SIGKILL. `status` prints one line per service: name, pid, port, up or down.
- A port already in use, PostgreSQL unreachable or a missing `.env` gives a one sentence error and exit 1.
- `Provisioner` runs `db provision` and `db migrate` for the four databases and, for a fresh start, drops existing databases and roles carrying `ONEPLEDGE_DB_PREFIX` first.
- `seed`: outside records data, outside records clients (one per bank), reference data per bank, the nine demo users with `DEMO_USER_PASSWORD`.

### 20.3 Replay

`ReplayDriver` reads `manifest.json` (never the answer key). For each step in order it logs in to that bank as the user with `uploader_role`, uploads the file with the step's facility, kind and as-of date, and polls `GET /v1/tapes/{id}` every 0.5 seconds until COMPLETED or FAILED (limit 600 seconds per step). Steps sharing a `concurrent_group` are uploaded from separate threads released by one barrier and awaited together. After each step it calls `POST /v1/registry/sync` on all three banks (as the credit officer) so alerts raised at other banks are collected. A FAILED tape stops the replay with the tape's failure code. It records, per step: bank, submission id, file SHA-256, status, counts, duration.

### 20.4 Scorecard

Inputs: the data directory (manifest and `answer_key/answer_key.json`), and the running stack. `AnswerKey` (in `scorecard/answer_key.py`) is the only class in the repository that opens the answer key file.

Observation (through the bank APIs with the demo users, never the databases): for every step, `GET /v1/tapes` to find the submission by file hash, then all pages of `GET /v1/tapes/{id}/items`; all pages of `GET /v1/alerts` per bank. For each tape row the observed record is: disposition, cleaning problems and actions, check results, registry result with match kind, basis and kinds, and the alerts whose `loan_id` is the row's loan.

Row outcome: `REJECTED` (disposition REJECTED); `HELD_LOCAL` (accepted, any check FAIL, no registry item); `LOCKED`, `CONFLICT`, `RELEASED` (registry item result); `OTHER` (anything else, for example still PENDING). A row matches its expectation when disposition, outcome, cleaning problems (expected problems are a subset of observed for rejected rows; equal for fixed rows), failed check set (equal), match kind and basis (equal), `kinds_matched_include` (subset) and `kinds_matched_exclude` (disjoint), and the alert expectation (role and match kind, or no alert at all) all hold. A RACE_PAIR case matches when exactly one row is LOCKED with a HOLDER EXACT alert and the other is CONFLICT, EXACT, ASSET.

Metrics:

- Per case type: planted cases, as expected, missed (not as expected), with the list of missed case ids and what was observed.
- False alarms: every row whose expectation has no alert and no conflict but which has an alert or a CONFLICT result, grouped by the case type it belongs to (or BASELINE).
- False holds: every row expected LOCKED that was REJECTED or HELD_LOCAL, grouped the same way.
- Data quality: per messiness kind, planted, handled as expected, not as expected.
- Blind spots: per blind spot type, planted and observed outcome. Reported, never counted as caught or missed.
- Privacy: number of raw values (every VIN in any case, borrower reference, loan number, and every amount in cents and dollar forms and date in three formats that belongs to a planted case row) found in (a) the bodies received by the registry and key holder doubles (`/_test/received`) and (b) the bank agent and outside records log files, ignoring the `ts` field. A value counts only as a whole token, that is, delimited on both sides by a character outside `[A-Za-z0-9]` or by the start or end of the text (so a digit run inside a hex code is not a hit). Expected 0.
- Leakage of identity: number of alert JSON objects fetched from one bank that contain another bank's code, name or member id. Expected 0.

Gates (exit code 1 when any fails, unless `--no-enforce`): every FRAUD case as expected; every HONEST_LOOKALIKE case as expected; zero false alarms; zero false holds; every data quality row as expected; privacy counts zero; identity leakage zero; zero rows with outcome OTHER.

Console output (ASCII, at most 100 columns), then the same content as JSON in `var/reports/scorecard.json` and Markdown in `var/reports/scorecard.md`:

```
OnePledge layer 1 scorecard
Data set: profile full, seed 20261117 (5,3xx tape rows, 15 tapes, 3 banks)

Fraud cases                              Expected outcome          Planted  As expected  Missed
EXACT_COPY                               registry EXACT                40           40       0
COPY_AMOUNT_CHANGED                      registry LIKELY (asset)       25           25       0
...
Honest look-alikes                       Expected outcome          Planted  As expected  False alarms
TWO_CARS_ONE_BORROWER                    locked, no alert              40           40       0
...
Known blind spots (reported, not graded) Observed
BLIND_SPOT_INVENTED_IN_PENDING_WINDOW    5 planted, 5 locked with no alert (see SPEC 23)

Data quality                             Planted  As expected
...
Baseline rows: 4,6xx   false alarms: 0   false holds: 0
Privacy: raw values in registry and key holder traffic: 0   in service logs: 0   identity leaks in alerts: 0

Gates: 8 of 8 passed
```

The numbers are measured from the run. No count, id or expected string from the answer key is hardcoded anywhere.

### 20.5 Demo runner

`onepledge-eval demo`: fresh databases, generate data, seed, start the stack, run the scenes by replaying their steps, print the narrative, run the scorecard on the same run, stop the stack (unless `--keep-running`). Total time for the full profile on the build machine: at most 10 minutes.

Each scene prints a block: a title line, two to six plain sentences with measured numbers, an "Evidence" list (identifiers, truncated hashes, endpoint to look at), and `Result: PASS` or `Result: FAIL (<reason>)` from programmatic assertions. No scene prints a raw loan value. The same content goes to `var/reports/demo_report.md` and `demo_report.json`. Exit code 1 if any scene fails or a scorecard gate fails.

| Scene | Steps | Shows | Assertions |
|---|---|---|---|
| 1. A bank joins and loads its back book | S01 to S05 | Per bank: rows, review list size, loans locked, conflicts found at onboarding, certificates verified, first checkpoint verified. | Every tape COMPLETED; every certificate verified; each bank's integrity `registry_chain` OK. |
| 2. An honest pledge gets a signed certificate | S06, S07 | S06: all rows locked; the certificate id, `as_of_seq`, signing key id, signature verified by the bank. | S06 has zero conflicts and zero held loans; its certificate summary equals the tape counts. |
| 3. The same loans go to a second bank | S08 | Northgate: conflicts EXACT. Harbor: HOLDER alerts on its own loans. One explanation text from each side. Then, for one case, each bank starts a review and its credit officer records CONFIRMED_DOUBLE_PLEDGE; after a sync both alerts are ESCALATED. | Alert counts at both banks equal the expected case count; no alert JSON at one bank contains the other bank's code, name or member id; the confirmed case is ESCALATED at both. |
| 4. A disguised copy is still caught | S09 | Four routes with counts: caught by the registry on the asset (amount or date changed), caught by the registry on field agreement (VIN altered inside the title window), stopped in cleaning (check digit), held by local checks (no title, or contract mismatch). | Counts per route equal the answer key. |
| 5. An invented loan is stopped inside the bank | S10 | Held loans by failed check; the lineage of one held loan (checks and reason codes, no values); registry and key holder traffic counters before and after. | Items received by the registry from Harbor grew by exactly the tape's `items_submitted`; key holder elements grew by exactly 8 times the tokenised loans; held loans have no tokens. |
| 7. A release lets another bank take the loan | S11, S12, S13 | Released count at Harbor; the same cars locked at Northgate (and re-pledged at Harbor) with no alert. | Every moved and refinanced row LOCKED; zero new alerts at any bank. |
| 8. Two banks submit at the same instant | S14, S15 | For each raced loan exactly one bank holds the lock; the other has a conflict; which bank won, per loan count. | For every race pair exactly one LOCKED and one CONFLICT. |
| 9. Someone edits history | none | (a) The app role tries `UPDATE` and `DELETE` on `loans_raw` and `audit_log` and is refused by PostgreSQL (SQLSTATE shown). (b) A superuser disables the trigger, edits one audit row and one raw row, re-enables it: `integrity verify` reports the audit seq and the submission. (c) The registry double rewrites one past event and signs a new checkpoint: after a sync every bank's integrity turns BROKEN with CHAIN_BREAK or HEAD_MISMATCH and the seq; a release instruction created afterwards at Harbor fails with REGISTRY_INTEGRITY_BROKEN and sends nothing. Parts (a) and (b) run against Harbor's database. | All four statements refused; `audit_log.first_bad_seq` and `raw_data.bad_submission_ids` name the edited rows; all three banks BROKEN; the late release sent zero items. |

Scene 9 runs after the scorecard observations are collected, because it deliberately breaks the stack. Scene 6 (the operator opens the registry) belongs to layers 2 and 3; the scorecard's privacy line covers the layer 1 part of it.

The narrative opens with a header (data set, seed, services and ports, start time) and closes with a summary table of scenes and results, the scorecard, the paths of the report files, and the total duration.

---

## 21. Tests

Tests are part of the product. They are written with the code, are deterministic (fixed seeds, injected clocks, no sleeps except in the stack tests), and never encode an expected answer taken from the answer key outside `evaluation`.

### 21.1 Unit (marker `unit`, no I/O)

Every domain class and rule: `Vin` (vectors, every reason), `Money`, `BorrowerRef`, `Lei`, canonical JSON, `clean_row` (one test per row of the 10.4 table, both lender formats), lender name normalisation, each local check decision (every row of 11.1 with boundary ages 29, 30, 31 and 44, 45, and a payment on the first and on the last day of the window), every state machine (every legal transition succeeds, every other pair raises), `AccessPolicy` (every cell of 18.2), idempotency key derivation, token input strings (the vector), `TemplateAlertExplainer` (every role, basis, kind combination; output contains no digit run longer than 5), `RedactionProcessor` (the allow-list equals the list of 16.5 exactly; a disallowed key such as `vin` is dropped and named in `redacted_keys`; each value shape rule; the VIN scrub in upper, lower and mixed case).

Hypothesis properties: VIN check digit (a generated valid VIN parses; changing one character to a different transliteration value always fails; a same-value substitution always passes); cleaning is idempotent on its own output; `Money` round trips; canonical JSON is invariant under key order; two-holder OPRF equals direct evaluation and is independent of the blind; idempotency keys differ when any input differs.

### 21.2 Integration (marker `integration`, real PostgreSQL)

Fixtures create a uniquely named database and role pair per test session through the same provisioning code, migrate it, and drop it afterwards. Covered: every repository; migrations up, down, up; model and schema agreement; each insert-only table refuses UPDATE, DELETE and TRUNCATE for the app role (privilege error) and for the owner role (trigger error); loan and tape guard triggers; audit chain guard; the app role cannot connect to a second bank's database; the full pipeline against the in-process doubles and in-process outside records.

### 21.3 Reliability (marker `integration`)

Using the injected `FaultInjector`, the pipeline is crashed (an exception that unwinds everything, then a new worker instance on the same database) at each of these points, and the final state must equal a clean run on a second database (same loan states, same tokens, same registry locks, same number of distinct idempotency keys, exactly one registry submission per key): after intake commit; mid clean before commit; after each check chunk; after a key holder response before storing tokens; after planning requests; after the registry committed but before the response is stored (using `AFTER_COMMIT` on the double); after the response is stored; after a certificate is stored; between outbox commit and ack. Also: registry returns 503 for a middle batch of a three batch tape (earlier batches applied, later ones sent on retry, no duplicate locks); key holder 2 fails after holder 1 succeeded; outside records down for a whole stage then back; `PIPELINE_MAX_ATTEMPTS` reached then `retry`; the same tape uploaded twice; two workers started on one database (advisory lock: no stage runs twice).

### 21.4 Contract (marker `contract`)

- `onepledge-contracts check` passes.
- Bank agent: routes, methods, status codes and `x-roles` equal `bank_agent.yaml`; live responses of every endpoint validate against the schemas.
- Bank agent clients against the doubles: every request the clients send validates against `key_holder.yaml`, `registry.yaml`, `outside_records.yaml` request schemas.
- Doubles and outside records: live responses of every contract endpoint validate against their YAML.
- Vectors: the bank agent's tokenisation reproduces `tokenization.json`; its verifier accepts the certificate and checkpoint in `signatures.json` and rejects each with one byte changed.
- The 20 loan example (5.9).

### 21.5 Security

Every cell of the permission matrix over HTTP; token tampering (wrong signature, `alg: none`, wrong audience, expired, another bank's token, stale `token_version`); login rate limit and uniform 401; oversize and malformed uploads (each row of the 10.1 table, plus a zip file renamed to csv, a 6 MiB file, 20,001 rows, a 600 character cell, Latin-1 bytes, NUL bytes, an unterminated quote); formula cells; validation errors never echo input (a canary string sent in every field of every body must not appear in any response); the log canary test of 16.5; tampered certificate (each failure reason of 15.2); tampered checkpoints (each failure reason of 15.3, using the double's test controls); secrets never appear in `repr(settings)` or logs.

### 21.6 Architecture tests

In each package, a test parses the source tree with `ast` and asserts the import boundaries of 2.1 and that `os.environ` is read only in the settings module. A repository level test asserts that the string `answer_key` appears in no file under `bank_agent/`, `outside_records/` or `contracts/`, and under `evaluation/src/` only inside `onepledge_evaluation/scorecard/` and `onepledge_evaluation/demo/`. (`datagen/` writes the key and may name it.)

### 21.7 End to end (marker `e2e`)

In `evaluation/tests`: generate the `small` profile with two different seeds, bring up the real stack on free ports with a unique database prefix, replay, score with enforcement, and assert all gates pass for both seeds; then run the scene 9 checks. Runs in `make test`.

---

## 22. Docker (for teammates; cannot be run on the build machine)

- `bank_agent/Dockerfile`, `outside_records/Dockerfile`, `contracts/fakes/Dockerfile`: base `python:3.12-slim`, copy the package, `pip install --no-cache-dir .`, create and switch to a non-root user, `EXPOSE` the port, `HEALTHCHECK` on `/healthz`, entrypoint the console script. No secret in any image layer.
- `docker-compose.layer1.yml`: `postgres:16` (named volume, health check, published on `${PG_PORT}`), a one-shot `provision` service (runs provisioning, migrations, data generation and seeding using the evaluation package image built from the repository root), `registry-fake`, `keyholder-1-fake`, `keyholder-2-fake`, `outside-records`, `bank-harbor`, `bank-northgate`, `bank-calder`. Every value comes from `.env` through `env_file` and `environment` mappings; services start in dependency order with `depends_on: condition: service_healthy` (or `service_completed_successfully` for `provision`). Service names of the doubles end in `-fake`.
- `docker compose -f docker-compose.layer1.yml config` must succeed with the generated `.env`.
- The README states that this compose file is the layer 1 slice and that Vedant's `infra/` will supersede it with the real key holders and registry.

---

## 23. Known limitations (stated plainly)

1. Test doubles, not layer 2. The key holders and registry used here are in-memory doubles. Their state is lost on restart, they have no database, and their matching rules are a draft for Shreyas to accept or change.
2. Title pending window. A loan originated within 30 days of the tape date with no title record yet is accepted if its contract checks out. An invented loan inside that window with a fabricated contract is therefore locked, and layer 1 does not re-check it when the window closes. The scorecard plants and reports this blind spot. A real bank would run a title exception report; that job is not built.
3. A VIN altered by one character is normally stopped inside the bank (check digit, or no title), not by the registry. The registry's field agreement rule only sees such a copy when the title window hides it.
4. Field agreement needs the same lender. Borrower codes are namespaced by lender LEI, so a copy laundered through a different lender with new borrower references matches on the vehicle only. A copy that changes both the VIN and the borrower reference is invisible to the registry.
5. Bucket edges. `AMOUNT_100` and `AMOUNT_1000` are floor buckets; two amounts a few cents apart can fall in different buckets. They are supporting evidence, not triggers on their own.
6. Rule C can flag an honest coincidence (one borrower, two cars, same day, same amount). It is cleared through the case flow as a data error.
7. The OPRF is not verifiable. A holder that returns wrong (but valid) points would make the bank compute wrong codes without noticing. The `oprf` library is small and unaudited. A production system needs a verifiable OPRF with proofs.
8. Key rotation is not implemented. A change of key version fails the affected tape with `KEY_VERSION_MISMATCH`; moving existing locks to a new key is a cross layer protocol that does not exist yet.
9. Tokens are HS256 JWTs with a per bank shared secret, 15 minutes, no refresh and no revocation list beyond `token_version`. There is no multi factor login and no single sign-on.
10. No TLS between services in the demo. A real deployment needs TLS everywhere and mutual authentication between the bank agent and the shared services.
11. The VIN travels in the URL of outside records lookups (the agreed contract paths). Layer 1 keeps paths out of its own logs but cannot control intermediaries.
12. Reads of loan lists are not audited one by one; logins, denied access, lineage and audit log reads, and every change are.
13. The audit chain detects edits and deletions inside the log. It does not detect a truncation of the newest entries unless an older copy of the head hash is held elsewhere. Nothing exports the head hash off the host yet.
14. One worker thread per bank processes tapes of one facility strictly in order. A tape that keeps failing blocks later tapes of that facility until it is FAILED (after 8 attempts) or retried.
15. Cleaning is rule based. It fixes the listed formats only; anything else goes to the review list. There is no screen to correct a rejected row; the lender resends it.
16. The alert explainer is a template. The LLM adapter behind the interface is not built.
17. Outside records are a static snapshot per run and are trusted as ground truth. In reality a contract vault is fed by the lender (which the planted cases exploit on purpose) and title offices lag.
18. All data is synthetic. Distributions are plausible, not calibrated to any real portfolio. The scorecard measures this system on this generator, not real world detection rates.
19. Docker files are written but were not run on the build machine (no Docker daemon). `docker compose config` is the only check done here.

---

## 24. Differences from BUILD_PLAN.md

| # | Plan says | Layer 1 does | Why |
|---|---|---|---|
| 1 | `loans` unique `(facility_id, vin)` | Unique `(facility_id, vin)` among rows that are not RELEASED | A released car can be pledged again (refinance, return) without rewriting history. |
| 2 | A copy with the amount changed is "flagged as likely" | Same wording, but the mechanism is an asset collision (the lock is on the VIN), reported as LIKELY with basis ASSET | One car, one lock is the stronger and simpler rule; EXACT is kept for a verbatim copy. |
| 3 | A copy with one VIN character changed is "flagged as likely" | Usually stopped inside the bank (check digit or title). Flagged LIKELY by the registry only inside the title pending window | That is what the checks honestly do. Stated in the scorecard per sub-type. |
| 4 | Tables as listed | Extra columns and five extra tables (`submission_items`, `registry_request_items`, `certificates`, `login_attempts`, `integrity_runs`) | Lineage, resumability, stored proofs, rate limiting. Each justified in 12.3. |
| 5 | `checkpoints_seen` keyed by `checkpoint_seq` | Surrogate key, unique verified seq | A forged checkpoint with a seen seq must be recordable as evidence. |
| 6 | Contract A lists 9 bank agent endpoints | 26 paths (the 9 plus those in `contracts/README.md` item 10) | Lineage, audit, releases, users, retries, search without VIN in the URL. Additive. |
| 7 | AI: one model call with a fallback | Interface and template fallback only | Not required for any scene; avoids a network dependency. Deferred. |
| 8 | Layer 1 folders are the four packages | Plus `contracts/fakes/` (doubles) and the stack launcher inside `evaluation/` | CONTEXT requires the doubles; the launcher is needed to run without Docker. |
| 9 | Outside records paths carry the VIN | Kept, with a logging ban on paths; a body based lookup is proposed for later | The paths were agreed by the team. |
| 10 | ADMIN role undefined | ADMIN manages users and integrity and cannot read loan data | Segregation of duties. |
| 11 | "pre-commit", "GitHub Actions CI" | pre-commit config and `make ci` only | CI workflows belong to layer 3. |
