# onepledge-key-holder: one of the two OPRF key holders

Layer 2 of OnePledge (`docs/layer2/SPEC.md` section 4, contract `contracts/key_holder.yaml`).
Two processes run this one package. `HOLDER_ID=kh1` is holder 1. By default it listens on port 8301
and is the bank's `KEY_HOLDER_1_URL`. `HOLDER_ID=kh2` is holder 2. By default it listens on 8302 and
is `KEY_HOLDER_2_URL`. Each holder has its own secret scalar and its own PostgreSQL 16 database.

A holder never sees a loan value. The bank blinds each value into a ristretto255 point, sends the
points to holder 1, sends holder 1's answer unchanged to holder 2, and unblinds holder 2's answer
itself. Each holder only multiplies the points it is given by its own scalar.

## The API

`POST /v1/evaluate` needs `X-API-Key`. The body is `request_id`, an optional or null `key_version`,
and 1 to 1000 `elements`, each the standard base64 (44 characters) of a 32 byte point. The answer
holds `request_id` (echoed), `holder_id`, `key_version` (the one version used for every element) and
`elements`, where `elements[i]` is the scalar times request `elements[i]`.

| Case | Answer |
|---|---|
| Missing, unknown, switched off or repeated `X-API-Key` | 401 `UNAUTHENTICATED` |
| Body over 1 MiB | 413 `PAYLOAD_TOO_LARGE` |
| Body outside the schema, 1001 elements included (`elements` `TOO_MANY_ITEMS`) | 422 `VALIDATION_ERROR` |
| Any element that is the identity, is not canonical base64, is not a point, or evaluates to all zeros | 422 `INVALID_ELEMENT`. `details` name the indexes (`elements[3]` `NOT_A_VALID_POINT`), never a value. Nothing is returned or stored |
| A pinned `key_version` other than the active one | 409 `KEY_VERSION_UNAVAILABLE` |
| The member's daily element cap would be passed | 429 `RATE_LIMITED` with `Retry-After`, the seconds to the next UTC midnight, at least 1. The whole request is refused |
| The holder's database does not answer | 503 `SERVICE_UNAVAILABLE`. Nothing is returned that was not recorded |

The checks run in this order: API key, body size, `Content-Type`, JSON, unknown fields, fields,
key version, every element, the cap, the log row. A refused request leaves no row. Evaluation is a
pure function of the key version and the element, so repeating a request returns the same elements;
the `request_id` is recorded and is not an idempotency store (`SPEC_DECISIONS.md` decision 2).

`GET /healthz` needs no key. It returns exactly the contract's `Health` fields. `test_double` is
absent, because this is not the layer 1 double (`SPEC_DECISIONS.md` decision 11):

```
{"status":"ok","service":"key_holder","holder_id":"kh1","active_key_version":1}
```

Every error body is the contract's `ErrorResponse`, also for an unknown route (404 `NOT_FOUND`) and
a wrong method (405 `METHOD_NOT_ALLOWED` with `Allow`). `X-Request-ID` is echoed when it is a lower
case UUID and replaced by a new one otherwise. No answer is a redirect or is compressed. A path with
a trailing slash is an unknown route. No generated documentation route and no `/_test/` route is
served.

## What is stored

| Table | Columns | Written by |
|---|---|---|
| `key_versions` | `key_version`, `status` (`ACTIVE` or `RETIRED_READABLE`), `created_at` | `db migrate` |
| `api_credentials` | `credential_id`, `member_id`, `key_sha256`, `active` | `db migrate` |
| `evaluation_log` | `eval_id`, `member_id`, `request_id`, `item_count`, `at` | `serve`, one row per accepted request |
| `rate_limits` | `member_id`, `window_start` (UTC midnight), `items_used` | `serve` |

No table has a column for an element, a token, an API key or the scalar. The scalar lives only in
the environment and in the memory of the serving process. The owner role (`{database}_owner`) owns
the tables and migrates. The serving process connects as the app role (`{database}_app`), which may
read `key_versions` and `api_credentials`, insert into `evaluation_log` (it cannot read, update or
delete a log row), and select, insert and update `rate_limits`.

Log lines are JSON on stdout. An access record holds the method, the route template
(`/v1/evaluate`), the status, the duration and the request id. No record holds a path, a query
string, a header value, an element or a key.

## Layout

