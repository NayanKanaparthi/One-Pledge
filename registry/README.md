# onepledge-registry: the shared ledger of pledge locks

Layer 2 of OnePledge (`docs/layer2/SPEC.md` sections 5 to 7, contract `contracts/registry.yaml`).
The registry receives 64 hex tokens from member banks, never raw loan values. It grants at most
one active lock per asset token, and it records every decision in an insert-only, hash-chained
ledger. It is the bank's `REGISTRY_URL`. The default port is 8400.

Status after WP1: the package installs and `serve` answers `GET /healthz`. The database, the
ledger and `db provision` and `db migrate` arrive in WP4. Submissions arrive in WP5. The bank does
not call this process yet.

`GET /healthz` needs no key. It returns exactly the contract's `Health` fields. `test_double` is
absent, because this is not the layer 1 double (`SPEC_DECISIONS.md` decision 11):

```
{"status":"ok","service":"registry"}
```

No answer is a redirect. A path with a trailing slash is an unknown route. No generated
documentation route (`/docs`, `/redoc`, `/openapi.json`) is served.

## Layout

```
registry/
  pyproject.toml  README.md  Dockerfile  alembic.ini
  migrations/
    env.py          # Alembic environment: owner role URL, psycopg driver
    versions/       # no revision yet; WP4 adds the SPEC 5 tables
  src/
    onepledge_registry/
      domain/           # empty until WP4
      application/      # empty until WP5
      infrastructure/
        settings        # the one module that reads the environment
      interface/
        http            # create_app: GET /healthz
        cli             # onepledge-registry serve
  tests/
```

## Commands

```
onepledge-registry serve       serve the API on BIND_HOST:PORT
```

`serve` exits 1 with one sentence on stderr when a variable is malformed. The sentence names the
variable and never its value. A usage error exits 2.

## Configuration

| Variable | Used by | Meaning |
|---|---|---|
| `PORT` | `serve` | TCP port, 1 to 65535 in ASCII digits. Default 8400. |
| `BIND_HOST` | `serve` | An IPv4 or IPv6 literal. A name such as `localhost` is refused. Default `127.0.0.1`, and `0.0.0.0` in the container. |
| `MIGRATION_DATABASE_URL` | Alembic `env.py` | The owner role's URL. Never read by `serve`. |

Every value is trimmed of ASCII space, tab, CR and LF at both ends. An empty value counts as
absent. No variable of WP1 is a secret.

## Running it by hand

From the repository root, after `python3 -m pip install -e "registry[dev]"`:

```
PORT=8400 onepledge-registry serve &
curl -s http://127.0.0.1:8400/healthz
```

Demo traffic has no TLS. A real deployment puts TLS in front of the registry (`SPEC.md` N6).

## Tests

```
python3 -m pytest registry/tests -q
```

The unit tests cover the settings, the health body against the `Health` schema in
`contracts/registry.yaml`, the absence of redirects and documentation routes, and the same
architecture and pre-commit rules as `key_holder/`. The integration tests start
`onepledge-registry serve` as a process and read `/healthz` over TCP.
