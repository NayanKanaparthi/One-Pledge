# onepledge-bank-agent: the program that runs inside one bank

The bank agent holds one bank's loan tapes, cleans them, checks every loan against
outside records, and (from WP3 on) tokenises the loans and talks to the shared registry
(SPEC 1, 8 to 13). This package is built in work packages. WP2 provides the domain model,
the complete database with its integrity mechanisms, the first three pipeline stages
(intake, clean, check), the hash chained audit log, the redacting logger and the
`onepledge-bank` commands `db`, `seed-reference` and `worker`.

What a reviewer can check, and where:

- **History cannot be edited, even by the owner.** Nine tables (`loans_raw`,
  `audit_log`, `cleaning_issues`, `submission_items`, `local_checks`, `loan_tokens`,
  `certificates`, `checkpoints_seen`, `integrity_runs`) have statement triggers that
  refuse `UPDATE`, `DELETE` and `TRUNCATE` with SQLSTATE `OP001`. Loan state changes
  outside the table of SPEC 8.3 fail with `OP002`; a loan's identity, and the terms of
  a tokenised loan, are immutable (`OP003`); the facts of an upload and illegal tape
  status changes fail with `OP004`; an audit entry that does not extend the chain fails
  with `OP005` (`migrations/versions/0002_guards.py`). `TRUNCATE loans_raw` by the
  owner fails with `0A000` first, because other tables reference it by foreign key, and
  `TRUNCATE loans_raw CASCADE` fails with `OP001` (SPEC_DECISIONS 24).
- **What the lender sent can be rebuilt byte for byte.** Each upload keeps its BOM
  flag, its verbatim header record and trailing text, and every record's verbatim text
  and cells (SPEC_DECISIONS 25). Intake refuses a file whose stored forms would not
  rebuild it, `row_sha256` is the hash of the record's text and the database checks it,
  and a test rebuilds every tape of the `full` data set from the database alone.
- **The program cannot even try.** The API and the worker connect as `{db}_app`, which
  has `SELECT, INSERT` on those tables (and `login_attempts`), `SELECT, INSERT, UPDATE`
  on six working tables, `SELECT` on reference data, and nothing else: no `UPDATE`,
  `DELETE`, `TRUNCATE` or DDL (`migrations/versions/0003_grants.py`). The worker's
  settings object has no field for an owner or superuser URL, so it cannot hold one.
- **Every change is audited in a hash chain.** Each unit of work appends its audit
  entries just before commit, under one advisory lock, each entry hashing the previous
  one (SPEC 17). A rolled back transaction writes no entry. Administrative acts are
  audited too: `seed-reference` writes `REFERENCE_DATA_SEEDED` and `db migrate` writes
  `SCHEMA_MIGRATED` (SPEC_DECISIONS 30). `tests/bank_testkit/audit_chain.py` recomputes
  the chain independently of the product code.
- **Raw loan values never reach a log.** Logs are JSON lines (on stderr for every
  command of this package, SPEC_DECISIONS 27); a
  `RedactionProcessor` keeps only the keys of SPEC 16.5, refuses unsafe value shapes and
  scrubs VIN shaped text. Events carry ids, codes and counts, never values; exceptions
  are logged as their type and frame locations only. A canary test runs known values
  through every stage and failing path and finds none of them in any record.
- **A crash never corrupts or duplicates.** Each stage commits idempotently; a stage is
  run under a per submission advisory lock; crash tests stop the process at every fault
  point and compare the resumed result with an uninterrupted run on a second database.

## Layout

