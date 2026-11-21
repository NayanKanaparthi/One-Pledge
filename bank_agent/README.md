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
  with `OP005` (`migrations/versions/0002_guards.py`).
- **The program cannot even try.** The API and the worker connect as `{db}_app`, which
  has `SELECT, INSERT` on those tables (and `login_attempts`), `SELECT, INSERT, UPDATE`
  on six working tables, `SELECT` on reference data, and nothing else: no `UPDATE`,
  `DELETE`, `TRUNCATE` or DDL (`migrations/versions/0003_grants.py`).
- **Every change is audited in a hash chain.** Each unit of work appends its audit
  entries just before commit, under one advisory lock, each entry hashing the previous
  one (SPEC 17). A rolled back transaction writes no entry. `tests/bank_testkit/audit_chain.py`
  recomputes the chain independently of the product code.
- **Raw loan values never reach a log.** Logs are JSON lines on stdout; a
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

Every command prints aligned text, or JSON with `--json`. It exits 0 on success, 1 with
one sentence on stderr for an expected failure (a variable missing, PostgreSQL not
reachable, the schema not at the latest revision), and 2 for a usage error. It never
prints a traceback, a URL with a password or a key. The worker refuses to start against
a schema that is not at the latest revision.

## Pipeline (WP2 stages)

| Stage | Use case | Does |
|---|---|---|
| RECEIVED | `IngestTape` | Authorises the role, validates the whole file (size, UTF-8, CSV shape, header, row count), refuses a duplicate file for the same facility and kind, and stores the submission and every raw row exactly as sent in one transaction. A refused upload stores nothing but one audit entry. |
| CLEANED | `CleanTape` | Cleans every row (SPEC 10.2 and 10.3), records each fix or rejection as a cleaning issue, opens or updates loans and records one disposition per row, in one transaction. |
| CHECKED | `RunLocalChecks` | Looks up each accepted loan's title, payments and contracts, evaluates the four local checks (SPEC 11), stores the results per loan and holds failing loans locally, in chunks of `CHECK_CHUNK_LOANS` that each commit. |

`AdvanceSubmission` dispatches to the next registered stage. A dependency outage is
retried with backoff (`min(2^attempts, 60)` seconds) up to `PIPELINE_MAX_ATTEMPTS`, then
the tape is FAILED with the dependency's code and can be resumed; any other error fails
the tape with `INTERNAL_ERROR` (SPEC 13.7). Tapes of one facility run in upload order.

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

## Decisions and limits recorded here

- `raw_payload` is `jsonb`, which does not keep key order, and SPEC 12.1 has no header
  column. The "first cell" rule for non data rows uses the loan number cell (the VIN
  cell when the tape has no loan number column); column count checks use the union of
  stored keys. A file in which every row lacks the same optional trailing column cannot
  be told apart from a file without that column.
- PostgreSQL checks foreign keys before triggers, so `TRUNCATE loans_raw` by the owner
  fails with SQLSTATE `0A000` (referenced by `cleaning_issues` and `submission_items`)
  rather than `OP001`. Either way the rows stay; the test records this.
- `seed-reference` writes no audit entry because SPEC 17 has no action for it.
- A refusal by outside records (401, 422) or an answer outside its contract fails the
  tape at once with `OUTSIDE_RECORDS_UNAVAILABLE`, the only outside records code of the
  failure code list; the log record names the precise error code.
