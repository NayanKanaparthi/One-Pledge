# onepledge-bank-agent: the program that runs inside one bank

The bank agent holds one bank's loan tapes, cleans them, checks every loan against
outside records, tokenises the loans through two key holders and talks to the shared
registry (SPEC 1, 8 to 15). This package is built in work packages. WP2 provides the
domain model, the complete database with its integrity mechanisms, the first three
pipeline stages (intake, clean, check), the hash chained audit log, the redacting logger
and the `onepledge-bank` commands `db`, `seed-reference` and `worker`. WP3 adds the rest
of the pipeline (tokenise, submit, certify), the registry outbox (alerts, case updates,
checkpoints), review, resubmission and release, checkpoint and certificate verification,
deep verification, the integrity report, and the commands `registry sync`, `integrity
report` and `integrity verify`.

What a reviewer can check, and where:

- **History cannot be edited, even by the owner.** Nine tables (`loans_raw`,
  `audit_log`, `cleaning_issues`, `submission_items`, `local_checks`, `loan_tokens`,
  `certificates`, `checkpoints_seen`, `integrity_runs`) have statement triggers that
  refuse `UPDATE`, `DELETE` and `TRUNCATE` with SQLSTATE `OP001`. Loan state changes
  outside the table of SPEC 8.4 fail with `OP002`; a loan's identity, and the terms of
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
  point of SPEC 21.3 and compare the resumed result (loan states, tokens, registry locks,
  distinct idempotency keys, one registry submission per key) with an uninterrupted run
  on a second database and a second registry (`tests/integration/test_reliability.py`).
- **Nobody sees a loan value but the bank.** The key holders see only blinded points
  (`infrastructure/crypto/blinder.py` is the only module importing `oprf`); the registry
  sees only the eight scrambled codes of each loan; alerts are explained from the
  registry's match facts alone (`AlertFacts` has no loan value).
- **Others can check us without trusting us, and we check them.** Every certificate and
  checkpoint is verified with the keys of `REGISTRY_PUBLIC_KEYS` only, stored with its
  verdict, and failures are never removed. One failed checkpoint halts every further
  submission (`HALT_ON_REGISTRY_INTEGRITY_FAILURE`); turning the rule off is audited at
  every start. Deep verification recomputes the audit chain, every raw row and file
  hash, every certificate and checkpoint signature and the registry's whole hash chain
  from seq 1, so it finds an edit by a superuser and a history rewrite before the
  latest checkpoint, and says where.

## Layout