```
bank_agent/
  pyproject.toml  README.md  alembic.ini
  migrations/          env.py; versions/0001_schema.py (the 18 tables of SPEC 12.1 with
                       named checks and indexes), 0002_guards.py (triggers), 0003_grants.py
  src/onepledge_bank_agent/
    domain/            values (Vin, Money, Lei, ...), enums, errors (one DomainError tree and
                       the fixed message table), tape and loan state machines, alert,
                       entities, tape_file (CSV intake rules), cleaning/ (one module per
                       field family), dispositions, checks (the four local checks),
                       lender_names, access_policy (SPEC 18.2), canonical_json, audit
    application/       ports (repositories, unit of work, clock, ids, log, clients),
                       IngestTape, CleanTape, RunLocalChecks, AdvanceSubmission (stages
                       registered in order), RunPipelineWorker, SeedReferenceData, faults
    infrastructure/    settings, logging, audit (hash chained writer), clock, ids,
                       seed_reference_file, db/ (models, engines, provisioning, migrations,
                       unit of work, advisory locks, one repository per port), http/
                       (base client with timeouts and retries, outside records client)
    interface/         composition (the one composition root), cli/ (commands, output)
  tests/
    unit/  integration/  contract/  bank_testkit/ (fixtures, in-process outside records,
                                                   an independent cleaning and check oracle)
```

Each layer imports only the layers below it; SQL, HTTP and logging libraries appear only
in `infrastructure/`. `tests/unit/test_architecture.py` enforces this and the size and
hygiene limits of SPEC 2.2.

## Commands

```
onepledge-bank db provision               create {db}_owner, {db}_app and the database (needs PG_ADMIN_URL), idempotent
onepledge-bank db migrate                 alembic upgrade head as the owner role; grants go to DB_APP_ROLE
onepledge-bank db status                  current and latest schema revision, read as the app role
onepledge-bank seed-reference --data-dir DIR
                                          upsert this bank's lenders and facilities from DIR/reference.json
onepledge-bank worker [--once]            advance submissions through the registered stages;
                                          --once stops when nothing is runnable, otherwise SIGTERM or SIGINT
```

Every command takes `--json`, `--quiet` and `--debug`.

- stdout carries only the result: aligned text, or with `--json` exactly one JSON
  document, `{"ok": true, ...}` with the facts the text shows, or on failure
  `{"ok": false, "error": {"code": "...", "message": "..."}}` (SPEC_DECISIONS 20).
- stderr carries the JSON log lines (`LOG_LEVEL`, or WARNING with `--quiet`) and, on
  failure, one plain sentence.
- Exit 0 on success, 1 for a failure (a variable missing, PostgreSQL not reachable, the
  schema not at the latest revision, an unusable reference file), 2 for a usage error.
  An unexpected error prints `Unexpected error. Run with --debug for details.`; only
  `--debug` adds the traceback. No output holds a URL with a password or a key.
- `worker` without `--once` runs until stopped and prints no result, so `--json`
  without `--once` is a usage error. The worker refuses a schema that is not at the
  latest revision.
- `db migrate` audits an upgrade that changed the revision (`from_revision`, null on a
  new database, and `to_revision`). A downgrade is a development operation and writes no
  entry; after `downgrade base` the audit table no longer exists.
- `seed-reference` reports, and audits in the same transaction, how many lenders and
  facilities were inserted, updated or unchanged, with the SHA-256 of `reference.json`;
  a run that changes nothing is audited too.

## Pipeline (WP2 stages)

| Stage | Use case | Does |
|---|---|---|
| RECEIVED | `IngestTape` | Authorises the role, validates the whole file (size, UTF-8, CSV shape, header, row count), checks that the verbatim forms rebuild the file, refuses a duplicate file for the same facility and kind, and stores the submission and every raw row in one transaction. A refused upload stores nothing but one audit entry. |
| CLEANED | `CleanTape` | Cleans every row from `header_cells` and `raw_cells` (SPEC 10.4, SPEC_DECISIONS 13 and 25), records each fix or rejection as a cleaning issue, opens or updates loans and records one disposition per row, in one transaction. |
| CHECKED | `RunLocalChecks` | Looks up each accepted loan's title, payments and contracts, evaluates the four local checks (SPEC 11), stores the results per loan and holds failing loans locally, in chunks of `CHECK_CHUNK_LOANS` that each commit. |

`AdvanceSubmission` dispatches to the next registered stage. Tapes of one facility run
in upload order. Failures (SPEC 13.7, SPEC_DECISIONS 26):

