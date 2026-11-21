# onepledge-datagen: seeded synthetic data for layer 1

From one integer seed and a profile, `onepledge-datagen` writes everything the demo and
the scorecard need: the lenders' tape files exactly as a lender would send them, the
outside records (titles, payments, contracts), the reference data (banks, lenders,
facilities, demo users), the timeline of tape submissions, and the answer key. All data
is invented. Only `onepledge_evaluation` reads the answer key.

## Commands

```
onepledge-datagen generate --seed INT --profile {full,small,example} --out DIR [--force]
onepledge-datagen verify --data-dir DIR
```

`generate` builds the data set in memory, renders every file, parses the rendered bytes
back and checks every invariant of SPEC 5.8 on them before anything is written. It
refuses a non-empty `--out` unless `--force` is given, and `--force` only replaces a
directory that already holds a data set. It prints the steps with their row counts, the
outside record counts, the number of planted cases and data quality rows, and the
number of tape rows the invariants were checked over.

`verify` re-checks an existing directory: every file against `checksums.json` (and no
file that is not listed), then every invariant. It exits 1 with the list of violations.

From the repository root, `make data` generates `var/data/$(PROFILE)-$(SEED)` (defaults
`full` and `20261117`) and skips when that directory already verifies; `FORCE=1`
regenerates.

## Output

```
DIR/
  manifest.json          timeline of tape submissions (no fraud labels)
  reference.json         banks, lenders, facilities, demo users (no passwords)
  checksums.json         SHA-256 of every other file
  tapes/<BANK_CODE>/<STEP>_<FACILITY_CODE>_<KIND>.csv
  outside_records/titles.csv  payments.csv  contracts.csv
  answer_key/answer_key.json
  contract_example/      example profile only: the files committed under contracts/examples/tapes/
```

Profiles (SPEC 5.6): `full` holds 4,700 honest baseline loans and every planted case of
SPEC 5.7 (about 5,000 distinct VINs over 15 tapes); `small` is the same timeline at about
a tenth of the size; `example` is one 20 row Harbor pledge tape for the contract example.

Tape formats (SPEC 5.9): Summit Auto Finance writes UTF-8 with a BOM, CRLF line ends,
`MM/DD/YYYY` dates, quoted `$23,450.17` amounts, a trailing `TOTAL` row and a trailing
blank line. Lakeside Motor Credit writes UTF-8 without a BOM, LF line ends, ISO dates and
plain `23450.17` amounts. Release tapes fill only the VIN and loan number cells. The
example tape has no totals row and no trailing blank line, so it holds exactly 20 data
rows.

The answer key lists every planted case with, per row, the role, location and the
expected disposition, cleaning problems, outcome, failed checks and their reasons, match
kind and basis, kinds that must and must not match, and the alert. `failed_check_reasons`
is an addition to the SPEC 5.10 sketch so that a reason such as
`CONTRACT_TERMS_MISMATCH` is checked too. Data quality rows are listed under `messiness`.
Every row not named is baseline: accepted, locked, no alert.

## Updating the committed example

```
onepledge-datagen generate --seed 20261117 --profile example --out /tmp/op_ex --force
cp -r /tmp/op_ex/contract_example/* contracts/examples/tapes/
```

A test regenerates the example and fails if the committed files differ by one byte.

## How correctness is checked

`domain/oracle/` is an independent implementation of the rules of SPEC sections 9 to 14
(cleaning, the four local checks, the registry rules A, B and C, releases and alerts). The
invariant checker walks every tape of the timeline through it, in both orders for the
concurrent S14 and S15 pair, and compares the result of every row with the answer key.
It also checks check digits, repeated VINs, case wiring, row counts and that no label
word appears outside the answer key. The bank agent shares no code with the oracle, so
each checks the other.

## Layout

```
src/onepledge_datagen/
  domain/          entities, catalog (banks, lenders, facilities, steps), VIN rules, money,
                   calendar, amortisation, tape formats, seeded random streams, errors,
                   oracle/ (cleaning, checks, lender names, registry, simulation)
  application/     workspace, factories, planting kit, portfolio, planters/ (one class per
                   case type), messiness, layout, records, reference, answer key, profiles,
                   view, expectations, invariants, generate, verify, example contract
  infrastructure/  CSV formats, JSON, the file codec and the data directory
  interface/       cli.py and the console output helper
tests/
```

All randomness comes from `random.Random(f"{seed}:{stream}")` streams; there is no
clock, no `uuid4` and no OS randomness, so the same seed always gives the same bytes.

## Configuration

None. The generator reads no environment variable.

## Tests

```
python3 -m pytest datagen -q
```

The tests cover the VIN rules (with Hypothesis properties), money, calendar and
amortisation, every cleaning rule of SPEC 10.4 in both formats, the check boundaries,
the registry rules, determinism and seed sensitivity, the exact case counts of both
profiles, the file formats, the absence of labels, the committed example, independent
construction checks of every case type, and `verify` on tampered copies.
