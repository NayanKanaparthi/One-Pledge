# onepledge-contract-fakes: TEST DOUBLES of the key holder and the registry

**These are test doubles, not layer 2.** This package holds in-memory stand-ins for the
two shared services that layer 2 (Shreyas) builds, plus the contract checker. They behave
as `contracts/key_holder.yaml` and `contracts/registry.yaml` describe, so layer 1 can be
verified end to end now, and layer 2 has an executable reference of the draft contract.

- No database. All state is in memory and is lost when the process stops.
- Every response of `/healthz` carries `"test_double": true`.
- Test-only control endpoints live under `/_test/` and exist only when `FAKE_TEST_CONTROLS=true`.
- Nothing in layers 2 or 3 imports this package outside tests.

## What each double does

| Double | Contract | Behaviour |
|---|---|---|
| Key holder (`onepledge-fake-keyholder`) | `contracts/key_holder.yaml` | Real blind OPRF evaluation on ristretto255 with one secret scalar per key version (`oprf` package). Member authentication by SHA-256 of the API key. Whole request rejected on any invalid element (422 with the failing indexes). Pinned unknown key version: 409. Daily element cap per member: 429 with `Retry-After`. An evaluation log of member, request id, element count and time; elements are never stored or logged. |
| Registry (`onepledge-fake-registry`) | `contracts/registry.yaml` | Decision rules A, B and C, one atomic decision per submission behind one lock, cases with positions and late release resolution, Ed25519 signed certificates, a hash chained ledger, a signed checkpoint after every submission, a per member outbox with at least once delivery, idempotent submissions with byte for byte replay, key version check (409), daily item cap (429), auditor only payload view, and 404 for another member's resources. At start it appends one `MEMBER_JOINED` event per configured member and signs checkpoint 1. |

## Layout

```
contracts/fakes/
  pyproject.toml  README.md  Dockerfile
  src/onepledge_contract_fakes/
    canonical_json.py  errors.py  settings.py  members.py  daily_cap.py  clock.py
    controls.py  web.py  logging_setup.py  server.py  cli_output.py  testing.py
    key_holder/        config, settings, double (KeyHolderDouble), app factory, cli
    registry/          model, locks, matching (rules A, B, C), cases, ledger, signing,
                       checkpoints, certificates, outbox, idempotency, stores, publisher,
                       submissions, inspection (stats, dump, tamper), double (RegistryDouble),
                       wire (request models), app factory, control_routes, settings, cli
    contracts_check/   documents, schemas, vectors, checker, cli
  tests/
```

`KeyHolderDouble` and `RegistryDouble` are plain classes usable in-process with no HTTP.
`create_key_holder_app(config)` and `create_registry_app(config)` wrap them in FastAPI.
`onepledge_contract_fakes.testing` offers a fixed clock, counting ids, configuration
builders and an in-process HTTP client for other layers' tests.

## Commands

```
onepledge-fake-keyholder serve        serve one key holder double on BIND_HOST:PORT
onepledge-fake-registry serve         serve the registry double on BIND_HOST:PORT
onepledge-contracts check [--contracts-dir contracts]
```

`onepledge-contracts check` (run from the repository root, or `make contracts-check`)
validates the four OpenAPI 3.1 files with `openapi-spec-validator`, validates every entry
of `contracts/examples/index.json` against its component schema (JSON Schema 2020-12 with
`date`, `date-time` and `uuid` format checks), checks that every `externalValue` resolves
and every example file is indexed, and recomputes `examples/vectors/tokenization.json` and
`examples/vectors/signatures.json` from their stated inputs. It prints one line per check
and `contracts-check: OK (4 files, N examples)`, or the problems and exit code 1.

## Configuration

Every value comes from the environment. A missing or malformed value stops startup with
one sentence naming the variable. The published test vector secrets are refused.

Key holder double:

| Variable | Default | Meaning |
|---|---|---|
| `HOLDER_ID` | required | `kh1` or `kh2`. |
| `PORT` | required | TCP port. |
| `BIND_HOST` | `127.0.0.1` | Address to bind (`0.0.0.0` in containers). |
| `FAKE_KH_KEYS` | required | JSON object of key version to base64 scalar, for example `{"1": "<44 base64 characters>"}`. Every scalar must decode to 32 bytes and not be zero. |
| `FAKE_KH_ACTIVE_VERSION` | required | The version used when a request does not pin one. |
| `FAKE_KH_MEMBERS` | required | JSON list of `{"member_id": "<uuid>", "api_key_sha256": "<64 hex>"}`. |
| `FAKE_KH_DAILY_ELEMENT_CAP` | `1000000` | Elements per member per UTC day. |
| `FAKE_TEST_CONTROLS` | `false` | Mount the `/_test/` endpoints. |
| `FAKE_TEST_CONTROL_KEY` | required when controls are on | At least 32 characters, sent as `X-Test-Control-Key`. |
| `LOG_LEVEL` | `INFO` | |

Registry double:

