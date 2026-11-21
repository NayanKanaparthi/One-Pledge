# OnePledge: Build Plan (team plan, agreed Nov 17, 2026)

Source of truth for the whole team. Layer owners: Nayan (layer 1), Shreyas (layer 2), Vedant (layer 3).

## 1. What we are building

A working OnePledge on synthetic data: three banks, one cheating lender, a shared registry that only ever sees codes, and a screen for each role. Each person owns one layer; layers meet only at contracts in `contracts/`, frozen on day 1 (changes are additive only, and need all three approvals).

The problem: a non-bank lender (for example a subprime auto lender) borrows from several banks through warehouse credit lines and pledges loans as collateral. Each bank only sees its own borrowing-base tape. A dishonest lender pledges the same loan to two banks (Tricolor 2025, First Brands 2025, Market Financial Solutions 2026). No bank can see it, and banks will not share their lists.

OnePledge: a program inside each bank cleans and checks every pledged loan, turns each loan into a scrambled code that cannot be reversed, and a shared registry locks each code to one bank. If a second bank submits the same code, both banks get an alert about their own loan; neither learns the other bank's identity or list. The registry never sees a raw value.

The professor sees nine scenes live, each from a real login:
1. A bank joins and loads the loans it already holds (back book).
2. An honest pledge goes through and the bank gets a signed certificate.
3. The cheating lender sends the same loans to a second bank. Both banks get an alert. Neither sees the other.
4. A disguised copy (one character or the amount changed) is still caught.
5. An invented loan is stopped inside the bank and nothing leaves.
6. The OnePledge operator opens the registry and finds only codes.
7. A bank releases a loan and another bank can then take it with no alert.
8. Two banks submit the same loan at the same instant and exactly one gets it.
9. Someone tries to edit history and the auditor's screen shows the break.

## 2. Layers and ownership

| Owner | Layer | Folders | Delivers | Never touches |
|---|---|---|---|---|
| Nayan | 1. Bank side and data | `datagen/`, `outside_records/`, `bank_agent/`, `evaluation/` | Fake loan files with an answer key. Simulated title, payment and contract records. The program that runs inside each bank. The scorecard that grades catches against the answer key | The registry's database |
| Shreyas | 2. Shared service | `key_holder/`, `registry/`, `ledger_verifier/` | The two key holders. The registry with locks, matching, cases and certificates. The append-only ledger and its verifier | Any raw loan value |
| Vedant | 3. Screens and delivery | `console/`, `infra/`, `e2e/`, `docs/` | Web console for every role. One command that starts everything. CI. End-to-end tests. Demo script | Business rules (they live in layers 1 and 2) |

Shared: `contracts/` and the release checklist (all three approve changes).

## 3. End to end, one loan file

1. File arrives: the lender's loan file is uploaded in the bank console. The bank agent stores a fingerprint (SHA-256) of the file and every raw row untouched.
2. Clean: one format per field. Rows that cannot be fixed go to a review list with the reason.
3. Check inside the bank: does the car have a title, is the lender named on it as lienholder, are payments arriving, is there a contract. A loan that fails stops here.
4. Scramble blind: the agent blinds each value, sends it through key holder 1 then key holder 2, and unblinds. Result: one stable code per loan (and per field). Neither key holder sees the value or holds the whole key.
5. Submit: codes go to the registry with an idempotency key, so a retry never locks twice.
6. Decide in one DB transaction (registry): lock every free code, record a conflict for every code already locked, append each fact to the ledger, queue alerts in an outbox.
7. Certify: the registry signs a certificate (checked / locked / held, and the ledger position `as_of_seq`).
8. Show: the analyst sees the result. On a conflict both banks see an alert on their own loan only.
9. Checkpoint: the registry signs the current end of the ledger. Each bank stores it and checks that it extends the last one it saw.

Release runs the same path with a release request. A conflict becomes a case that ends as a data error, a late release, or a confirmed double pledge.