```
key_holder/
  pyproject.toml  README.md  Dockerfile  alembic.ini  .env.example
  migrations/
    env.py          # Alembic environment: owner role URL, psycopg driver
    versions/       # 0001_schema (the four tables), 0002_grants (the app role's privileges)
  src/
    onepledge_key_holder/
      domain/
        identity  elements  evaluation  daily_window  credentials  errors
      application/
        report_health        # ReportHealth
        authenticate_member  # AuthenticateMember: X-API-Key by SHA-256
        evaluate_blinded     # EvaluateBlinded: POST /v1/evaluate
        ports                # what the use cases need from outside
      infrastructure/
        settings             # the one module that reads the environment
        scalar_keyring       # the scalar in memory, the one caller of the oprf package
        system  logging_setup
        db/                  # engines, provisioning, migrations, registration, repositories
      interface/
        http  wire  plumbing # create_app, the request rules, the envelope and the middleware
        composition  cli     # wiring, onepledge-keyholder
  tests/
```

## Commands

```
onepledge-keyholder db provision   create the roles and the database (needs PG_ADMIN_URL)
onepledge-keyholder db migrate     run the migrations, record the key version and the member key hashes
onepledge-keyholder serve          serve the API on BIND_HOST:PORT
```

`db provision` creates `{database}_owner`, `{database}_app` and the database, as the PostgreSQL
superuser. `db migrate` runs Alembic to head as the owner role, then records `ACTIVE_KEY_VERSION` in
`key_versions` and makes `api_credentials` equal to `MEMBER_KEY_HASHES`: a listed hash is active, any
other stored hash is switched off. Both are safe to repeat. Key rotation is not part of this version
(`SPEC.md` N4): `db migrate` refuses an `ACTIVE_KEY_VERSION` other than the one the database already
records, and a pinned version other than the active one is 409.

`serve` refuses to start, with exit 1, when the scalar is not 32 bytes or is all zeros, or when the
database does not record `ACTIVE_KEY_VERSION` as the active version. Every command exits 1 with one
sentence on stderr when a variable is missing or malformed or the database is not ready. The
sentence names the variable and never its value, for example `HOLDER_ID must be kh1 or kh2.`. A
usage error exits 2.

## Configuration

| Variable | Used by | Meaning |
|---|---|---|
| `HOLDER_ID` | `serve` | Required. `kh1` or `kh2`. |
| `ACTIVE_KEY_VERSION` | `serve`, `db migrate` | Required. The key version this holder evaluates with, 1 to 2,147,483,647 in ASCII digits. `/healthz` reports it as `active_key_version`. |
| `OPRF_SCALAR_B64` | `serve` | Required. Secret. The standard base64 of this holder's 32 byte scalar, not all zeros. Each holder has its own. |
| `DATABASE_URL` | `serve`, `db provision` | Required. Secret. `postgresql://{database}_app:PASSWORD@HOST:PORT/{database}`, the app role. |
| `MIGRATION_DATABASE_URL` | `db provision`, `db migrate`, Alembic `env.py` | Required by those. Secret. `postgresql://{database}_owner:PASSWORD@HOST:PORT/{database}`, the owner role. Never read by `serve`. |
| `PG_ADMIN_URL` | `db provision` | Required by it. Secret. A PostgreSQL superuser URL. |
| `MEMBER_KEY_HASHES` | `db migrate` | Required by it. A JSON object of member id (a lower case UUID) to the SHA-256 hex of that member's API key. The holder is never given a key itself. |
| `DAILY_ELEMENT_CAP` | `serve` | Elements one member may send per UTC day, 1 to 2,147,483,647. Default 1,000,000. |
| `PORT` | `serve` | TCP port, 1 to 65535 in ASCII digits. Default 8301 for `kh1` and 8302 for `kh2`. |
| `BIND_HOST` | `serve` | An IPv4 or IPv6 literal. A name such as `localhost` is refused. Default `127.0.0.1`, and `0.0.0.0` in the container. |

Every value is trimmed of ASCII space, tab, CR and LF at both ends. An empty value counts as
absent. A database URL carries no query parameters. `.env.example` lists every variable, the
secrets with empty values. Never commit a scalar, an API key or a password.

The scalars in `contracts/examples/vectors/tokenization.json` are public. The holder accepts them,
because WP3 runs both holders with them to reproduce the vector (`SPEC_DECISIONS.md` decision 15).
Never configure them in a holder that serves real data: the code does not stop you.

## Running both holders by hand

From the repository root, after `python3 -m pip install -e "key_holder[dev]"`, with PostgreSQL 16
at `PG_ADMIN_URL`. This setup step makes one member, its API key (`MEMBER_KEY`, which the member
keeps), the hash the holders store, and one scalar per holder:

