# onepledge-key-holder: one of the two OPRF key holders

Layer 2 of OnePledge (`docs/layer2/SPEC.md` section 4, contract `contracts/key_holder.yaml`).
Two processes run this one package. `HOLDER_ID=kh1` is holder 1. By default it listens on port 8301
and is the bank's `KEY_HOLDER_1_URL`. `HOLDER_ID=kh2` is holder 2. By default it listens on 8302 and
is `KEY_HOLDER_2_URL`. Each holder will hold its own secret scalar and its own database.

Status after WP1: the package installs and `serve` answers `GET /healthz`. `POST /v1/evaluate`,
the database schema, and `db provision` and `db migrate` arrive in WP2. The bank does not call this
process yet.

`GET /healthz` needs no key. It returns exactly the contract's `Health` fields. `test_double` is
absent, because this is not the layer 1 double (`SPEC_DECISIONS.md` decision 11):

```
{"status":"ok","service":"key_holder","holder_id":"kh1","active_key_version":1}
```

No answer is a redirect. A path with a trailing slash is an unknown route. No generated
documentation route (`/docs`, `/redoc`, `/openapi.json`) is served.

## Layout

```
key_holder/
  pyproject.toml  README.md  Dockerfile  alembic.ini
  migrations/
    env.py          # Alembic environment: owner role URL, psycopg driver
    versions/       # no revision yet; WP2 adds the SPEC 4.1 tables
  src/
    onepledge_key_holder/
      domain/
        identity        # HolderId (kh1, kh2), KeyVersion (1 to 2,147,483,647)
      application/
        report_health   # ReportHealth: holder id and active key version
      infrastructure/
        settings        # the one module that reads the environment
      interface/
        http            # create_app: GET /healthz
        cli             # onepledge-keyholder serve
  tests/
```

## Commands

```
onepledge-keyholder serve      serve the API on BIND_HOST:PORT
```

`serve` exits 1 with one sentence on stderr when a variable is missing or malformed. The sentence
names the variable and never its value, for example `HOLDER_ID must be kh1 or kh2.`. A usage
error exits 2.

## Configuration

| Variable | Used by | Meaning |
|---|---|---|
| `HOLDER_ID` | `serve` | Required. `kh1` or `kh2`. |
| `ACTIVE_KEY_VERSION` | `serve` | Required. The key version this holder evaluates with, 1 to 2,147,483,647 in ASCII digits. `/healthz` reports it as `active_key_version`. |
| `PORT` | `serve` | TCP port, 1 to 65535 in ASCII digits. Default 8301 for `kh1` and 8302 for `kh2`. |
| `BIND_HOST` | `serve` | An IPv4 or IPv6 literal. A name such as `localhost` is refused. Default `127.0.0.1`, and `0.0.0.0` in the container. |
| `MIGRATION_DATABASE_URL` | Alembic `env.py` | The owner role's URL. Never read by `serve`. |

Every value is trimmed of ASCII space, tab, CR and LF at both ends. An empty value counts as
absent. No variable of WP1 is a secret. The scalar and the member keys arrive in WP2, and
`.env.example` will list them with empty values.

## Running it by hand

From the repository root, after `python3 -m pip install -e "key_holder[dev]"`:

```
HOLDER_ID=kh1 ACTIVE_KEY_VERSION=1 PORT=8301 onepledge-keyholder serve &
HOLDER_ID=kh2 ACTIVE_KEY_VERSION=1 PORT=8302 onepledge-keyholder serve &
curl -s http://127.0.0.1:8301/healthz
curl -s http://127.0.0.1:8302/healthz
```

Demo traffic has no TLS. A real deployment puts TLS in front of both holders (`SPEC.md` N6).

## Tests

```
python3 -m pytest key_holder/tests -q
```

The unit tests cover the settings, the health body against the `Health` schema in
`contracts/key_holder.yaml`, the absence of redirects and documentation routes, and the
architecture rules. Those rules are: `domain` imports only the standard library and no
network module, `application` imports only the domain, SQL packages only in `infrastructure` and
the migrations, web packages only in `interface`, the environment read only in
`infrastructure/settings.py`, no layer 1 package imported from `src/`, and no em dash and no
`.env` file in the package, as the repository's pre-commit hooks require. The integration
tests start `onepledge-keyholder serve` as a process and read `/healthz` over TCP.
