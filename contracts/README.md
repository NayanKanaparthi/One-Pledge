# OnePledge contracts (DRAFT)

Status: **DRAFT, version 1.0.0-draft.1.** Written by layer 1 (Nayan) so that layer 1 can be built and verified now. These files are **not frozen**. They become the team contract only when Shreyas (layer 2) and Vedant (layer 3) approve them. Until then, treat every file here as a proposal that layer 1 already implements.

## What is here

| File | Contract | Served by | Called by |
|---|---|---|---|
| `bank_agent.yaml` | Contract A, bank agent part | `bank_agent/` (layer 1) | console (layer 3) |
| `key_holder.yaml` | Contract B, key holder | `key_holder/` (layer 2) | bank agent |
| `registry.yaml` | Contract B registry endpoints the bank agent calls, plus the Contract A read endpoints | `registry/` (layer 2) | bank agent, console |
| `outside_records.yaml` | Contract B, outside records | `outside_records/` (layer 1) | bank agent |
| `examples/` | One example JSON per request and response, and shared test vectors | all layers test against these | |
| `examples/index.json` | Maps every example file to the schema it must satisfy | contract tests in every layer | |
| `examples/vectors/tokenization.json` | Fixed inputs, fixed test scalars, every intermediate OPRF value and the final codes for one loan | bank agent and key holder tests | |
| `examples/vectors/signatures.json` | Canonical JSON bytes, hashes and Ed25519 signatures for a certificate, a checkpoint and a ledger event | bank agent, registry and verifier tests | |
| `fakes/` | Contract test doubles for the key holder and the registry (see below) | layer 1 tests, layer 1 demo | |

All four YAML files are OpenAPI 3.1.0 and pass `openapi-spec-validator`. Every example validates against its schema. `make contracts-check` runs both checks.

## The test doubles in `fakes/` are not layer 2

`contracts/fakes/` holds small, clearly labelled **contract test doubles** for the key holder and the registry. They implement the behaviour written in `key_holder.yaml` and `registry.yaml` (real OPRF evaluation, idempotent submissions, lock uniqueness, conflicts, signed certificates, hash chained events, signed checkpoints, an outbox). They keep all state in memory, have test-only control endpoints under `/_test/`, and report `"test_double": true` on `/healthz`.

They exist for two reasons: layer 1 can be verified end to end before layer 2 exists, and Shreyas has an executable reference for the contract. They are not production code, they are not the registry, and nothing in layers 2 or 3 should import them outside tests.

## Change rule

1. While DRAFT: any of the three owners may propose a change by pull request to `contracts/`. The pull request must update the YAML, the affected examples, `examples/index.json`, and state which layers are affected.
2. Once approved by all three owners the contracts are **frozen at 1.0.0**. From then on changes are **additive only** and need all three approvals:
   - Allowed: a new endpoint, a new optional request field, a new response field, a new enum value where the contract already tells clients how to treat unknown values, a new error code on an endpoint that already returns errors.
   - Not allowed: removing or renaming anything, changing a type or a meaning, making an optional field required, tightening a pattern or a limit, changing a status code.
3. Clients must ignore response fields they do not know. Servers must reject request fields they do not know (`additionalProperties: false`).
4. A breaking change needs a new path prefix (`/v2`) and a migration note. It does not happen inside `/v1`.

## Decisions in this draft that need the other owners' eyes

These are choices layer 1 made because the plan did not settle them. Each is implemented in the fakes and assumed by the bank agent.

| # | Decision | Affects |
|---|---|---|
| 1 | Codes are 64 character lower case hex: SHA-256 over the unblinded OPRF output with a domain prefix. Blinded elements travel as standard base64 of the 32 byte ristretto point. Exact rules and a full vector are in `examples/vectors/tokenization.json`. | Shreyas |
| 2 | `key_version` on a submission is `"<holder 1 version>.<holder 2 version>"`. The registry rejects a submission whose version is not current (409 KEY_VERSION_MISMATCH). | Shreyas |
| 3 | The lock key (ASSET) is derived from the VIN alone: one car, one lock. A copy with a changed amount or date therefore still collides on the asset and is reported as `match_kind: LIKELY, match_basis: ASSET`. EXACT is reserved for a copy where borrower, exact amount and day also agree. | Shreyas, Vedant |
| 4 | A field-only match (Rule C in `registry.yaml`: different vehicle code, same borrower, amount and date) is a CONFLICT with no lock, `match_kind: LIKELY, match_basis: FIELDS`. | Shreyas |
| 5 | AMOUNT and DATE codes are bound to the borrower code input, so the registry cannot build a global histogram of amounts or dates. | Shreyas |
| 6 | A case resolves when both parties record the same position. The registry auto-resolves CLEARED_LATE_RELEASE when the holder releases. Positions are enums only, no free text. | Shreyas, Vedant |
| 7 | Any member may read ledger event **headers** (hashes, no payloads) so a bank can verify that each checkpoint extends the previous one. Payloads need an auditor or operator key. | Shreyas, Vedant |
| 8 | Certificates and checkpoints are Ed25519 signatures over canonical JSON (rule in `registry.yaml`). Banks get the registry public key out of band (environment), never from the registry API. | Shreyas |
| 9 | Outside records keep the planned paths with the VIN in the URL. Both sides must not log paths or query strings for that API. A body based lookup would be cleaner and can be added later as an additive change. | Vedant |
| 10 | The bank agent adds endpoints beyond the plan's Contract A list: `/v1/auth/me`, `GET /v1/tapes`, `/v1/tapes/{id}/items`, `/v1/tapes/{id}/retry`, `/v1/releases`, `/v1/loans/search`, `/v1/loans/{id}`, `/v1/loans/{id}/lineage`, `/v1/loans/{id}/resubmit`, `/v1/alerts/{id}`, `/v1/certificates/{id}`, `/v1/integrity/verify`, `/v1/registry/sync`, `/v1/audit-log`, `/v1/users`, `/healthz`, `/readyz`. | Vedant |
| 11 | ADMIN cannot read loan, tape, alert or certificate data (segregation of duties). | Vedant |

## Validate locally

```
make contracts-check
```

This parses the four YAML files, validates them as OpenAPI 3.1, validates every file in `examples/index.json` against its schema, and checks that every `externalValue` in the YAML points at an existing example.
