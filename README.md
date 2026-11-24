# OnePledge

A neutral cross-bank check that a lender has not pledged the same loan to two banks,
without any bank revealing its loan book.

NYU Tandon MG-GY 8503 FinTech, Fall 2026. Team: Nayan Kanaparthi (layer 1), Shreyas
Vyamajala (layer 2), Vedant Chawardol (layer 3).

## The problem

Non-bank lenders, such as subprime auto lenders, fund themselves by borrowing from
several banks through warehouse credit lines. Each line is secured by a pool of the
lender's loans, which the lender reports to the bank as a borrowing-base tape. Each bank
sees only its own tape.

A dishonest lender can pledge the same loan to two banks at once. No single bank can
see it, and banks will not share their loan lists with each other. When the lender
fails, two banks discover they were relying on the same collateral. This is the pattern
behind the Tricolor (2025), First Brands (2025) and Market Financial Solutions (2026)
collapses.

## What OnePledge does

A program inside each bank cleans and checks every pledged loan, then turns it into a
scrambled code that cannot be reversed. A shared registry locks each code to one bank.
If a second bank submits the same code, both banks get an alert about their own loan.
Neither learns the other bank's identity or loan list, and the registry never sees a raw
value.

### One loan file, end to end

1. **Upload.** The lender's loan tape is uploaded at the bank. The bank agent stores a
   SHA-256 fingerprint of the file and every raw row exactly as received.
2. **Clean.** Each field is brought to one format. Rows that cannot be fixed go to a
   review list with the reason.
3. **Check inside the bank.** Does the car have a title, is the lender named on it as
   lienholder, are payments arriving, is there a contract. A loan that fails stops here
   and nothing about it leaves the bank.
4. **Scramble blind.** The agent blinds each value, sends it through key holder 1 and
   then key holder 2, and unblinds the result. This gives one stable code per loan and
   per field. Neither key holder sees the value, and neither holds the whole key.
5. **Submit.** The codes go to the registry with an idempotency key, so a retry never
   locks twice.
6. **Decide.** In one database transaction the registry locks every free code, records
   a conflict for every code that another bank already holds, appends each fact to its
   ledger and queues alerts.
7. **Certify.** The registry signs a certificate for the submission (checked, locked or
   held, and the ledger position). The bank verifies the signature and keeps it.
8. **Alert.** On a conflict, both banks see an alert on their own loan only. The case
   ends as a data error, a late release or a confirmed double pledge.
9. **Checkpoint.** The registry signs the current end of its ledger. Each bank checks
   that it extends the last checkpoint it saw, so a rewritten history is detected.

Releasing a loan runs the same path. Once a loan is released, another bank can take it
without an alert.

### Why two key holders

A VIN has a known format, so a plain hash could be reversed by trying every possible
VIN. A keyed scramble stops that, but whoever holds the key could still try every VIN.
So the key is split between OnePledge and an independent party, and a code exists only
when both take part (a blind OPRF over ristretto255, evaluated in series). Each key
holder rate-limits and logs requests per bank.

### What gets caught

- **Exact copies:** the same loan pledged twice, matched on the car (VIN).
- **Disguised copies:** the same car with the amount or date changed is still flagged,
  with the fields that differ.
- **One-character VIN changes:** mostly stopped inside the bank, because the VIN check
  digit fails or no title exists for the altered VIN.
- **Invented loans:** stopped by the checks against title, payment and contract records
  before anything is sent.

### Trust and audit

- The registry stores only codes. A bank's raw loan values never leave the bank, apart
  from the VIN lookups it makes against outside records.
- Raw rows, the audit log and the evidence tables are insert-only. Database triggers
  refuse updates and deletes, including from the table owner.
- The bank's audit log is hash chained, and an integrity report checks the chain, the
  guards, the role privileges and the stored certificates and checkpoints.
- Every bank-side service runs with a least-privilege database role and refuses to start
  with more privileges than it needs.

## Architecture

| Layer | Owner | Components |
|---|---|---|
| 1: bank side and data | Nayan | Synthetic data generator with an answer key; outside records service (titles, payments, contracts); the bank agent; the evaluation scorecard |
| 2: shared service | Shreyas | The two key holders; the registry (locks, matching, cases, certificates); the append-only ledger and its verifier |
| 3: screens and delivery | Vedant | Web console for every role; one command that starts the stack; CI; end-to-end tests; demo script |

The layers meet only through the contracts in `contracts/` (OpenAPI 3.1). Each bank has
its own PostgreSQL database, as do outside records, the registry and each key holder.

Technology: Python 3.12+, FastAPI and Pydantic, PostgreSQL 16 with SQLAlchemy 2 and
Alembic, the `oprf` package (ristretto255), Ed25519 and SHA-256, pytest and Hypothesis,
ruff and mypy strict. The console is planned in Next.js and TypeScript. The full team
plan is [docs/BUILD_PLAN.md](docs/BUILD_PLAN.md).