```
export PG_ADMIN_URL=postgresql://postgres@localhost:55432/postgres
export MEMBER_ID=$(python3 -c "import uuid;print(uuid.uuid4())")
export MEMBER_KEY=$(python3 -c "import secrets;print('opk_'+secrets.token_urlsafe(32))")
export MEMBER_KEY_HASHES=$(python3 -c "import hashlib,json,os;print(json.dumps({os.environ['MEMBER_ID']:hashlib.sha256(os.environ['MEMBER_KEY'].encode()).hexdigest()}))")
export KH1_SCALAR=$(python3 -c "import base64,oprf;print(base64.b64encode(bytes(oprf.mask.random())).decode())")
export KH2_SCALAR=$(python3 -c "import base64,oprf;print(base64.b64encode(bytes(oprf.mask.random())).decode())")
export KH1_OWNER_URL=postgresql://onepledge_kh1_owner:kh1-owner-password@localhost:55432/onepledge_kh1
export KH1_APP_URL=postgresql://onepledge_kh1_app:kh1-app-password@localhost:55432/onepledge_kh1
export KH2_OWNER_URL=postgresql://onepledge_kh2_owner:kh2-owner-password@localhost:55432/onepledge_kh2
export KH2_APP_URL=postgresql://onepledge_kh2_app:kh2-app-password@localhost:55432/onepledge_kh2
```

The passwords above are examples for a local machine; choose your own. Then provision, migrate and
serve each holder with its own database and its own scalar:

```
MIGRATION_DATABASE_URL=$KH1_OWNER_URL DATABASE_URL=$KH1_APP_URL onepledge-keyholder db provision
MIGRATION_DATABASE_URL=$KH1_OWNER_URL ACTIVE_KEY_VERSION=1 onepledge-keyholder db migrate
MIGRATION_DATABASE_URL=$KH2_OWNER_URL DATABASE_URL=$KH2_APP_URL onepledge-keyholder db provision
MIGRATION_DATABASE_URL=$KH2_OWNER_URL ACTIVE_KEY_VERSION=1 onepledge-keyholder db migrate

HOLDER_ID=kh1 ACTIVE_KEY_VERSION=1 OPRF_SCALAR_B64=$KH1_SCALAR DATABASE_URL=$KH1_APP_URL PORT=8301 onepledge-keyholder serve &
HOLDER_ID=kh2 ACTIVE_KEY_VERSION=1 OPRF_SCALAR_B64=$KH2_SCALAR DATABASE_URL=$KH2_APP_URL PORT=8302 onepledge-keyholder serve &
curl -s http://127.0.0.1:8301/healthz
curl -s http://127.0.0.1:8302/healthz
```

The bank is pointed at them with `KEY_HOLDER_1_URL=http://127.0.0.1:8301`,
`KEY_HOLDER_2_URL=http://127.0.0.1:8302` and `KEY_HOLDER_1_API_KEY` and `KEY_HOLDER_2_API_KEY` set
to `MEMBER_KEY`. To read the kh1 database, use `KH1_OWNER_URL` (the app role cannot read the log):

```
psql "$KH1_OWNER_URL" -c "SELECT member_id, request_id, item_count, at FROM evaluation_log"
```

Demo traffic has no TLS. A real deployment puts TLS in front of both holders (`SPEC.md` N6).

## Tests

```
python3 -m pytest key_holder/tests -q
```

The integration tests need PostgreSQL 16 at `PG_ADMIN_URL` (default
`postgresql://postgres@localhost:55432/postgres`). They create and drop their own `optest_`
databases.

- The oracle comparison with layer 1's `KeyHolderDouble` (`RUBRIC.md` KH-02) is
  `python3 -m pytest key_holder/tests/test_oracle_double.py -q`. With the same scalar the elements
  match byte for byte, and each refusal has the double's status, code, message and details. It is
  the only test file that imports `onepledge_contract_fakes`.
- `test_evaluate_blinded.py` and `test_http_evaluate.py` hold every WP2 edge case, at the use case
  and over HTTP, and check each answer against the schemas in `contracts/key_holder.yaml`.
- `test_database.py` runs the schema, the roles, the cap under concurrent requests and the search
  for element bytes on real PostgreSQL.
- `test_serve.py` provisions, migrates and serves kh1 and kh2 as processes and sends them the two
  calls the bank's `TokenizeLoans` makes.
- `test_architecture.py` holds the layer rules: `domain` imports only the standard library and no
  network module, `application` imports only the domain, SQL packages only in `infrastructure` and
  the migrations, web packages only in `interface`, the environment read only in
  `infrastructure/settings.py`, no layer 1 package imported from `src/`, and no em dash and no
  `.env` file in the package, as the repository's pre-commit hooks require.