```
bank_agent/
  pyproject.toml  README.md  alembic.ini
  migrations/          env.py; versions/0001_schema.py (the 18 tables of SPEC 12.1 with
                       named checks and indexes), 0002_guards.py (triggers), 0003_grants.py,
                       0004_checkpoint_body.py (the signed checkpoint body)
  src/onepledge_bank_agent/
    domain/            values (Vin, Money, Lei, ...), enums, errors (one DomainError tree and
                       the fixed message table), tape and loan state machines, alert,
                       entities, tape_file (CSV intake rules), cleaning/ (one module per
                       field family), dispositions, checks (the four local checks),
                       lender_names, access_policy (SPEC 18.2), canonical_json, audit;
                       tokenization (token inputs and the token rule), idempotency,
                       explanation (TemplateAlertExplainer), registry_records,
                       registry_results (result application rules), certificate_rules,
                       checkpoint_rules, signatures (the SignatureVerifier port),
                       raw_evidence (deep verification of raw rows), integrity (report)
    application/       ports, evidence_ports, registry_ports (repositories, unit of work,
                       clock, ids, log, clients), IngestTape, CleanTape, RunLocalChecks,
                       TokenizeLoans, SubmitStage (halt rule, SubmitPledge, ReleaseLoans,
                       request planning, RegistrySender), CollectCertificates,
                       VerifyCertificate, SyncRegistryOutbox (alert and case handlers),
                       VerifyCheckpoint, ReviewAlert, ResubmitLoan, CreateReleaseInstruction,
                       RetryTape, RunDeepVerification, GetIntegrityReport,
                       RecordStartupConfiguration, AdvanceSubmission (stages registered in
                       order), RunPipelineWorker, SeedReferenceData, faults
    infrastructure/    settings, logging, audit (hash chained writer), clock, ids,
                       seed_reference_file, crypto/ (blinder: the only oprf importer;
                       signatures: Ed25519), db/ (models, engines, provisioning, migrations,
                       unit of work, advisory locks, database control inspector, one
                       repository per port), http/ (base client with timeouts and retries,
                       outside records, key holder and registry clients, strict answers)
    interface/         composition (the one composition root), cli/ (commands, output)
  tests/
    unit/  integration/  contract/  bank_testkit/ (fixtures, in-process outside records,
                                                   the registry and key holder doubles in
                                                   process, three banks, an independent
                                                   cleaning and check oracle)
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
onepledge-bank worker [--once]            advance submissions through the five stages and sync the registry
                                          outbox every OUTBOX_POLL_SECONDS; --once stops when nothing is
                                          runnable, otherwise SIGTERM or SIGINT
onepledge-bank registry sync              pull and process the pending registry messages now
onepledge-bank integrity report           the live integrity report (SPEC 15.5)
onepledge-bank integrity verify           deep verification (SPEC 15.6): stored, audited and printed
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
- SIGTERM or SIGINT stops the worker after the unit it is in, not at the end of the
  stage (SPEC_DECISIONS 51): after the current check chunk, tokenise chunk, registry
  request with its stored response, certificate or outbox message, nothing further is
  committed, the advisory lock is released and the process exits 0. The submission keeps
  its status and the stage resumes from that point on the next start. The clean stage is
  one transaction and runs to its commit; an HTTP call in flight finishes or times out,
  so shutdown takes at most one unit plus `HTTP_READ_TIMEOUT_SECONDS`.
- `db migrate` audits an upgrade that changed the revision (`from_revision`, null on a
  new database, and `to_revision`). A downgrade is a development operation and writes no
  entry; after `downgrade base` the audit table no longer exists.
- `seed-reference` reports, and audits in the same transaction, how many lenders and
  facilities were inserted, updated or unchanged, with the SHA-256 of `reference.json`;
  a run that changes nothing is audited too.
- `worker` writes `HALT_RULE_DISABLED` at every start while
  `HALT_ON_REGISTRY_INTEGRITY_FAILURE` is false.
- `registry sync` prints the messages processed, alerts created, case updates applied
  and checkpoints verified or failed, and is audited as `REGISTRY_SYNC_REQUESTED` with
  `via` cli. When another sync holds the bank's outbox lock it does nothing and says so.
- `integrity report` and `integrity verify` print the five sections, their facts and
  every failure; with `--json` the document is the `IntegrityReport` of
  `bank_agent.yaml` plus `"ok": true`. `integrity verify` needs the registry: when it
  cannot be reached it prints `DEPENDENCY_UNAVAILABLE` and stores nothing.
- A transient dependency failure prints `DEPENDENCY_UNAVAILABLE`; a refusal or an
  answer outside the contract prints `DEPENDENCY_REFUSED`.

## Pipeline

| Stage | Use case | Does |
|---|---|---|
| RECEIVED | `IngestTape` | Authorises the role, validates the whole file (size, UTF-8, CSV shape, header, row count), checks that the verbatim forms rebuild the file, refuses a duplicate file for the same facility and kind, and stores the submission and every raw row in one transaction. A refused upload stores nothing but one audit entry. |
| CLEANED | `CleanTape` | Cleans every row from `header_cells` and `raw_cells` (SPEC 10.4, SPEC_DECISIONS 13 and 25), records each fix or rejection as a cleaning issue, opens or updates loans and records one disposition per row, in one transaction. |
| CHECKED | `RunLocalChecks` | Looks up each accepted loan's title, payments and contracts, evaluates the four local checks (SPEC 11), stores the results per loan and holds failing loans locally, in chunks of `CHECK_CHUNK_LOANS` that each commit. |
| TOKENIZED | `TokenizeLoans` | For PENDING loans without their eight tokens, in chunks of `TOKENIZE_CHUNK_LOANS`: blinds the eight inputs of SPEC 9.2, sends them to key holder 1 and its answer to key holder 2, unblinds, derives the tokens and stores them with the key version pair, one transaction per chunk. Both holders must answer for a chunk to be stored; a holder that changes key version mid submission fails it with `KEY_VERSION_MISMATCH`. |
| SUBMITTED | `SubmitStage` | Checks the halt rule, then plans (once) one registry request per `SUBMIT_BATCH_ITEMS` eligible loans with its idempotency key (SPEC 13.4), and sends every unanswered request in batch order. Each send is counted before it leaves (the client's own retries included); each answer is checked against what was sent and applied in one transaction (SPEC 13.5). RELEASE submissions send only asset codes. |
| COMPLETED | `CollectCertificates` | Fetches and verifies the certificate of every answered request (SPEC 15.2) and stores it either way; any failure ends the tape FAILED with `CERTIFICATE_INVALID` (loan states are kept). Then one outbox sync. |

Outside the stages: `SyncRegistryOutbox` processes ALERT, CASE_UPDATE and CHECKPOINT
messages in position order, one transaction each, then acknowledges (SPEC 14.1);
`VerifyCheckpoint` applies SPEC 15.3 and stores every checkpoint with its verdict;
`ReviewAlert`, `ResubmitLoan`, `CreateReleaseInstruction` and `RetryTape` are the
actions of SPEC 13.8, 14.5 and 14.6 (their HTTP routes come with WP4);
`RunDeepVerification` and `GetIntegrityReport` are SPEC 15.5 and 15.6.

`AdvanceSubmission` dispatches to the next registered stage. Tapes of one facility run
in upload order. Failures (SPEC 13.7, SPEC_DECISIONS 26):

| Situation | Handling | Failure code |
|---|---|---|
| Outside records unreachable, timed out, 429 or 5xx after the client's two retries | Stage retried with backoff `min(2^attempts, 60)` seconds | `OUTSIDE_RECORDS_UNAVAILABLE` after `PIPELINE_MAX_ATTEMPTS` |
| 401, 403, 422, any other 4xx except an agreed 404, or an answer outside the contract | FAILED at once | `OUTSIDE_RECORDS_REJECTED` |
| 404 `TITLE_NOT_FOUND` or `CONTRACT_NOT_FOUND` | A check result, not an error | none |
| A key holder unreachable, 429 or 5xx | Stage retried with backoff; nothing of the chunk is stored | `KEY_HOLDER_UNAVAILABLE` after `PIPELINE_MAX_ATTEMPTS` |
| A key holder refusal (any 4xx) or an answer outside the contract (wrong count, an element that is not a point, a float) | FAILED at once | `KEY_HOLDER_REJECTED` |
| The key version pair changed within a submission, or the registry answered 409 `KEY_VERSION_MISMATCH` | FAILED at once, not retryable | `KEY_VERSION_MISMATCH` |
| The registry unreachable, 429 or 5xx | Stage retried; answered batches keep their results, the rest are sent again with the same key | `REGISTRY_UNAVAILABLE` after `PIPELINE_MAX_ATTEMPTS` |
| A registry refusal or an answer that does not match what was sent | FAILED at once | `REGISTRY_REJECTED_REQUEST` |
| A received checkpoint failed verification and the halt rule is on | FAILED before anything is sent | `REGISTRY_INTEGRITY_BROKEN` |
| A certificate failed verification | FAILED, not retryable | `CERTIFICATE_INVALID` |
| Any other error | FAILED at once | `INTERNAL_ERROR` |

`TAPE_FAILED` records `dependency` (`outside_records`, `key_holder_1`, `key_holder_2` or
`registry`) and `http_status` (null when the service was not reached) for a dependency
failure, and for a refusal also `reason`: `UNAUTHENTICATED`, `FORBIDDEN`,
`REQUEST_REFUSED` or `MALFORMED_RESPONSE`. Every failure code except
`KEY_VERSION_MISMATCH` and `CERTIFICATE_INVALID` can be resumed by a retry
(SPEC_DECISIONS 33).

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
| `REGISTRY_URL`, `REGISTRY_API_KEY`, `REGISTRY_MEMBER_ID` | `worker`, `registry sync`, `integrity verify` | The registry, this bank's key and its member id. |
| `REGISTRY_PUBLIC_KEYS` | `worker`, `registry sync`, `integrity verify` | JSON object of signing key id to base64 Ed25519 public key: the only trust anchors (SPEC 15.1). The published test vector key is refused. |
| `KEY_HOLDER_1_URL`, `KEY_HOLDER_1_API_KEY`, `KEY_HOLDER_2_URL`, `KEY_HOLDER_2_API_KEY` | `worker` | The two key holders and this bank's keys. |
| `HALT_ON_REGISTRY_INTEGRITY_FAILURE` | `worker` | Default true (SPEC 15.4). False is audited as `HALT_RULE_DISABLED` at every start. |
| `OUTBOX_POLL_SECONDS` | `worker` | Default 2. |
| `TOKENIZE_CHUNK_LOANS` | `worker` | Default 100 (1 to 125: at most 1,000 elements per key holder request). |
| `SUBMIT_BATCH_ITEMS` | `worker` | Default 500 (1 to 500, the contract maximum). |
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
The `opdev` prefix keeps this away from the stack's own databases. The administrative
commands get the superuser and owner URLs in a subshell only (SPEC_DECISIONS 53.13): the
shell that later starts `worker` never has `PG_ADMIN_URL` or `MIGRATION_DATABASE_URL`
exported, and `worker` refuses to start as the owner anyway.

```
OWNER_PASSWORD=$(python3 -c "import secrets; print(secrets.token_urlsafe(24))")
APP_PASSWORD=$(python3 -c "import secrets; print(secrets.token_urlsafe(24))")
OWNER_URL=postgresql://opdev_bank_harbor_owner:$OWNER_PASSWORD@localhost:55432/opdev_bank_harbor
export DATABASE_URL=postgresql://opdev_bank_harbor_app:$APP_PASSWORD@localhost:55432/opdev_bank_harbor
export BANK_CODE=HARBOR BANK_NAME="Harbor Bank"

