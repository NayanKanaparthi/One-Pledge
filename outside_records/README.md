# onepledge-outside-records: title, payment and contract lookups

A small read-only HTTP service that stands in for three independent sources a bank may
query about a pledged vehicle: state title offices, payment deposits and a digital
contract vault (SPEC 6, contract `contracts/outside_records.yaml`). All data is
synthetic and comes from `onepledge-datagen`.

- Three lookups, each behind an `X-API-Key`: `GET /v1/titles/{vin}`,
  `GET /v1/payments?vin=...`, `GET /v1/contracts/{vin}`. `GET /healthz` needs no key.
- The API process connects as `{db}_app`, which has `SELECT` on the four tables and
  nothing else, so it cannot write even if the code tried. `serve` checks this at startup
  with `inspect_role()` (SPEC_DECISIONS 72, 103) and refuses to start, with one sentence
  naming `DATABASE_URL`, the first violation code and the advice that fits it, unless all
  ten points hold: no superuser,
  `CREATEDB`, `CREATEROLE`, `REPLICATION` or `BYPASSRLS`; membership of no role, the
  predefined `pg_` roles included; ownership of nothing in the database; `CONNECT` only on
  the database, `USAGE` only on `public` and nothing on any other schema; on every table of
  `public` no column-level privilege at all (`COLUMN_PRIVILEGE`) and, compared at table
  level, exactly the privileges of the grant matrix, counting grants to the role and to
  `PUBLIC`, with no table outside the matrix; no privilege on any sequence; no role
  setting (`ROLE_HAS_SETTING`), no database setting of `session_replication_role` for
  every role (`DATABASE_HAS_SETTING`, object `database X: session_replication_role`), no
  privilege on a configuration parameter (`PARAMETER_PRIVILEGE`) and a session that fires
  triggers (`TRIGGERS_DISABLED_FOR_SESSION`, only when neither setting explains a session in
  `replica` or `local`); no `SECURITY DEFINER` function and no `EXECUTE` on any
  function outside the system schemas (`SECURITY_DEFINER_FUNCTION`, `FUNCTION_PRIVILEGE`);
  no default privilege for the role or `PUBLIC` (`DEFAULT_PRIVILEGE`); and nothing held
  `WITH GRANT OPTION` (`GRANT_OPTION`). The advice comes from one table: "use the app role
  as provisioned" for an attribute, membership, ownership or extra privilege; "the database
  was not provisioned or migrated correctly; run db migrate as the owner" for a missing
  privilege or a table outside the matrix; "remove the role's settings" for a role setting,
  a parameter privilege or the session; "remove the database setting" for
  `DATABASE_HAS_SETTING`. The grant matrix (`role_check.GRANT_MATRIX`) is the one table the grants
  migration and the check both read. The check reads the role's attributes and
  privileges, never the URL, and does not depend on the search path.
- `serve` sets its own session parameters on every connection, as connection options that
  override role and database defaults: `search_path` `public`, `statement_timeout` 5s,
  `lock_timeout` 2s, `idle_in_transaction_session_timeout` 10s; and refuses every new
  pooled connection whose `session_replication_role` is not `origin`. `DATABASE_URL` and
  `MIGRATION_DATABASE_URL` may not carry query parameters.
- `serve` starts through named checks, in order: settings, database reachable, session
  parameters, schema in place, role inspection, bind probe (SPEC_DECISIONS 104). Each has
  its own sentence, and a statement the database refuses inside one is "The database
  refused a statement of the startup check NAME (SQLSTATE X); ...", never the last resort.