## Repository layout

| Folder | What it holds |
|---|---|
| `datagen/` | Deterministic synthetic data sets: lenders, banks, loan tapes, title and payment records, and an answer key |
| `outside_records/` | The title, payment and contract records service the bank agent calls |
| `bank_agent/` | The program that runs inside one bank |
| `contracts/` | OpenAPI drafts, examples, test vectors, the contract checker, and test doubles of the key holder and registry |
| `tools/` | Shared test tooling: scratch databases, unit isolation, repository rules |
| `docs/` | The team build plan and the layer 1 specification |

## Status

| Layer | State |
|---|---|
| 1: bank side and data | Core built and tested; HTTP API and stack tooling in progress |
| 2: key holders, registry, ledger verifier | Starting |
| 3: console, infra, end to end | Starting |
| Shared contracts | Draft, awaiting Shreyas and Vedant's approval |

Layer 1 today:

- `datagen/` generates data sets and verifies them by regenerating and comparing.
- `outside_records/` serves title and payment lookups.
- `bank_agent/` runs the whole pipeline from the command line: intake, clean, check,
  tokenise, submit, certificate and checkpoint verification, alerts and case updates,
  release, and the integrity report.
- `contracts/fakes/` runs test doubles of the key holder and registry, so the bank side
  can be exercised end to end before layer 2 exists.

In progress in layer 1:

- The bank agent's HTTP API from `contracts/bank_agent.yaml`, with login, users and the
  permission matrix. Until it lands, the console can be built against the contract and
  its examples.
- The evaluation scorecard, demo runner, stack launcher, Docker Compose for the whole
  stack, and the layer 1 threat model.

Last full run (`make ci`): lint, mypy strict, the contract check and 4,828 tests pass,
with 96 to 98 percent coverage per package.

## For Shreyas (layer 2)

- Your contracts are `contracts/key_holder.yaml` and `contracts/registry.yaml`. Read
  `contracts/README.md` first, then propose changes there.
- `contracts/fakes/` holds runnable test doubles of the key holder (real ristretto255
  OPRF) and the registry (lock rules, cases, Ed25519 signed certificates, hash chained
  events, signed checkpoints, outbox, idempotent replay). They show the behaviour the
  bank agent expects; they are not production code. Your services should pass the same
  examples in `contracts/examples/` and the vectors in `contracts/examples/vectors/`.
- The bank agent's registry and key holder clients are in
  `bank_agent/src/onepledge_bank_agent/infrastructure/http/`. Point them at your services
  instead of the doubles to test end to end.

## For Vedant (layer 3)

- The console talks to the bank agent through `contracts/bank_agent.yaml` and to the
  registry read endpoints in `contracts/registry.yaml`. Build against the examples in
  `contracts/examples/` until the bank agent's HTTP API lands.
- `outside_records/` and `contracts/fakes/` each have a Dockerfile. The stack compose
  file belongs in `infra/`.

## Getting started

Needs Python 3.12 or later and PostgreSQL 16.

```bash
git clone https://github.com/NayanKanaparthi/One-Pledge.git && cd One-Pledge
python3 -m venv .venv && . .venv/bin/activate
make setup            # every package editable, with its dev tools
make data             # generate a data set into var/data (SEED and PROFILE optional)
make help             # every target
```

Tests need a PostgreSQL superuser URL in `PG_ADMIN_URL` (default
`postgresql://postgres@localhost:55432/postgres`). They create and drop their own
`optest_` databases.

```bash
export PG_ADMIN_URL=postgresql://postgres@localhost:5432/postgres
make test-fast        # unit and contract tests
make ci               # lint, typecheck, contract check, every test with coverage gates
```

Each package README explains how to run it by hand: `datagen/README.md`,
`outside_records/README.md`, `bank_agent/README.md` and `contracts/fakes/README.md`.

## Repository rules

- Work on a branch per layer and open pull requests into `main`. Do not change another
  layer's folders without its owner.
- Changes to `contracts/` need all three owners. `CODEOWNERS` becomes active once the
  GitHub handles are filled in.
- Never commit secrets: `.env` and `.env.*` are ignored. Settings come from environment
  variables, listed in each package README.
- No raw loan values (VIN, borrower, amount, date) in logs, errors or anything sent to
  the registry.
- `make ci` must pass before a merge.

## Layer 1 design documents

- `docs/layer1/SPEC.md`: the full layer 1 specification.
- `docs/layer1/SPEC_DECISIONS.md`: binding decisions made during review. Later decisions
  override earlier ones and the SPEC.
- `docs/layer1/RUBRIC.md`: the release rubric layer 1 is reviewed against.
- `docs/layer1/WORK_PACKAGES.md`: the build order.