| Variable | Default | Meaning |
|---|---|---|
| `PORT` | required | TCP port. |
| `BIND_HOST` | `127.0.0.1` | Address to bind. |
| `FAKE_REGISTRY_SIGNING_SEED_B64` | required | Base64 of a 32 byte Ed25519 seed. |
| `REGISTRY_SIGNING_KEY_ID` | required | Key id placed on certificates and checkpoints, `^[a-z0-9-]{1,64}$`. |
| `FAKE_REGISTRY_MEMBERS` | required | JSON list of `{"member_id", "api_key_sha256"}`, in the order members join. |
| `FAKE_REGISTRY_AUDITOR_KEY_SHA256` | required | SHA-256 of the auditor key that may read `view=full`. |
| `FAKE_REGISTRY_KEY_VERSION` | `1.1` | The only key version accepted on submissions. |
| `FAKE_REGISTRY_DAILY_ITEM_CAP` | `1000000` | Items per member per UTC day. |
| `FAKE_TEST_CONTROLS`, `FAKE_TEST_CONTROL_KEY`, `LOG_LEVEL` | as above | |

## Run the doubles by hand

From the repository root after `make setup PIP_FLAGS=--break-system-packages`:

```
export MEMBER_KEY="opk_$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')"
export MEMBER_HASH="$(python3 -c 'import hashlib,os;print(hashlib.sha256(os.environ["MEMBER_KEY"].encode()).hexdigest())')"
export MEMBERS="[{\"member_id\": \"$(python3 -c 'import uuid;print(uuid.uuid4())')\", \"api_key_sha256\": \"$MEMBER_HASH\"}]"
export FAKE_TEST_CONTROLS=true
export FAKE_TEST_CONTROL_KEY="$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')"

PORT=18301 HOLDER_ID=kh1 FAKE_KH_ACTIVE_VERSION=1 FAKE_KH_MEMBERS="$MEMBERS" \
  FAKE_KH_KEYS="{\"1\": \"$(python3 -c 'import oprf;print(oprf.mask.random().to_base64())')\"}" \
  onepledge-fake-keyholder serve &

PORT=18400 REGISTRY_SIGNING_KEY_ID=registry-local-1 FAKE_REGISTRY_MEMBERS="$MEMBERS" \
  FAKE_REGISTRY_SIGNING_SEED_B64="$(python3 -c 'import base64,secrets;print(base64.b64encode(secrets.token_bytes(32)).decode())')" \
  FAKE_REGISTRY_AUDITOR_KEY_SHA256="$(python3 -c 'import hashlib,secrets;print(hashlib.sha256(secrets.token_bytes(32)).hexdigest())')" \
  onepledge-fake-registry serve &

curl -s http://127.0.0.1:18301/healthz      # {"status":"ok","service":"key_holder","holder_id":"kh1",...,"test_double":true}
curl -s http://127.0.0.1:18400/healthz      # {"status":"ok","service":"registry","test_double":true}
```

Logs are JSON lines on stdout. Stop the doubles with `kill %1 %2`.

## Test controls (`/_test/`, not part of any contract)

Every call needs the header `X-Test-Control-Key`.

| Endpoint | Double | Behaviour |
|---|---|---|
| `GET /_test/stats` | both | Totals and per member counts (requests and elements; or submissions, items, active locks, open cases, events, checkpoints). |
| `GET /_test/received` | both | Every request body received on contract endpoints, as raw text, in order. |
| `POST /_test/fail-next` | both | `{"count": n, "status": 503, "when": "BEFORE_PROCESSING" or "AFTER_COMMIT"}`. `BEFORE_PROCESSING` fails the next n contract calls of any kind. `AFTER_COMMIT` (registry only) applies to the next n `POST /v1/submissions`, which are processed and stored before the failure is returned, simulating a lost response. Statuses: 429, 500, 502, 503, 504. |
| `GET /_test/dump` | registry | Members, locks with field codes, cases, events with payloads, certificates, checkpoints. Codes and registry identifiers only. |
| `POST /_test/tamper` | registry | `{"mode": "REWRITE_HISTORY", "seq": n}` rewrites one payload, recomputes every hash to the head and signs and distributes a new checkpoint. `{"mode": "EDIT_IN_PLACE", "seq": n}` changes one stored `payload_hash` only. `{"mode": "BAD_CHECKPOINT_SIGNATURE"}` signs and distributes a checkpoint with one bit of its signature flipped. `{"mode": "CERTIFICATE_BODY", "certificate_id": "..."}` changes `summary.locked` of a stored certificate without re-signing. |
| `POST /_test/checkpoint` | registry | Signs and distributes a checkpoint now. |

## Tests

```
python3 -m pytest contracts/fakes -q
```

They cover the vectors, the OPRF property (two holders in series equal direct evaluation,
independent of the blind), every rule and its non-triggers, idempotent replay, a race of
two members for one asset code repeated 100 times, every tamper mode, live responses of
every endpoint against the YAML, the checker on a broken copy, the logging rules and the
command lines.

## Docker

`Dockerfile` builds one image for both doubles (base `python:3.12-slim`, non-root user,
`HEALTHCHECK` on `/healthz`). The default entrypoint is `onepledge-fake-registry serve`;
key holder containers override it with `onepledge-fake-keyholder serve`. It was written
for teammates' laptops and was not run on the build machine, which has no Docker daemon.
