# OnePledge

OnePledge is a neutral cross-bank check that a non-bank lender has not pledged the same
loan (for example the same car loan) to two banks as collateral. That is the failure
behind the Tricolor, First Brands and MFS cases. Each bank keeps its own loan tapes. Only
scrambled codes, made through two independent key holders, ever reach the shared
registry, so no bank sees another bank's book and the registry sees no raw loan data.

NYU Tandon MG-GY 8503 FinTech, Fall 2026. Team: Nayan Kanaparthi (layer 1), Shreyas
Vyamajala (layer 2), Vedant Chawardol (layer 3). The team plan is
[docs/BUILD_PLAN.md](docs/BUILD_PLAN.md).

## Status (read this first)

| Layer | Owner | Folders | State |
|---|---|---|---|
| 1: bank side and data | Nayan | `datagen/`, `outside_records/`, `bank_agent/`, `evaluation/` | WP1 to WP3 built, WP4 and WP5 not started |
| 2: key holders, registry, ledger verifier | Shreyas | `key_holder/`, `registry/`, `ledger_verifier/` | not started |
| 3: console, infra, end to end | Vedant | `console/`, `infra/`, `e2e/`, `docs/` | not started |
| shared contracts | all three | `contracts/` | DRAFT, needs Shreyas and Vedant approval |

What layer 1 has today:

- `datagen/`: deterministic synthetic data sets (lenders, banks, loan tapes, title and
  payment records) with an answer key, and `verify`, which regenerates and compares.
- `outside_records/`: the title and payment records service the bank agent calls.
- `bank_agent/`: the program that runs inside one bank. Intake, clean, check against
  outside records, tokenise through two key holders, submit to the registry, verify
  signed certificates and checkpoints, handle alerts and case updates from the registry
  outbox, release, and an integrity report. Command line only for now.
- `contracts/`: OpenAPI 3.1 drafts for every service, examples, test vectors, a contract
  checker, and test doubles of the key holder and the registry.
- `tools/`: shared test tooling (scratch databases, unit isolation, repository rules).

Not built yet in layer 1:

- WP4: the bank agent HTTP API of `contracts/bank_agent.yaml`, login, users and the
  permission matrix. Until it exists, layer 3 builds the console against the contract
  and its examples.
- WP5: evaluation scorecard, demo runner, stack launcher, Docker Compose for the whole
  stack, `THREAT_MODEL_L1.md`.

Last full run (`make ci`): lint, mypy strict, contract check and 4,828 tests pass, with
96 to 98 percent coverage per package. Independent reviews of the last few rounds
found only edge cases; the open review items will be fixed in later pushes.

## For Shreyas (layer 2)

- Your contracts are `contracts/key_holder.yaml` and `contracts/registry.yaml`. Read
  `contracts/README.md` first, then propose changes there (additive only once approved).
- `contracts/fakes/` holds runnable test doubles of the key holder (real ristretto255
  OPRF) and the registry (lock rules, cases, Ed25519 signed certificates, hash chained
  events, signed checkpoints, outbox, idempotent replay). They are a reference for the
  behaviour the bank agent expects, not production code. Your services must pass the
  same examples in `contracts/examples/` and the vectors in `contracts/examples/vectors/`.
- The bank agent's registry and key holder clients are in
  `bank_agent/src/onepledge_bank_agent/infrastructure/http/`. Point them at your services
  instead of the doubles to test end to end.

## For Vedant (layer 3)

- The console talks to the bank agent through `contracts/bank_agent.yaml` and to the
  registry read endpoints in `contracts/registry.yaml`. Build against the examples in
  `contracts/examples/` until WP4 lands.
- Each layer 1 service has a Dockerfile (`outside_records/`, `contracts/fakes/`); the
  stack compose file is yours in `infra/`.

## Getting started

Needs Python 3.12 or later and PostgreSQL 16.

```bash
git clone <this repository> && cd onepledge
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

Each package README says how to run it by hand: `datagen/README.md`,
`outside_records/README.md`, `bank_agent/README.md`, `contracts/fakes/README.md`.

## Repository rules

- Work on a branch per layer and open pull requests into `main`. Do not commit to
  another layer's folders without its owner.
- `contracts/` changes need all three owners. `CODEOWNERS` becomes active once the
  GitHub handles are filled in.
- Never commit secrets: `.env` and `.env.*` are ignored. Settings come from environment
  variables, listed in each package README.
- No raw loan values (VIN, borrower, amount, date) in logs, errors or anything sent to
  the registry.
- `make ci` must pass before a merge.

## Layer 1 design documents

- `docs/layer1/SPEC.md`: the full layer 1 specification.
- `docs/layer1/SPEC_DECISIONS.md`: binding decisions made during review rounds. Later
  decisions override earlier ones and the SPEC.
- `docs/layer1/RUBRIC.md`: the release rubric layer 1 is reviewed against.
- `docs/layer1/WORK_PACKAGES.md`: the build order (WP1 to WP5).