(
  export PG_ADMIN_URL=postgresql://postgres@localhost:55432/postgres
  export MIGRATION_DATABASE_URL=$OWNER_URL DB_APP_ROLE=opdev_bank_harbor_app
  onepledge-bank db provision
  onepledge-bank db migrate
  onepledge-bank seed-reference --data-dir var/data/full-20261117
)
onepledge-bank db status
```

`OWNER_PASSWORD`, `APP_PASSWORD` and `OWNER_URL` are shell variables, not exported, so no
child process sees them unless a subshell exports them as above.

To see the guards at work:

```
psql "$DATABASE_URL" -c "DELETE FROM audit_log"      # ERROR: permission denied for table audit_log
psql "$OWNER_URL" -c "DELETE FROM audit_log"         # ERROR: table audit_log is insert-only (SQLSTATE OP001)
```

With outside records, the registry double and the two key holder doubles running (their
READMEs) and the variables above set, `onepledge-bank worker --once` advances every
uploaded tape to COMPLETED, then `onepledge-bank integrity verify` shows what the bank
verified. Uploads arrive through the HTTP API of WP4; the tests upload through
`IngestTape`.

## Tests

```
python3 -m pytest bank_agent -q
python3 -m pytest bank_agent/tests/integration -k "crash or resume or fault" -v
python3 -m pytest bank_agent/tests -k "vector or oprf or certificate or checkpoint" -q
python3 -m pytest bank_agent/tests/unit bank_agent/tests/integration -q --cov=onepledge_bank_agent.domain --cov=onepledge_bank_agent.application --cov-fail-under=85
```

Integration tests need the PostgreSQL server at `PG_ADMIN_URL` (default
`postgresql://postgres@localhost:55432/postgres`). Each test creates a uniquely named
database (`opwp2t_<random>_bank_harbor`) and role pair through the product's own
provisioning and migrations, and drops it afterwards. Data sets come from the
`onepledge-datagen` command; outside records runs in process on its own test database;
the registry and both key holders are the real doubles of `contracts/fakes` (real OPRF,
real Ed25519, the real registry rules) reached in process through the bank's own HTTP
clients, with one member per bank. Nothing mocks the cryptography or the registry.