Why two key holders: a VIN has a known format, so a plain hash can be reversed by enumeration. A keyed scramble stops that, but whoever holds the key could enumerate. So the key is split: one half with OnePledge, one with an independent party. A code exists only when both take part (blind OPRF in series). Each holder rate-limits and logs requests per bank.

## 4. Technology

| Concern | Choice |
|---|---|
| Services | Python 3.12+, type hints, FastAPI + Pydantic |
| Databases | PostgreSQL 16: one per bank, one registry, one per key holder (and one for outside records) |
| DB access | SQLAlchemy 2 + Alembic migrations |
| Blind scrambling | `oprf` package (ristretto255). Tested: two holders in series equal direct evaluation. Small, unaudited library |
| Signatures / hashes | Ed25519, SHA-256 |
| Screens | Next.js + TypeScript (layer 3) |
| Login | Email + password per bank user, roles, short-lived tokens. Service-to-service API keys stored hashed |
| AI | One model call that explains an alert in plain words, inside the bank, behind an interface with a no-AI fallback. AI never decides |
| Tests | pytest, Hypothesis, Playwright (layer 3) |
| Quality | ruff, mypy strict, pre-commit |
| Running | Docker Compose one command; GitHub Actions CI |

## 5. Data system

Four kinds of store; raw loan data exists only in the bank database. Money is integer cents, times UTC, IDs UUIDs, every status a fixed enum.

### Bank database (one per bank) - layer 1
| Table | Columns | Notes |
|---|---|---|
| lenders | lender_id, legal_name, lei, created_at | Non-bank lenders this bank funds |
| facilities | facility_id, lender_id, name, credit_limit_cents, advance_rate_bps, status, opened_on | One credit line |
| tape_submissions | submission_id, facility_id, kind, file_name, file_sha256, row_count, status, uploaded_by, uploaded_at | kind BACK_BOOK, PLEDGE, RELEASE. status RECEIVED, CLEANED, CHECKED, TOKENIZED, SUBMITTED, COMPLETED, FAILED |
| loans_raw | raw_id, submission_id, row_number, raw_payload, received_at | Exactly what the lender sent. Never updated |
| cleaning_issues | issue_id, raw_id, field, problem, action | action FIXED or REJECTED |
| loans | loan_id, facility_id, first_submission_id, vin, borrower_ref, origination_date, original_amount_cents, current_balance_cents, state, updated_at | state PENDING, HELD_LOCAL, LOCKED, CONFLICT, RELEASED. Unique (facility_id, vin) |
| local_checks | check_id, loan_id, submission_id, check_type, result, evidence, checked_at | TITLE_EXISTS, LIENHOLDER_MATCH, PAYMENTS_RECEIVED, CONTRACT_PRESENT; PASS, FAIL, UNKNOWN |
| loan_tokens | loan_id, token_kind, token, key_version, created_at | Private map code -> own loan. ASSET, VIN, BORROWER, AMOUNT_EXACT, AMOUNT_100, AMOUNT_1000, DATE_DAY, DATE_MONTH |
| registry_requests | request_id, submission_id, idempotency_key, kind, item_count, attempts, sent_at, response_status, certificate_id | idempotency_key unique |
| alerts | alert_id, registry_case_id, loan_id, match_kind, kinds_matched, explanation_text, status, received_at, reviewed_by, reviewed_at | EXACT or LIKELY; NEW, REVIEWING, CLEARED, ESCALATED |
| checkpoints_seen | checkpoint_seq, last_event_seq, head_hash, signature, received_at, verified, verified_at | Every signed checkpoint received |
| users | user_id, email, display_name, role, password_hash, active | ANALYST, CREDIT_OFFICER, ADMIN |
| audit_log | audit_id, actor_user_id, action, object_type, object_id, detail, at | Insert only |

### Outside records (one) - layer 1
| Table | Columns | Notes |
|---|---|---|
| title_records | vin, state, title_status, lienholder_name, lien_recorded_on | Stands in for state title offices |
| payments | payment_id, vin, paid_on, amount_cents, deposit_bank_code | Which bank's account the money landed in |
| contracts | contract_id, vin, lender_name, amount_cents, signed_on, document_sha256 | Stands in for a digital contract vault |