| Situation | Handling | Failure code |
|---|---|---|
| Outside records unreachable, timed out, 429 or 5xx after the client's two retries | Stage retried with backoff `min(2^attempts, 60)` seconds | `OUTSIDE_RECORDS_UNAVAILABLE` after `PIPELINE_MAX_ATTEMPTS` |
| 401, 403, 422, any other 4xx except an agreed 404, or an answer outside the contract | FAILED at once | `OUTSIDE_RECORDS_REJECTED` |
| 404 `TITLE_NOT_FOUND` or `CONTRACT_NOT_FOUND` | A check result, not an error | none |
| Any other error | FAILED at once | `INTERNAL_ERROR` |

`TAPE_FAILED` records `dependency` and `http_status` (null when the service was not
reached) for a dependency failure, and for a refusal also `reason`: `UNAUTHENTICATED`,
`FORBIDDEN`, `REQUEST_REFUSED` or `MALFORMED_RESPONSE`. Every failure code except
`KEY_VERSION_MISMATCH` and `CERTIFICATE_INVALID` can be resumed by a retry.

## Configuration

| Variable | Used by | Meaning |
|---|---|---|
| `BANK_CODE` | `seed-reference`, `worker` | `HARBOR`, `NORTHGATE` or `CALDER`. |
| `BANK_NAME` | later packages | The bank's display name. |
| `DATABASE_URL` | `db provision`, `db status`, `worker` | `postgresql://{db}_app:PASSWORD@HOST:PORT/{db}`, the app role. |
| `MIGRATION_DATABASE_URL` | `db provision`, `db migrate`, `seed-reference` | `postgresql://{db}_owner:PASSWORD@HOST:PORT/{db}`, the owner role. Never read by the worker. |
| `DB_APP_ROLE` | `db migrate` | The role that receives the grants (`{db}_app`). |
| `PG_ADMIN_URL` | `db provision` | Superuser URL. Provisioning only. |
| `OUTSIDE_RECORDS_URL`, `OUTSIDE_RECORDS_API_KEY` | `worker` | The outside records service and this bank's key. |
| `LOG_LEVEL` | `worker` | Default `INFO`. |
| `PIPELINE_MAX_ATTEMPTS` | `worker` | Default 8 (1 to 100). |
| `PIPELINE_POLL_SECONDS` | `worker` | Default 1.0. |
| `CHECK_CHUNK_LOANS` | `worker` | Default 100. |
| `HTTP_CONNECT_TIMEOUT_SECONDS`, `HTTP_READ_TIMEOUT_SECONDS` | `worker` | Defaults 2 and 10. |
| `TITLE_PENDING_WINDOW_DAYS`, `FIRST_PAYMENT_GRACE_DAYS`, `PAYMENT_WINDOW_DAYS`, `CONTRACT_DATE_TOLERANCE_DAYS` | `worker` | Check windows of SPEC 11; defaults 30, 45, 90, 3. |

`PORT` has no default (SPEC_DECISIONS 12); it is used by the HTTP API of WP4. Role names
are derived from the two database URLs (SPEC_DECISIONS 17): the roles must be named
`{db}_owner` and `{db}_app`. Database URLs and keys are held as secrets and never appear
in a settings repr, a log line or an error message.

## Running it by hand

From the repository root, with PostgreSQL on port 55432 and the data set of `make data`.
The `opdev` prefix keeps this away from the stack's own databases.

```
export PG_ADMIN_URL=postgresql://postgres@localhost:55432/postgres
export OWNER_PASSWORD=$(python3 -c "import secrets; print(secrets.token_urlsafe(24))")
export APP_PASSWORD=$(python3 -c "import secrets; print(secrets.token_urlsafe(24))")
export MIGRATION_DATABASE_URL=postgresql://opdev_bank_harbor_owner:$OWNER_PASSWORD@localhost:55432/opdev_bank_harbor
export DATABASE_URL=postgresql://opdev_bank_harbor_app:$APP_PASSWORD@localhost:55432/opdev_bank_harbor
export DB_APP_ROLE=opdev_bank_harbor_app BANK_CODE=HARBOR BANK_NAME="Harbor Bank"

onepledge-bank db provision
onepledge-bank db migrate
onepledge-bank db status
onepledge-bank seed-reference --data-dir var/data/full-20261117
```