- `unit/`: value objects and the VIN check digit, every cleaning rule (with Hypothesis),
  dispositions, the four checks, state machine tables, the access matrix, the audit
  hash rule, redaction, settings, the HTTP client's retries and error mapping, every use
  case against in-memory ports, and the architecture rules.
- `unit/` (WP3): token inputs and the tokenisation vector, the two-holder OPRF equal to
  direct evaluation and independent of the blind (Hypothesis), idempotency keys
  (Hypothesis), the explainer for every role, kind and basis, every certificate and
  checkpoint reason with real signatures, result application, the key holder and
  registry clients over scripted transports.
- `integration/`: the migrated schema (models equal migrations, checks equal enums, up
  down up), every database guard and grant as the owner and the app role, repositories
  and advisory locks under concurrency, the pipeline on S01, S04, S09 and S10 compared
  with an independent computation, the command line, and:
  `test_reliability.py` (every crash point of SPEC 21.3, a lost answer after the
  registry committed, a 503 on the middle of three batches, key holder 2 failing after
  key holder 1, outages to the attempt limit then retry, a duplicate tape, two workers);
  `test_three_banks.py` (every step of the `small` data set for three banks, checked
  without the answer key, then every registry tamper mode); `test_proofs.py` (all four
  certificate reasons, all six checkpoint reasons, the halt rule); `test_integrity.py`
  (a superuser's edits found by deep verification, controls switched off);
  `test_alert_cases.py`, `test_registry_flow.py`, `test_registry_cli.py`; and the log
  canary over the whole pipeline, which also scans every body the registry and key
  holders received, every audit detail and every alert text.
- `contract/`: the committed 20 loan example against its expected file; every request
  of the outside records, key holder and registry clients against their YAML; the
  tokenisation and signature vectors; `AccessPolicy` against the `x-roles` of
  `bank_agent.yaml`; error codes and enumerations against the YAML.

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
| checkpoints_seen | `body jsonb NOT NULL` with `ck_checkpoints_seen_body` (an object), migration 0004 | The signed body exactly as received. SPEC 12.1 stores `signed_at` as a timestamp, but the signature covers its text; deep verification (SPEC 15.6 step 4) re-verifies every stored signature, which needs the exact signed bytes. |
| audit_log | `ck_audit_log_seq`: `>= 1`; `ck_audit_log_actor_user`: `(actor_type = 'USER') = (actor_user_id IS NOT NULL)`; `ck_audit_log_request_id`: at most 64 characters; value lists on `action` and `object_type` | The chain starts at 1; a user entry names its user and a system entry names none; request ids are bounded; actions and object types are those of SPEC 17.2 and SPEC_DECISIONS 30. |

## Decisions recorded here

- `registry_requests.attempts` counts every POST, including the client's own two
  retries: the count is written in its own transaction before each attempt leaves
  (SPEC 13.4 "increment attempts ... then POST"), so a lost answer replayed by the
  client shows attempts 2 and status 200.
- A deep verification finding (SPEC 15.6 step 4) is reported against the stored
  verified checkpoint that covers the bad seq (the first whose `last_event_seq` is at
  least the seq), because a `CheckpointFailure` names a checkpoint.
- The halt rule is checked by the submit stage and also by `ResubmitLoan`, before it
  plans or sends anything (SPEC 15.4 names only the submit stage; a resubmission is a
  submission too).
- An outbox message of a known type whose payload is outside the contract is a protocol
  error: it is not processed, not acknowledged, and the sync ends quietly, so it is
  retried. An unknown type is acknowledged and audited as `REGISTRY_MESSAGE_IGNORED`.
- The key version pair of a submission is the one of the tokens already stored for its
  eligible loans, so a resumed tokenise stage detects a holder that changed version.

- A blank line before the header record is kept in `header_text`, so the
  reconstruction of SPEC_DECISIONS 25 rule 1 also holds for such a file; re-parsing
  `header_text` with blank lines dropped yields `header_cells`.
- `TAPE_FAILED` for an outage that reached the attempt limit records `dependency` and
  `http_status` but no `reason`: the closed set of reasons describes refusals.
- `details[].issue` codes raised by the domain come from the shared vocabulary of
  SPEC_DECISIONS 22 (a test reads them from the source). The duplicate upload detail
  carries the existing submission id in `issue`, as `contracts/examples` shows.