- `db migrate`, `seed` and `clients add` refuse to run when the role of
  `MIGRATION_DATABASE_URL` does not own the database ("is not the owner of the database;
  use the owner role"), and when a table of the schema is owned by another role ("the
  schema was changed outside db migrate"). `db migrate` also refuses a `DB_APP_ROLE` that
  is not `{db}_app` for the database of `MIGRATION_DATABASE_URL`, or that owns it.
- Client keys are stored only as SHA-256 hashes of 256 bit random keys. A request's key is
  hashed and looked up by indexed equality on that hash; nothing is compared in memory.
- A VIN that does not match `^[A-HJ-NPR-Z0-9]{17}$` gets 422 `VALIDATION_ERROR` with
  `details: [{"field": "vin", "issue": "PATTERN_MISMATCH"}]`; the value is never echoed.
  The check digit is not validated (a title office answers for whatever it is asked).
- The access log records method, route template (`/v1/titles/{vin}`), status, duration,
  request id and the client name under the key `client` (null when the request had no
  valid key). It never records the path, the query string or a VIN. uvicorn's own access
  log is off. The log allow-list is the SPEC 16.5 keys this service writes plus `client`
  and `revision` (SPEC_DECISIONS 5, 43.6). The `service` value of every log line is
  `outside_records`, the health value, and every 500 writes exactly one `error` record
  carrying the response's `request_id` (a 503 for a database outage writes one `warning`).
  An `X-Request-ID` that is not a canonical lower case UUID is replaced by a new id, never
  rewritten, and a client that hangs up mid request writes one `info` record
  `client_disconnected` and nothing else (SPEC_DECISIONS 75).
  A repeated `X-API-Key` (or `Authorization`, `X-Test-Control-Key`) is 401; after
  authentication a repeated `Idempotency-Key`, `Content-Type` or `Content-Length` is 422
  `DUPLICATE`, and a repeated `X-Request-ID` is 422 `DUPLICATE` before routing
  (SPEC_DECISIONS 112.8).
  Every record of a 5xx response, the access record included, carries `error_code`,
  `http_status`, `request_id` and `exception_type` (SPEC_DECISIONS 66). Every exception
  while `db migrate` runs (a statement PostgreSQL refuses, a missing privilege, any
  SQLAlchemy or Alembic error) writes `schema_migration_failed` with `exception_type` and
  the `revision` being applied, prints "The migration to REVISION failed and was rolled
  back. Fix the database state named in the log and run db migrate again." and exits 1
  (SPEC_DECISIONS 79); a database that cannot be reached says so instead, and `--debug`
  shows the frames of the failure. Records of libraries (uvicorn, SQLAlchemy, Alembic)
  below WARNING are dropped; above it they keep only `logger`, `level` and
  `exception_type`. `db migrate` writes one `schema_migration_applied` record per applied
  revision.
- No response is a redirect: a path with a trailing slash is an unknown route, 404
  `NOT_FOUND`. A 405 names the route's methods in `Allow`. `/healthz` ignores its query
  string (probes add cache busters).
- Query strings are validated at the edge (SPEC_DECISIONS 22, 23): `limit` and `offset`
  are 1 to 18 ASCII digits inside their bounds (`offset` at most 1,000,000), dates are
  ASCII `YYYY-MM-DD`, a repeated parameter is `DUPLICATE` and an undeclared one is
  reported once as `query` `UNKNOWN_PARAMETER` without its name, on every route except
  `/healthz`. Issues
  come from the one vocabulary: `REQUIRED`, `INVALID_INTEGER`, `OUT_OF_RANGE`,
  `PATTERN_MISMATCH`, `INVALID_DATE`, `DUPLICATE`, `UNKNOWN_PARAMETER`. No input gives a
  500. The order is fixed (SPEC_DECISIONS 66): route and method, then the API key,
  then an unknown parameter (reported alone), then every parameter and path problem together.

## Layout

```
outside_records/
  pyproject.toml  README.md  Dockerfile  alembic.ini
  migrations/
    env.py
    versions/
      0001_schema.py    # tables, checks, indexes
      0002_grants.py    # the grant matrix for the app role
      0003_contract_checks.py  # the contract's rules as check constraints
  src/
    onepledge_outside_records/
      domain/
        records         # VinText, TitleRecord, Payment, Contract, PaymentQuery
        record_rules    # one validator per record type: every rule of the contract
        errors
        text_values     # the one text to integer and date parser, ASCII digits only
      application/
        ports  lookups  administration
      infrastructure/
        settings  logging_setup  csv_source  binding
        db/
          models  engines  provisioning  migrations  repositories
          role_check    # inspect_role, the grant matrix and the advice per code
          role_points   # points 5 (columns) and 7 to 10 of the role check
          session       # the session parameters of every connection, the pooled guard
          failures      # the sentences naming the real cause of a database failure
      interface/
        cli  composition  output
        startup         # the named startup checks of serve
        http/
          app  plumbing  query
  tests/
```

## Commands

```
onepledge-outside db provision          create the roles and the database (needs PG_ADMIN_URL), idempotent
onepledge-outside db migrate            alembic upgrade head as the owner role
onepledge-outside seed --data-dir DIR   replace the three data tables from DIR/outside_records/*.csv
onepledge-outside clients add --name NAME --key-env VAR
                                        add a client, or replace its key, from the key in variable VAR
                                        (NAME: 1 to 64 letters, digits, hyphens or underscores)
onepledge-outside serve                 serve the API on BIND_HOST:PORT as the app role
```

`seed` runs in one transaction as the owner role, prints the row count per table and is
idempotent (the same input gives the same table contents). Before anything is written it
checks every cell of every row of the three files against the contract (SPEC_DECISIONS
109): one validator per record type (`domain.record_rules`) holds every rule of
`outside_records.yaml` (patterns, lengths, enums, minimums, formats) and the bounds of
decision 100 (no control character, dates from 2000-01-01 to 2099-12-31), checked on the
record as the API would return it, and `payment_id`, `contract_id` and the title VIN may
not repeat inside their file. On any problem nothing is loaded: the sentence "The outside
records hold N problems; nothing was loaded." is followed by at most 20 problems, each
`FILE line N, column NAME: PROBLEM` (`titles.csv line 2, column state: must be two upper
case letters`), and the count of the rest; `--json` carries the same list as `problems`
with `problems_total`. Migration 0003 gives the database the same rules as check
constraints, so no writer can store a row that makes the API break its contract.
`clients add` with a key another client already has says so.

Every command exits 0 on
success and 1 with one sentence on stderr when something is wrong (PostgreSQL not
reachable, a connection refused, tables missing, a privilege missing, port in use, a
variable missing, malformed or out of range, a `PG_ADMIN_URL` that is not a superuser, a
data file that is not a regular file inside the directory, such as a FIFO or a link,
refused by name before it is opened, `outside_records/payments.csv is not a regular
file.`, or one over 256 MiB, SPEC_DECISIONS 110), and 2 on a
usage error (an empty `--data-dir` is one). An unexpected error prints `Unexpected error.
Run with --debug for details.`, after one `error` log record with `exception_type` and
`stack`; `--debug` adds the exception type and its frames (file, line, function), never
the exception message, a source line or a local variable. The settings `PORT`,
`BIND_HOST` and `LOG_LEVEL` have the doubles' sentences, word for word.
Every line for a human (results, errors, usage errors, help) is at most 100 columns: a
longer line wraps onto continuation lines indented two more spaces, and `--wide` lifts the
limit for results only. Arguments are exact (SPEC_DECISIONS 121): an abbreviated option or
an option given twice is a usage error, and `--json` takes no value. A usage error prints
the usage text and the product's own sentence on stderr, never the value typed (`argument
--profile must be one of: full, small, example.`), and exits 2; with any argument starting
`--json` it also prints `{"ok": false, "error": {"code": "USAGE", "message": "The command
line is not valid. See the usage text."}}` on stdout. No message ends in a colon
(SPEC_DECISIONS 78).

Every command except `serve` also takes `--json` (stdout holds exactly one JSON document:
`{"ok": true, ...}` with the facts of the text output, or `{"ok": false, "error":
{"code": ..., "message": ...}}` with the code `CONFIGURATION_ERROR`, `DATABASE_UNAVAILABLE`,
`NOT_MIGRATED`, `INVALID_INPUT` or `OPERATION_FAILED`) and `--quiet` (log warnings and
errors only). These commands write their JSON log lines to stderr, so stdout holds only
the result. `serve` writes its log lines to stdout, prints nothing else and refuses
`--json` as a usage error.

Settings are scoped to the command: `serve` parses only `BIND_HOST`, `PORT`,
`DATABASE_URL` and `LOG_LEVEL`, so an owner or superuser URL present in its environment
never reaches the serving process's settings; each administrative command parses only
the variables in the table below.

## Configuration

| Variable | Used by | Meaning |
|---|---|---|
| `DATABASE_URL` | `serve`, `db provision` | `postgresql://{db}_app:PASSWORD@HOST:PORT/{db}`, the read-only app role. |
| `MIGRATION_DATABASE_URL` | `db provision`, `db migrate`, `seed`, `clients add` | `postgresql://{db}_owner:PASSWORD@HOST:PORT/{db}`, the owner role. Never read by `serve`. |
| `DB_APP_ROLE` | `db migrate` | The role that receives `SELECT` on the four tables; must be `{db}_app` for the database of `MIGRATION_DATABASE_URL`. |
| `PG_ADMIN_URL` | `db provision` | Superuser URL. Provisioning only. |
| `PORT` | `serve` | TCP port, 1 to 65535 in ASCII digits (8200 in the local stack: `ONEPLEDGE_PORT_BASE` plus 200). |
| `BIND_HOST` | `serve` | An IPv4 or IPv6 literal of this machine (a name such as `localhost` is refused). Default `127.0.0.1`; `0.0.0.0` in the container. `serve` binds a socket to it before anything else and refuses with one line that names the variable, never its value (SPEC_DECISIONS 73). |
| `LOG_LEVEL` | `serve` | Default `INFO`. |

Every value is trimmed of ASCII space, tab, CR and LF at both ends; a value that still
holds any other whitespace or a control character is refused with one line naming its
variable, the client key of `clients add` included (SPEC_DECISIONS 66).

`db provision` takes the database and role names and passwords from the two role URLs;
the roles must be named `{db}_owner` and `{db}_app`. Database URLs are held as secrets:
they never appear in a settings repr, a log line or an error message.

## Running it by hand

From the repository root, with PostgreSQL on port 55432 and a data set generated by
`make data`. The `opdev` prefix keeps this away from the stack's own `onepledge_outside`.
The superuser and owner URLs are set only for the administrative commands that need them
(inline), so the shell that starts `serve` never has `PG_ADMIN_URL` or
`MIGRATION_DATABASE_URL` exported (SPEC_DECISIONS 53.13).

```
OWNER_PASSWORD=$(python3 -c "import secrets; print(secrets.token_urlsafe(24))")
APP_PASSWORD=$(python3 -c "import secrets; print(secrets.token_urlsafe(24))")
OWNER_URL=postgresql://opdev_outside_owner:$OWNER_PASSWORD@localhost:55432/opdev_outside
APP_URL=postgresql://opdev_outside_app:$APP_PASSWORD@localhost:55432/opdev_outside
HARBOR_OUTSIDE_KEY=opk_$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")

PG_ADMIN_URL=postgresql://postgres@localhost:55432/postgres MIGRATION_DATABASE_URL=$OWNER_URL \
  DATABASE_URL=$APP_URL onepledge-outside db provision
MIGRATION_DATABASE_URL=$OWNER_URL DB_APP_ROLE=opdev_outside_app onepledge-outside db migrate
MIGRATION_DATABASE_URL=$OWNER_URL onepledge-outside seed --data-dir var/data/full-20261117
MIGRATION_DATABASE_URL=$OWNER_URL HARBOR_OUTSIDE_KEY=$HARBOR_OUTSIDE_KEY \
  onepledge-outside clients add --name harbor --key-env HARBOR_OUTSIDE_KEY
DATABASE_URL=$APP_URL PORT=8200 onepledge-outside serve
```

In the full stack, `onepledge-eval` sets these variables from the root `.env`.

## Tests

```
python3 -m pytest outside_records -q
```

The integration tests need the PostgreSQL server at `PG_ADMIN_URL` (default
`postgresql://postgres@localhost:55432/postgres`). Each test session provisions a
uniquely named database and role pair through the same provisioning code, migrates it and
drops it afterwards. The names have the form `optest_<UTC time>_<pid>_<8 hex>_outside`; a
run stopped by SIGTERM starts no further test, tears its fixtures down, exits non-zero and
says so on its last line (SPEC_DECISIONS 126), every session first drops `optest_`
databases and roles more than two hours old, and `make test-sweep` drops all of them. Every
test stops after 120 seconds, and the tests that expect `serve` to refuse after 60.

They cover provisioning twice, migrations up, down and up, agreement of the models with
the migrated schema, seeding and its idempotence, the app role being refused every write,
client keys stored only as hashes, every endpoint against the contract's schemas, ordering
and paging of payments, 401, 404 and 422 without echo, trailing slashes and 405 `Allow` on
every route, `serve` refused for a hand-made violation of each of the ten points
(`test_role_check.py`, column-level grants, role settings, parameter privileges, functions,
default privileges and grant options included), the startup checks and the reproductions
of review 4 (`test_startup.py`), the administrative commands refused for a role that is not
the owner, and log records that hold no VIN, path or query string.