To see the guards at work:

```
psql "$DATABASE_URL" -c "DELETE FROM audit_log"                  # ERROR: permission denied for table audit_log
psql "$MIGRATION_DATABASE_URL" -c "DELETE FROM audit_log"        # ERROR: table audit_log is insert-only (SQLSTATE OP001)
```

With outside records running (its README) and `OUTSIDE_RECORDS_URL` and
`OUTSIDE_RECORDS_API_KEY` set, `onepledge-bank worker --once` advances every uploaded tape.
Uploads arrive through the HTTP API of WP4; the tests upload through `IngestTape`.

## Tests

```
python3 -m pytest bank_agent -q
python3 -m pytest bank_agent/tests/unit -q --cov=onepledge_bank_agent.domain --cov=onepledge_bank_agent.application --cov-fail-under=85
```

Integration tests need the PostgreSQL server at `PG_ADMIN_URL` (default
`postgresql://postgres@localhost:55432/postgres`). Each test creates a uniquely named
database (`opwp2t_<random>_bank_harbor`) and role pair through the product's own
provisioning and migrations, and drops it afterwards. Data sets come from the
`onepledge-datagen` command; outside records runs in process on its own test database.

- `unit/`: value objects and the VIN check digit, every cleaning rule (with Hypothesis),
  dispositions, the four checks, state machine tables, the access matrix, the audit
  hash rule, redaction, settings, the HTTP client's retries and error mapping, every use
  case against in-memory ports, and the architecture rules.
- `integration/`: the migrated schema (models equal migrations, checks equal enums, up
  down up), every database guard and grant as the owner and the app role, repositories
  and advisory locks under concurrency, the pipeline on S01, S04, S09 and S10 compared
  with an independent computation, crash and outage recovery compared with a clean run,
  the command line, and the log canary.
- `contract/`: the committed 20 loan example against its expected file; every request
  of the outside records client against `outside_records.yaml`; `AccessPolicy` against
  the `x-roles` of `bank_agent.yaml`; error codes and enumerations against the YAML.

## Additions to SPEC 12.1

Everything SPEC 12.1 and SPEC_DECISIONS require is in the schema; these are the
constraints and indexes added beyond them, each with its reason (SPEC_DECISIONS 29).
None rejects a state SPEC allows, and `tests/integration/test_schema.py` checks that the
models equal the migrated schema, so every one of them is covered by the drift test.

Required by SPEC_DECISIONS 25 (not additions, listed so the table is complete):
`tape_submissions.has_bom`, `header_text`, `header_cells`, `trailer_text`;
`loans_raw.raw_text`, `raw_cells` with `ck_loans_raw_raw_cells` (an array); the four
tape columns are immutable under the tape guard. Required by SPEC_DECISIONS 26 and 30:
`OUTSIDE_RECORDS_REJECTED` in `ck_tape_submissions_failure_code`, and
`REFERENCE_DATA_SEEDED`, `SCHEMA_MIGRATED`, `REFERENCE_DATA`, `SCHEMA` in the audit
action and object type checks.

