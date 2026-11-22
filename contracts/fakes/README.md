# onepledge-contract-fakes: TEST DOUBLES of the key holder and the registry

**These are test doubles, not layer 2.** This package holds in-memory stand-ins for the
two shared services that layer 2 (Shreyas) builds, plus the contract checker. They behave
as `contracts/key_holder.yaml` and `contracts/registry.yaml` describe, so layer 1 can be
verified end to end now, and layer 2 has an executable reference of the draft contract.

- No database. All state is in memory and is lost when the process stops.
- Every response of `/healthz` carries `"test_double": true`. `/healthz` ignores its query
  string (probes add cache busters) and never answers 422.
- No response is a redirect: a path with a trailing slash is an unknown route, 404
  `NOT_FOUND` in the error envelope. A 405 names the route's methods in `Allow`.
- Identifiers and encodings are canonical or refused (SPEC_DECISIONS 75): a UUID in a body
  (`item_ref`, `request_id`) is accepted only as 36 lower case hexadecimal characters with
  hyphens, anything else is `PATTERN_MISMATCH`; a path id in another spelling is 404; an
  `X-Request-ID` in another spelling is replaced by a new id, never rewritten; a key holder
  element whose base64 does not re-encode to the same text (non-zero padding bits) is
  `NOT_A_VALID_POINT`. A client that hangs up before its request is complete writes one
  `info` record `client_disconnected` and nothing else.
- Every 500 writes exactly one `error` log record carrying the response's `request_id`; any
  other 5xx writes one `warning` record. Every record of a 5xx response, the access record
  included, carries `error_code` (the code of the response body), `http_status`,
  `request_id` and `exception_type` (SPEC_DECISIONS 66), written in one place, the middleware.
  Log lines are JSON with the allow-listed keys of SPEC 16.5 plus `revision`; records of
  third party libraries below WARNING are dropped, and above it keep only `logger`,
  `level` and `exception_type` (event `library_log`), never the library's message.
- Test-only control endpoints live under `/_test/` and exist only when `FAKE_TEST_CONTROLS=true`.
- Nothing in layers 2 or 3 imports this package outside tests.

## What each double does