### Key holder database (one per holder) - layer 2
key_versions(key_version, status, created_at); evaluation_log(eval_id, member_id, request_id, item_count, at); rate_limits(member_id, window_start, items_used). Key itself in a secret, never a table.

### Registry database - layer 2 (codes only)
members, api_credentials, submissions (unique member_id+idempotency_key, stored response), ledger_events (seq gapless, insert-only, prev_hash, event_hash, schema_version; MEMBER_JOINED, LOCK_ACQUIRED, LOCK_RELEASED, CONFLICT_DETECTED, CASE_OPENED, CASE_RESOLVED, CERTIFICATE_ISSUED, CHECKPOINT_SIGNED, KEY_ROTATED), pledge_locks (unique asset_token where state LOCKED), lock_field_tokens, match_candidates, conflict_cases (OPEN, CLEARED_DATA_ERROR, CLEARED_LATE_RELEASE, CONFIRMED), certificates (as_of_seq, body_hash, signature), checkpoints, outbox (ALERT, CASE_UPDATE, CHECKPOINT), access_log. Locks/cases are views rebuilt by replaying ledger_events.

## 6. Design rules (Designing Data-Intensive Applications)

| Goal | Rule | Where |
|---|---|---|
| Reliability | A retried request never locks twice | idempotency keys + stored responses |
| Reliability | Race for one loan: exactly one wins | unique index on locked codes, one transaction, one primary |
| Reliability | No alert lost, none for a rolled-back decision | transactional outbox |
| Reliability | A crashed bank agent resumes where it stopped | tape_submissions.status stages, each safe to re-run |
| Reliability | Bad input cannot corrupt history | loans_raw immutable; cleaning re-runnable (derived data) |
| Auditability | Log is the truth; tables are views | ledger_events, replay |
| Auditability | History cannot be edited quietly | hash chain; updates/deletes blocked in DB |
| Auditability | Others can check us without trusting us | signed checkpoints stored and verified by every bank |
| Auditability | Order from sequence numbers, not clocks | registry seq |
| Transparency | Every answer names what it rests on | certificate as_of_seq; alert lists rule and fields |
| Transparency | Anyone with read access can verify | verifier tool and auditor screen |
| Evolvability | Old and new versions interoperate | schema_version, /v1 APIs, additive changes, migrations |

Out of scope at this size (say so if asked): replication, partitioning, streaming platform.

## 7. Security and privacy (THREAT_MODEL.md)

Threats: registry DB stolen (codes only); operator curious (never gets raw values, half a key); one key holder compromised (half key useless, rate limits); bank tests guesses via fake loans (daily cap, logging, local checks required); invented/forged loans (title, payment, contract checks in bank); history edited (hash chain, blocked updates, signed checkpoints held by banks); stolen service key (per-bank, hashed, scoped, revocable); bank insider (roles, bank-side audit log).

Rules: no raw loan value in any log, error message or URL. Least-privilege DB users (ledger writer insert-only). Secrets only in env files never committed (ship `.env.example`). Validate every input at the service edge. Passwords with a slow hash; short sessions. All data invented. Demo runs without TLS between services; a real deployment needs TLS (say so).

## 8. Contracts

Contract A (screens call): bank agent `POST /v1/auth/login`, `GET /v1/facilities`, `POST /v1/tapes`, `GET /v1/tapes/{id}`, `GET /v1/loans`, `GET /v1/alerts`, `POST /v1/alerts/{id}/review`, `GET /v1/certificates`, `GET /v1/integrity`; registry `GET /v1/ledger/events`, `GET /v1/checkpoints/latest`, `GET /v1/cases`.

Contract B (bank agent calls): key holder `POST /v1/evaluate`; registry `POST /v1/submissions`, `GET /v1/submissions/{id}`, `GET /v1/outbox`, `POST /v1/outbox/{id}/ack`, `POST /v1/cases/{id}/resolution`, `GET /v1/certificates/{id}`; outside records `GET /v1/titles/{vin}`, `GET /v1/payments`, `GET /v1/contracts/{vin}`.