| Table | Constraint or index | Reason |
|---|---|---|
| tape_submissions, loans_raw, loan_tokens, registry_requests, certificates, checkpoints_seen, audit_log, login_attempts | `ck_<table>_<column>` on every hash or token column (`file_sha256`, `rows_sha256`, `row_sha256`, `token`, `idempotency_key`, `body_hash`, `head_hash`, `prev_hash`, `entry_hash`, `email_sha256`): `^[0-9a-f]{64}$` | A digest column holds exactly a lower case SHA-256 hex digest, so a truncated or upper case value cannot be compared wrongly. |
| loans_raw | `ck_loans_raw_row_sha256_text`: `row_sha256 = sha256(raw_text)` | The row hash is a fact of the row, enforced where it is stored, not only at intake. |
| tape_submissions | `ck_tape_submissions_header_cells`: an array | The header is an ordered list of cells (SPEC_DECISIONS 25 rule 4). |
| loans_raw, local_checks, registry_requests, certificates, audit_log, integrity_runs | `ck_<table>_<column>`: `jsonb_typeof = 'object'` on `raw_payload`, `evidence`, `response_body` (or null), `body`, `detail`, `report` | Readers index these documents by key; a scalar or array would break them silently. |
| tape_submissions | `ck_tape_submissions_resume_status`, `ck_tape_submissions_failed_stage`, `ck_tape_submissions_failure_code`: value lists | SPEC 12.1 names the columns; the lists are the statuses, stages and failure codes of SPEC 8.3 and 13.7. |
| tape_submissions | `ck_tape_submissions_failed_has_stage`: `(status = 'FAILED') = (failed_stage IS NOT NULL)` | The pair of the two SPEC checks for the third failure field. |
| tape_submissions | `ck_tape_submissions_attempts`: `>= 0` | A count of attempts. |
| tape_submissions | `ix_tape_submissions_facility_uploaded_at` | The worker's "one facility's tapes in upload order" query. |
| facilities | `ck_facilities_code`: `^[A-Z0-9-]{3,40}$` | The stable human key used by the manifest and seeding. |
| users | `ck_users_token_version`: `>= 0` | A counter. |
| loans | `ck_loans_lender_loan_number`: `^[A-Za-z0-9][A-Za-z0-9_/-]{0,39}$` | The shape the cleaning rules accept (SPEC 10.4). |
| cleaning_issues | `ck_cleaning_issues_field`: `row` and the six canonical fields | The field names cleaning writes (SPEC 10.4). |
| submission_items | `ck_submission_items_row_number`: `>= 1` | Same rule as `loans_raw.row_number`. |
| local_checks | `ck_local_checks_combination`: the allowed (check type, result, reason) triples | Every answer names a rule that can produce it (SPEC 11.1). |
| registry_requests | `ck_registry_requests_attempts`, `ck_registry_requests_batch_index`: `>= 0`; `ck_registry_requests_key_version`: `^[1-9][0-9]*\.[1-9][0-9]*$` | Counters, and the key version format of `loan_tokens`. |
| registry_request_items | `ck_registry_request_items_position`: `>= 0`; value lists on `match_kind`, `match_basis` (or null); `ck_registry_request_items_kinds_matched`: a subset of the eight token kinds | The values the registry contract defines. |
| certificates | `ck_certificates_as_of_seq`: `>= 1`; `ck_certificates_kind`: the three kinds | A sequence number and the tape kinds. |
| checkpoints_seen | `ck_checkpoints_seen_checkpoint_seq`: `>= 1`; `ck_checkpoints_seen_last_event_seq`: `>= 0` | Sequence numbers. |
| checkpoints_seen | `ck_checkpoints_seen_verified`: `verified = (failure_reason IS NULL)` | The same rule SPEC 12.1 states for certificates; a checkpoint is stored once, after verification (SPEC 15.3). |
| alerts | `ck_alerts_kinds_matched`, `ck_alerts_kinds_not_matched`: subsets of the eight token kinds | The values the registry contract defines. |
| alerts | `ix_alerts_loan_id` | Lineage and loan pages list a loan's alerts (SPEC 14). |
| audit_log | `ck_audit_log_seq`: `>= 1`; `ck_audit_log_actor_user`: `(actor_type = 'USER') = (actor_user_id IS NOT NULL)`; `ck_audit_log_request_id`: at most 64 characters; value lists on `action` and `object_type` | The chain starts at 1; a user entry names its user and a system entry names none; request ids are bounded; actions and object types are those of SPEC 17.2 and SPEC_DECISIONS 30. |

## Decisions recorded here

- A blank line before the header record is kept in `header_text`, so the
  reconstruction of SPEC_DECISIONS 25 rule 1 also holds for such a file; re-parsing
  `header_text` with blank lines dropped yields `header_cells`.
- `TAPE_FAILED` for an outage that reached the attempt limit records `dependency` and
  `http_status` but no `reason`: the closed set of reasons describes refusals.
- `details[].issue` codes raised by the domain come from the shared vocabulary of
  SPEC_DECISIONS 22 (a test reads them from the source). The duplicate upload detail
  carries the existing submission id in `issue`, as `contracts/examples` shows.
