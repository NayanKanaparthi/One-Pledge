# onepledge-datagen: seeded synthetic data for layer 1

From one integer seed and a profile, `onepledge-datagen` writes everything the demo and
the scorecard need: the lenders' tape files exactly as a lender would send them, the
outside records (titles, payments, contracts), the reference data (banks, lenders,
facilities, demo users), the timeline of tape submissions, and the answer key. All data
is invented. Only `onepledge_evaluation` reads the answer key.

## Commands

```
onepledge-datagen generate --seed INT --profile {full,small,example} --out DIR [--force] [--json] [--quiet] [--debug]
onepledge-datagen verify --data-dir DIR [--json] [--quiet] [--debug]
```

With `--json`, stdout holds exactly one JSON document: `{"ok": true, ...}` with the facts
of the text output (steps and their rows, outside record counts, cases, data quality rows,
rows checked, files), or `{"ok": false, "error": {"code": ..., "message": ...}}` with the
same sentence on stderr. Two codes carry a list of problems (SPEC_DECISIONS 66):

| Code | Command | Meaning |
|---|---|---|
| `VERIFICATION_FAILED` | `verify` | A data set on disk fails a check. `violations` lists each failure as `{"stage", "problem"}`, the stage being `CHECKSUM` (a file against `checksums.json`), `INVARIANT` (a SPEC 5.8 invariant or the contract example) or `REGENERATION` (the comparison with the data set its seed and profile generate). |
| `INVARIANTS_VIOLATED` | `generate` | The generator's own pre-write invariant check failed: a defect of the generator. Nothing is written; `violations` lists the invariants as sentences. |

Every failure is one sentence and exit 1, including a path the file system refuses ("Cannot
write the data set to PATH: permission denied; choose another --out.") and a malformed
data set file, which is named with what is wrong ("outside_records/payments.csv line 2
column amount_cents is not a positive whole number of cents."). JSON files are read with
the bounded parser of SPEC_DECISIONS 74 (`manifest.json: nests too deeply.`,
`manifest.json: not valid JSON.`); a path read from a file must stay inside the data set
(`manifest.json names a path outside the directory.`), and a file that is a symbolic link
out of it is refused unread. An unexpected error
prints `Unexpected error. Run with --debug for details.`; `--debug` adds the exception
type and its frames (file, line, function), never the exception message, a source line or
a local variable. No output holds a Python repr: lists are comma separated, alerts are
written `ROLE MATCH_KIND`, an empty list is `none`. The generator writes no log lines;
`--quiet` is kept for one interface across layer 1 and its help says it has no effect.
`--seed` is ASCII digits only, 0 to 999,999,999,999,999,999; an empty `--out` or
`--data-dir` is a usage error (exit 2).

`generate` builds the data set in memory, renders every file, parses the rendered bytes
back and checks every invariant of SPEC 5.8 on them before anything is written. It
refuses a non-empty `--out` unless `--force` is given, and `--force` only replaces a
directory that already holds a data set. It prints the steps with their row counts, the
outside record counts, the number of planted cases and data quality rows, and the
number of tape rows the invariants were checked over.

`verify` checks an existing directory in two stages. First every file against
`checksums.json` (and no file that is not listed), then every invariant of SPEC 5.8.
Then conformance (SPEC_DECISIONS 43.2): it regenerates the data set in memory from the
manifest's `seed` and `profile` and requires every file on disk, `manifest.json`,
`reference.json` and the answer key included, to be byte identical to the regenerated
one; each file that differs is named ("answer_key/answer_key.json differs from what seed
20261117, profile full generates"), as is a missing or an extra file. A manifest whose
`seed` or `profile` is missing or invalid is reported as such. A data set written by
another `generator_version` cannot be regenerated, so `verify` says so in one sentence and
exits 1. It exits 1 with the list of problems.

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

Reserved whole-dollar loans: to plant the `23450` and `$23,450` spellings of
`M_AMOUNT_ALT_FORMAT` without changing an amount, the generator reserves a deterministic
set of honest loans whose original amount and balance have zero cents; this departs from
SPEC 5.4 ("plus uniform random cents 0 to 99") for those loans only, because a whole
dollar spelling of an amount with cents would change the amount and break the contract
match (SPEC_DECISIONS 39), and `verify` checks the forms rotate.

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

The top-level `inherent_fixes` map lists, per facility code of the profile, the FIXED
problems that facility's lender format causes on every row (Lakeside references such as
`LK-123456` always give `BORROWER_REF_NORMALISED`; Summit causes none). It is filled from
the same format definition the oracle uses, and `verify` refuses a key whose map omits a
facility or differs from the format (SPEC_DECISIONS 1). An accepted row is as expected when
its FIXED problems include the expected ones and add nothing outside its facility's
inherent fixes.

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
each checks the other, and no technique either (SPEC_DECISIONS 50): the oracle's cleaning
module has no regular expression; it walks each cell, classifies characters against
literal ASCII sets and builds values by arithmetic. Digits are ASCII digits only, trimming
removes ASCII space, tab, CR and LF only, a borrower reference loses only the separators
space, `-`, `_`, `.` and `/`, and only ASCII letters change case (SPEC_DECISIONS 45). Its
tests run every case of the neutral table `contracts/examples/cleaning_cases.json`, which
none of the implementations produced. `domain/text_values.py` is the one place text
becomes an integer or a date; an architecture test forbids `int()`, `isdigit` and its
kin, the `\d` class and `fromisoformat` anywhere else.

## Layout

```
src/onepledge_datagen/
  domain/          entities, catalog (banks, lenders, facilities, steps), VIN rules, money,
                   calendar, amortisation, tape formats, seeded random streams, errors,
                   text_values (integers and dates from text), bounded_json,
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
construction checks of every case type, every case of `contracts/examples/cleaning_cases.json`,
and `verify` on tampered copies, including every tampering that keeps the data set
consistent and is found only by the regeneration.