| Double | Contract | Behaviour |
|---|---|---|
| Key holder (`onepledge-fake-keyholder`) | `contracts/key_holder.yaml` | Real blind OPRF evaluation on ristretto255 with one secret scalar per key version (`oprf` package). Member authentication by SHA-256 of the API key. Whole request rejected on any invalid element (422 with the failing indexes). Pinned unknown key version: 409. Daily element cap per member: 429 with `Retry-After`. An evaluation log of member, request id, element count and time; elements are never stored or logged. |
| Registry (`onepledge-fake-registry`) | `contracts/registry.yaml` | Decision rules A, B and C, one atomic decision per submission behind one lock, cases with positions and late release resolution, Ed25519 signed certificates, a hash chained ledger, a signed checkpoint after every submission, a per member outbox with at least once delivery, idempotent submissions with byte for byte replay, key version check (409), daily item cap (429; it counts BACK_BOOK and PLEDGE items only, and never refuses a RELEASE), auditor only payload view, and 404 for another member's resources. At start it appends one `MEMBER_JOINED` event per configured member and signs checkpoint 1. |

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
onepledge-fake-keyholder serve [--debug] [--wide]   serve one key holder double on BIND_HOST:PORT
onepledge-fake-registry serve [--debug] [--wide]    serve the registry double on BIND_HOST:PORT
onepledge-contracts check [--contracts-dir DIR] [--json] [--quiet] [--debug] [--wide]
```

Every command prints one sentence on stderr and exits 1 when something is wrong, and
exits 2 on a usage error. An unexpected error prints `Unexpected error. Run with --debug
for details.`, after one `error` log record with `exception_type` and `stack` (on stdout
for `serve`, whose log lines go there, on stderr otherwise); `--debug` adds the exception
type and its frames (file, line, function), never the exception message, a source line or
a local variable. `serve` prints no result, so it refuses `--json` as a usage error. The
checker writes no other log line, so its `--quiet` has no effect (it is kept for one
interface across commands).
Human readable output is at most 100 columns: a longer line wraps onto continuation lines
indented two more spaces, and `--wide` turns the limit off. A usage error with `--json`
among the arguments also prints `{"ok": false, "error": {"code": "USAGE", "message": "The
command line is not valid. See the usage text."}}` on stdout, the usage text on stderr,
exit 2. No message ends in a colon (SPEC_DECISIONS 78).

`onepledge-contracts check` (run from the repository root, or `make contracts-check`)
validates the four OpenAPI 3.1 files with `openapi-spec-validator`, validates every entry
of `contracts/examples/index.json` against its component schema (JSON Schema 2020-12 with
`date`, `date-time` and `uuid` format checks), checks that every `externalValue` resolves
and every example file is indexed, and recomputes `examples/vectors/tokenization.json` and
`examples/vectors/signatures.json` from their stated inputs. It prints one line per check
and `contracts-check: OK (4 files, N examples)`, or the problems, `contracts-check: FAILED,
N problems` and exit code 1. A vector
that reads an example already reported invalid is not checked a second time: it prints
one `SKIP` line naming the example and adds no problem. No line names a Python class or
holds a Python repr: schema problems are plain sentences (`lacks the required property
has_more`), lists are comma separated. It also checks the shape of
`examples/cleaning_cases.json` (at least 120 cases, SPEC_DECISIONS 50).
Every file is read as hostile input (SPEC_DECISIONS 74): JSON through the bounded parser
(depth 32, number literals of at most 30 characters, no duplicate key), YAML through the
safe loader with the same depth, at most 5 MiB and no alias. A file that fails is one
problem naming it (`examples/vectors/signatures.json: nests too deeply`,
`registry.yaml: not valid YAML`), and a path read from a file (an index entry, a vector's
example file, an `externalValue`) must stay inside the directory, or the problem is
`examples/index.json names a path outside the directory.`
With `--json`, stdout holds exactly one JSON document: `ok`, the file, example and
problem counts, and one entry per check with its status, summary and problems (a
`FAILED` check's summary is the count of problems found, never its success sentence); on
failure also `error` with a `code` (`CONTRACTS_INVALID`, `CONTRACTS_DIR_NOT_FOUND`) and
the sentence that went to stderr.

## Configuration

Every value comes from the environment. A missing or malformed value stops startup with
one sentence naming the variable. The published test vector secrets are refused.

Key holder double:

| Variable | Default | Meaning |
|---|---|---|
| `HOLDER_ID` | required | `kh1` or `kh2`. |
| `PORT` | required | TCP port. |
| `BIND_HOST` | `127.0.0.1` | IP address to bind (`0.0.0.0` in containers). It must be an IPv4 or IPv6 literal (a name such as `localhost` is refused). Before anything else, `serve` binds a socket to it and refuses with one line, never printing the value: "BIND_HOST is an IPv6 address and this machine has no IPv6.", "BIND_HOST is not an address of this machine." or "BIND_HOST cannot be bound.", each followed by "Set BIND_HOST to 127.0.0.1 or 0.0.0.0." A taken `PORT` gives "PORT is already in use or not permitted on BIND_HOST; set another PORT." The rule and sentences are those of outside records (SPEC_DECISIONS 73). |
| `FAKE_KH_KEYS` | required | JSON object of key version to base64 scalar, for example `{"1": "<44 base64 characters>"}`. Versions are ASCII digits; every scalar must decode to 32 bytes and be neither zero nor one (modulo the group order). |
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
| `BIND_HOST` | `127.0.0.1` | IP address to bind, validated as for the key holder. |
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

Logs are JSON lines on stdout; their `service` is the health value (`key_holder`,
`registry`). An unhandled failure writes exactly one `error` record. Stop the doubles
with `kill %1 %2`.

## Input validation

Every request is checked in one order (SPEC_DECISIONS 66): route and method (404, 405),
authentication (401, the test control key included), the size limit and `Content-Length`
(413, 422), `Content-Type`, JSON well-formedness (`INVALID_JSON`), unknown fields and
parameters (reported alone), then field validation (every field, query and header problem
together, `Idempotency-Key` included). An unauthenticated caller learns nothing about its
body. An injected `BEFORE_PROCESSING` failure is answered before authentication, as an
outage would be.

Request bodies are checked by strict models: an integer field refuses `1.0`, `true` and
`"1"`, a string field refuses numbers, and no float reaches hashing. Query integers are 1
to 18 ASCII digits inside their bounds (`offset` at most 1,000,000). A 422 names a
contract field or `body` / `query` and one issue of the vocabulary of SPEC_DECISIONS 22:
`REQUIRED`, `INVALID_TYPE`, `INVALID_INTEGER`, `OUT_OF_RANGE`, `PATTERN_MISMATCH`,
`INVALID_VALUE`, `TOO_LONG`, `TOO_SHORT`, `TOO_MANY_ITEMS`, `TOO_FEW_ITEMS`, `DUPLICATE`,
`UNKNOWN_FIELD`, `UNKNOWN_PARAMETER`, `INVALID_JSON`, plus `NOT_A_VALID_POINT` and `DUPLICATE_ASSET_TOKEN`
from the contracts. A name the client invented is never returned: any unknown body key is
`body` `UNKNOWN_FIELD` once, any unknown query parameter `query` `UNKNOWN_PARAMETER`
(also on the `/_test/` routes). A JSON `null` where a value is required is
`INVALID_TYPE`; only an absent key is `REQUIRED`. A missing `Idempotency-Key` is
`REQUIRED`, a malformed one `PATTERN_MISMATCH`.

Every body, `/_test/` bodies included, is parsed by one bounded parser (SPEC_DECISIONS
38): a syntax error, invalid UTF-8, nesting deeper than 32 levels, a number literal longer
than 30 characters, a duplicate key, `NaN` or `Infinity`, or a top level value that is
not an object is `body` `INVALID_JSON`. A body must be sent with `Content-Type:
application/json` (parameters such as `charset` allowed), otherwise `Content-Type`
`INVALID_VALUE`. A malformed `Content-Length` is `Content-Length` `INVALID_INTEGER` (a negative one `OUT_OF_RANGE`), one
above 1 MiB is 413. Integer settings and header values follow the same rule as query
integers (ASCII digits only), so a malformed numeric setting stops startup with one line
naming the variable. Every setting is trimmed of ASCII space, tab, CR and LF at both
ends; a value that still holds any other whitespace or a control character is refused, with
one line naming the variable (secrets included).

## Test controls (`/_test/`, not part of any contract)

Every call needs the header `X-Test-Control-Key`.

| Endpoint | Double | Behaviour |
|---|---|---|
| `GET /_test/stats` | both | Totals and per member counts (requests and elements; or submissions, items, active locks, open cases, events, checkpoints). |
| `GET /_test/received` | both | Every request body received on contract endpoints, as raw text, in order. |
| `POST /_test/fail-next` | both | `{"count": n, "status": 503, "when": "BEFORE_PROCESSING" or "AFTER_COMMIT", "path": "/v1/submissions"}`, `path` optional. Without `path`, `BEFORE_PROCESSING` fails the next n contract calls of any kind. With `path` (a route template exactly as in the contract YAML, for example `/v1/outbox` or `/v1/certificates/{certificate_id}`), only calls to that route consume the n failures and other calls are served normally; an unknown template is 422 with issue `INVALID_VALUE` on `path`. `AFTER_COMMIT` (registry only) applies to the next n `POST /v1/submissions`, which are processed and stored before the failure is returned, simulating a lost response; with `AFTER_COMMIT`, `path` may only be `/v1/submissions`. `/healthz` and `/_test/` calls never consume a failure. Statuses: 429, 500, 502, 503, 504. A new call replaces any pending failure. |
| `GET /_test/dump` | registry | Members, locks with field codes, cases, events with payloads, certificates, checkpoints. Codes and registry identifiers only. |
| `POST /_test/tamper` | registry | `{"mode": "REWRITE_HISTORY", "seq": n}` rewrites one payload, recomputes every hash to the head and signs and distributes a new checkpoint. `{"mode": "EDIT_IN_PLACE", "seq": n}` changes one stored `payload_hash` only. `{"mode": "BAD_CHECKPOINT_SIGNATURE"}` signs and distributes a checkpoint with one bit of its signature flipped. `{"mode": "CERTIFICATE_BODY", "certificate_id": "..."}` changes `summary.locked` of a stored certificate without re-signing. `{"mode": "CERTIFICATE_RESTORE", "certificate_id": "..."}` puts that certificate back to exactly the document first signed (the double keeps the original from the moment of signing) and answers `{"mode", "certificate_id", "restored": true}`; on a certificate that was never tampered it is a no-op answered 200 with `"restored": false`; an unknown id is 404 `CERTIFICATE_NOT_FOUND`. In process, `RegistryDouble.test_controls.restore_certificate(certificate_id)` does the same and returns `restored` (SPEC_DECISIONS 65). |
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