Contracts live in `contracts/` as OpenAPI files plus shared example requests/responses all layers test against.

## 9. Code standards

Each service: `domain/` (plain classes and rules; no DB, web or network), `application/` (one class per use case; depends on interfaces), `infrastructure/` (repositories, clients, crypto; only place SQL appears), `interface/` (API routes and CLI; validate, call one use case, return).

Layer 1 classes (starting point): domain `Loan`, `TapeSubmission`, `Vin`, `Money`, `CheckResult`, `LoanToken`, `Alert`; application `IngestTape`, `CleanTape`, `RunLocalChecks`, `TokenizeLoans`, `SubmitPledge`, `ReleaseLoans`, `ReviewAlert`, `VerifyCheckpoint`; infrastructure `LoanRepository`, `TitleRecordsClient`, `PaymentsClient`, `KeyHolderClient`, `RegistryClient`, `AlertExplainer`.

Rules: type hints everywhere, mypy strict passes. Constructor injection, no globals. No rule in a route, no SQL outside a repository. Business errors are named exception classes. Unit tests for domain; tests against real PostgreSQL for repositories; contract tests against `contracts/`. At least 85% coverage on domain + application. No shortcuts to pass a test, no hardcoded expected answers; only `evaluation/` reads the answer key. Structured logs with request ID, never a raw loan value.

Repository layout:
```
onepledge/
  contracts/  datagen/  outside_records/  bank_agent/  evaluation/      (contracts shared; rest L1)
  key_holder/ registry/ ledger_verifier/                                  (L2)
  console/ e2e/ infra/ docs/                                              (L3)
  CODEOWNERS
```
GitHub: protected main; branches `l1/...`, `l2/...`, `l3/...`; CODEOWNERS; nobody merges own PR; CI green to merge; PRs about 400 lines or fewer.

## 10. Phases

0 contracts (day 1 together) -> 1 build alone against fakes (days 2-5) -> 2 join (6-7) -> 3 harden (8-10) -> 4 rehearse with cross-review (11-12).

Layer 1 per phase: P0 bank + outside-records table definitions, 20-loan example file with expected results. P1 data generator + answer key, outside records service, ingest/clean/local checks. P2 scramble through real key holders, submit to real registry, collect alerts. P3 release flow, field codes at several resolutions, scorecard, alert explainer.

## 11. Release checklist (team-wide)

- Same loan from two banks alerts both; neither sees the other's name or list.
- A copy with one VIN character or the amount changed is flagged as likely, with the fields that agreed.
- Honest look-alikes raise no alert: one borrower two cars, refinanced loan, loan moved after release.
- An invented loan with no title is stopped inside the bank; nothing is sent.
- After a release, another bank locks that loan with no alert.
- The same request sent twice produces one set of locks and the same certificate.
- Two banks submitting the same loan at the same instant, 100 times, get exactly one lock each time.
- DB refuses update/delete on the ledger; a forced change makes verification fail and the auditor screen shows where.
- Automated scan finds no VIN, name, amount or date in registry or key holder DBs.
- Bank B login cannot read Bank A's loans or alerts; operator sees codes only.
- Fresh laptop: one command starts everything; one script runs all nine scenes.
- Scorecard reports measured catches and false alarms; every planted exact copy is caught.
- CI green: lint, types, tests, contract check.

## 12. Synthetic data and role-play

Banks (invented): Harbor Bank, Northgate Bank, Calder Credit Fund. Lenders (invented): Summit Auto Finance (cheats), Lakeside Motor Credit (honest). About 5,000 car loans, each with valid-format VIN, title record, payment history and contract record.

Planted fraud: exact copy at a second bank; copy with amount changed; copy with date changed; copy with one VIN character changed; invented car with no title; title held by another lender; no payments for 90 days. Planted honest look-alikes that must not alert: one borrower with two cars; a loan that moved banks after a release; a refinanced loan. Answer key lists every planted case and expected outcome; only the scorecard reads it. Generator is seeded and deterministic.
