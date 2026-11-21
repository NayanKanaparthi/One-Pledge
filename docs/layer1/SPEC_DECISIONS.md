# OnePledge layer 1: SPEC decisions (binding addendum)

Version 1.0, 2026-11-21. Written by the architect after the WP1 hand-back (commits 4e909ac to b48998e).

This file is binding. It resolves questions the implementers raised. Where it differs from `SPEC.md` or from the literal text of a `RUBRIC.md` procedure, this file wins, and reviewers grade against it. `SPEC.md` and `RUBRIC.md` themselves are not edited. Later questions are appended here with the next number; an entry is never rewritten.

Each entry: question, decision, rationale, what it affects. "Ratified" means the WP1 code stays as it is. "Correction" names the work package that makes the change.

## Summary

| # | Topic | Decision | Code change |
|---|---|---|---|
| 1 | Inherent `BORROWER_REF_NORMALISED` on Lakeside rows | Ratified; the answer key must declare inherent fixes | WP1 (small) |
| 2 | Example profile shape | Ratified | none |
| 3 | Location of the example's expected file | Ratified | none |
| 4 | `failed_check_reasons`, `LOCKED_OR_CONFLICT` | Ratified; scorer semantics fixed below | none (WP5 implements) |
| 5 | Client name in the outside records access log | Correction: log key `client`, not `user_id` | WP1 (small) |
| 6 | Scope of `fail-next` | Correction: optional `path` filter | WP1 (small) |
| 7 | Case reuse keyed by claimant member | Ratified | none |
| 8 | Exception class names | One rule: every exception class ends in `Error` | none in WP1; binds WP2 to WP5 |
| 9 | Untyped `jsonschema` and `PyYAML`; checker dependencies | Correction: use the stub packages; runtime dependencies ratified | WP1 (small) |
| 10 | `make setup` and `make env` | Ratified until WP5 | none |
| 11 | CODEOWNERS handles | Correction: no unverified handle in the file | WP1 (small) |
| 12 | Silent choices in the doubles | Ratified; 404 and 405 codes added to the draft contracts | none |
| 13 | Cleaning evaluation order | Ratified and made binding for the bank agent | none in WP1; binds WP2 |
| 14 | Moved rows identical to S01 | Ratified | none |
| 15 | No checkpoint on case resolution | Ratified | none |
| 16 | `integration` marker wording | Ratified | none |
| 17 | Role names derived from URLs | Ratified; same rule for the bank agent | none |
| 18 | Log file path in DATA-06 | Graded at WP5; interim procedure below | none |

---

## 1. Lakeside borrower references always produce a FIXED issue

**Question.** Lakeside references look like `LK-123456`. Cleaning removes the hyphen, so every Lakeside row carries `FIXED BORROWER_REF_NORMALISED`. SPEC 20.4 says fixed rows must match their expected problems exactly. The implementer put fixable messiness on Summit rows only and asks that the scorer allow the inherent fix.

**Decision.** Ratified, with one addition.

- The cleaning rule of SPEC 10.4 stands: removing separators from a borrower reference is recorded as `FIXED BORROWER_REF_NORMALISED`. Every normalisation is on the record.
- A fix that a lender's standard format always causes is an "inherent fix". The answer key declares them at the top level, keyed by facility code:
  `"inherent_fixes": {"HARBOR-LAKESIDE": ["BORROWER_REF_NORMALISED"], "NORTHGATE-LAKESIDE": ["BORROWER_REF_NORMALISED"], "HARBOR-SUMMIT": [], "NORTHGATE-SUMMIT": [], "CALDER-SUMMIT": []}`.
  datagen fills it from the same format definition its oracle already uses (`inherent_fixes` in `tape_format.py`).
- Scorer rule for an accepted row (replaces "equal for fixed rows" in SPEC 20.4): let `E` be the row's expected FIXED problems (empty for a baseline row) and `I` the inherent fixes of the row's facility. The observed FIXED problems `O` must satisfy `E ⊆ O ⊆ E ∪ I`. Any other FIXED issue fails the row as a data quality mismatch. The scorer reads `I` from the answer key and hardcodes no problem name.
- Placing fixable messiness on Summit rows only is accepted. `M_BORROWER_LOWERCASE` stays meaningful there.
- Presentation: the demo narrative and the tape counts report `rows_fixed` as defined in the contract (it includes inherent fixes). The scene 1 text must say in one clause that Lakeside references are normalised on every row, so a large number does not read as bad data.

**Rationale.** Hiding a normalisation to keep a count small would trade auditability for tidiness. Declaring the inherent fixes in the key keeps the scorer strict (an unexpected fix still fails) without a hardcoded exception.

**Affects.** WP1 correction: add `inherent_fixes` to the answer key (and to `verify`). WP5: scorer rule above, scene 1 wording. Rubric: E2E-02, E2E-03, PRES-02. DATA-07 is unaffected (the example is a Summit tape; its committed files do not change).

## 2. Example profile shape

**Question.** The example has no TOTAL row and no trailing blank line, its manifest scene is null and its step id is S01.

**Decision.** Ratified. SPEC 5.6 fixes the example at exactly 20 data rows with a stated composition; a totals row would be a 21st record. `scene: null` and `step_id: "S01"` are fine. Consumers of a manifest must accept a null scene.

**Affects.** WP2 (the example contract test), WP5 (the replay driver must not assume a scene number). DATA-07.

## 3. Where the example's expected file lives in generator output

**Question.** SPEC 5.9 names the committed location but not the generator output location. The implementer used `contract_example/`.

**Decision.** Ratified: for the `example` profile only, the generator writes `contract_example/example_tape_20.expected.json` next to the other output, and `checksums.json` covers it. The committed copies stay where SPEC 5.9 puts them, under `contracts/examples/tapes/`. This file is not an answer key: it holds cleaning and local check expectations for one small tape and may be read by the bank agent's contract test. The name `answer_key` must not appear in it or in its path.

**Affects.** WP2. DATA-07, CODE-03.

## 4. Answer key additions and race rows

**Question.** The key adds `failed_check_reasons` per row. Race rows carry outcome `LOCKED_OR_CONFLICT` with `alert: null`.

**Decision.** Ratified. Scorer semantics, binding for WP5:

- `failed_check_reasons` is a map from check type to reason code. The scorer requires the observed set of FAILed check types to equal `failed_checks` (as SPEC 20.4 says), and, for every entry of `failed_check_reasons`, the observed reason code of that check to equal the expected one. A missing or empty map means reasons are not compared.
- A row with outcome `LOCKED_OR_CONFLICT` is never matched on its own. Its `match_kind`, `match_basis`, kinds lists and `alert` fields are ignored at row level. The case is graded as a whole and is "as expected" only when all of these hold: both rows have disposition accepted and no FAILed check; exactly one row has registry result LOCKED and the other CONFLICT with `match_kind` EXACT and `match_basis` ASSET and all eight kinds matched; the LOCKED row's loan has exactly one alert, role HOLDER, EXACT; the CONFLICT row's loan has exactly one alert, role CLAIMANT, EXACT; both alerts carry the same `registry_case_id`. Anything else (both locked, both in conflict, either row OTHER, a missing or extra alert) is a miss.
- For the false alarm and false hold counts, race rows are rows that are expected to conflict or alert; they are never counted there.
- The scorecard prints, for the race type, how many times each bank won. That is information, not a gate.

**Rationale.** Which bank wins a true race is not determined, so only the pair can be graded. Comparing reason codes where the key gives them makes "caught for the right reason" measurable.

**Affects.** WP5. E2E-02, E2E-03, REL-07, PRES-02.

## 5. Client name in the outside records access log

**Question.** SPEC 6.2 asks the access record for the client name. SPEC 16.5's allow-list has no such key, so the implementer logged the client id under `user_id`.

**Decision.** Correction. SPEC 16.5 is the bank agent's allow-list. Outside records has its own, which is the bank agent's list minus the keys it never uses, plus one key: `client`. The access record carries `client` with the client name (for example `harbor`), never under `user_id`. Client names are restricted to `^[A-Za-z0-9_-]{1,64}$` when a client is added (`clients add` refuses anything else), so the value always passes the value shape rule. A request with no valid key logs `client` as null.

**Rationale.** A client id in a field called `user_id` misleads whoever reads the log. A named, shape-checked key is the same safety with an honest label.

**Affects.** WP1 correction (logging and `clients add` validation, with a test). DATA-06, PRES-05 (reviewers treat `client` as an allowed key in the outside records log).

## 6. Scope of `fail-next`

**Question.** `BEFORE_PROCESSING` applies to every contract call, so a bank agent's outbox poll can consume an injected failure meant for a submission. `AFTER_COMMIT` applies only to `POST /v1/submissions`.

**Decision.** Correction, additive. `POST /_test/fail-next` accepts one more optional field, `"path"`: a contract path template exactly as written in the YAML (for example `/v1/submissions`, `/v1/outbox`, `/v1/certificates/{certificate_id}`, `/v1/evaluate`). When present, only calls to that route consume the failure budget; other calls are served normally. When absent, behaviour is as built (every contract call consumes it). An unknown template is a 422. `AFTER_COMMIT` stays limited to `POST /v1/submissions` and may be combined with `path` only if the path is `/v1/submissions`. `/healthz` and `/_test/` calls never consume the budget.

**Rationale.** WP3's reliability suite must fail "the middle batch of a three batch tape" and "a certificate fetch" deterministically while the outbox poller is running. Without a filter those tests would be timing dependent.

**Affects.** WP1 correction (both doubles, with tests). WP3 (reliability suite uses `path`). REL-02, REL-04 (its procedure uses `AFTER_COMMIT`, which is already scoped), REL-05.

## 7. Case reuse keyed by claimant member

**Question.** The double reuses an OPEN case by (claimant member, claimant asset token, lock), while SPEC 7.2 names only the claimant asset token.

**Decision.** Ratified. The key is (claimant member id, claimant asset token, matched lock id). Two members pledging the same car both produce the same asset token, so the token alone would merge two different banks' conflicts into one case and send one bank the other's case. SPEC 7.2's wording is incomplete; the implementation is right. `registry.yaml` already says "the existing OPEN case for the same claimant and lock".

**Affects.** Nothing further. BANK-06, BANK-08.

## 8. Exception class names (one rule for the whole repository)

**Question.** ruff rule N818 requires exception classes to end in `Error`. SPEC names some without it (`InvalidVin`, `IllegalTransition`, `KeyVersionChanged`, `OutsideRecordsUnavailable`, `KeyHolderUnavailable`, `RegistryUnavailable`).

**Decision.** N818 stays enabled with no per-file ignore and no `noqa`. Every exception class in every package ends in `Error`. The SPEC names map as follows, and these are now the binding names:

| SPEC text | Binding name |
|---|---|
| `InvalidVin` | `InvalidVinError` |
| `IllegalTransition` | `IllegalTransitionError` |
| `KeyVersionChanged` | `KeyVersionChangedError` |
| `OutsideRecordsUnavailable` | `OutsideRecordsUnavailableError` |
| `KeyHolderUnavailable` | `KeyHolderUnavailableError` |
| `RegistryUnavailable` | `RegistryUnavailableError` |
| `KeyHolderProtocolError`, `DomainError` | unchanged |

Any exception class a later package introduces follows the same rule. Each package has one base (`DomainError` in the bank agent and the doubles; `DataGenError` in datagen is ratified as that package's base). Infrastructure and tooling errors that are not business errors (`PortUnavailableError`, `DocumentError`) may derive from `Exception` directly, as built.

Where RUBRIC BANK-04 says the altered VIN "raises `InvalidVin`", reviewers read `InvalidVinError`.

**Rationale.** WP1 already follows the suffix rule in three packages. A lint exception for one package would be the first suppression in the code base and would make names inconsistent across packages. Renaming six names in a document addendum costs nothing.

**Affects.** WP2, WP3, WP4, WP5. BANK-04, CODE-01, CODE-06.

## 9. Untyped `jsonschema` and `PyYAML`; checker dependencies

**Question.** Neither library ships type information. The implementer used `type: ignore[import-untyped]` (four so far) because stub packages were not on the allowed list. Also, `jsonschema` and `openapi-spec-validator` are listed as dev only in SPEC 2.1 but the checker lives in the doubles' `src/`, so they are runtime dependencies there.

**Decision.**

- Correction: `types-PyYAML` and `types-jsonschema` are added to the allowed dev-only packages. Each package that imports `yaml` or `jsonschema` (in `src/` or `tests/`) lists the matching stub package in its `dev` extra, and the four `type: ignore[import-untyped]` comments are removed. I confirmed both stub packages install from the index on the build machine, and that `openapi-spec-validator` ships `py.typed` and needs nothing.
- Ratified: `PyYAML`, `jsonschema` and `openapi-spec-validator` are runtime dependencies of `onepledge_contract_fakes` (the checker is a shipped command). They remain dev only everywhere else.
- The only mypy override in the repository is still `ignore_missing_imports` for `oprf` and `oblivious`.

**Rationale.** RUBRIC CODE-01 allows at most 10 `type: ignore` comments in the whole repository. The bank agent and evaluation contract tests will import both libraries in several more files; spending the budget on imports would leave none for real boundaries, and stubs give actual checking instead of silence.

**Affects.** WP1 correction (two `pyproject.toml` dev extras, four comments). WP2 to WP5 follow the same rule. CODE-01, CODE-05.

## 10. `make setup` does not run `make env`

**Decision.** Ratified for now. `env` is a WP5 target. WP5 completes `setup` as SPEC 2.3 describes (run `make env` when `.env` is missing). Tests in every package must keep working with no `.env` present.

**Affects.** WP5. DX-02, E2E-01.

## 11. CODEOWNERS

**Question.** The implementer wrote `@NayanKanaparthi` from memory. It is not verified. The handles of Shreyas and Vedant are unknown.

**Decision.** Correction. No unverified handle is committed. Until the team confirms all three GitHub handles, `CODEOWNERS` contains comments only:

- a header saying the file is inactive until handles are confirmed, and by whom (Nayan);
- the intended rules, commented out, with role placeholders in plain words and no `@`: for example `# /bank_agent/   <layer 1 owner: Nayan>` and `# /contracts/   <layer 1 owner> <layer 2 owner: Shreyas> <layer 3 owner: Vedant>`;
- one line telling the reader to replace each placeholder with the verified handle and remove the `#`.

**Rationale.** GitHub rejects or silently ignores rules with unknown owners, and a wrong handle would route reviews to a stranger or to nobody while looking valid. A comment-only file is valid and honest. A guessed name is the kind of unverified claim the CONTEXT standard forbids.

**Open item for Nayan.** Supply the three GitHub handles. Activating the file is then a three line change.

**Affects.** WP1 correction. DX-01 (documents state only what is true).

## 12. Choices SPEC was silent on, in the doubles and outside records

| Choice | Decision |
|---|---|
| `PORT` has no default | Ratified. The launcher and compose always set it. A missing `PORT` stops startup with one line naming the variable. The bank agent follows the same rule in WP2. |
| `FAKE_TEST_CONTROL_KEY` must be at least 32 characters | Ratified. `env init` in WP5 generates 43 url-safe characters. |
| Request bodies to the doubles limited to 1 MiB | Ratified. The largest legal bodies are far below it (500 item submission about 0.35 MiB; 1,000 elements about 0.05 MiB). Over the limit is 413 `PAYLOAD_TOO_LARGE`. |
| Unknown route gives 404 `NOT_FOUND`, wrong method gives 405 `METHOD_NOT_ALLOWED` | Ratified, and made a rule for every layer 1 service: no framework default body ever reaches a caller. I added `NOT_FOUND` and `METHOD_NOT_ALLOWED` to the `ErrorResponse` code enums of the draft `key_holder.yaml`, `registry.yaml` and `outside_records.yaml`, and `METHOD_NOT_ALLOWED` to `bank_agent.yaml` (which already had `NOT_FOUND`). This is an additive change to a draft contract; `onepledge-contracts check` still passes. Fixed messages: "The requested resource does not exist." and "This method is not allowed on this resource." |

**Affects.** WP2 (settings rule), WP4 (bank agent handlers for unknown route and wrong method, 405 with an `Allow` header), WP5 (`env init`). PRES-04, DX-05. Shreyas and Vedant should be told of the two new error codes when the contracts go for approval.

## 13. Cleaning evaluation order

**Question.** SPEC 10.4 says field rules are evaluated for every field with no stop at the first problem, but does not order the row rules against the field rules. The datagen oracle stops field evaluation on `NON_DATA_ROW` and `COLUMN_COUNT_MISMATCH`, and stops a field at `UNSAFE_CHARACTERS`.

**Decision.** Ratified and binding for the bank agent's `clean_row` in WP2, which must produce the same issues as the oracle. The full order:

1. Row rules first, in this order: `NON_DATA_ROW` (all cells empty, or the first cell is a totals word), then `COLUMN_COUNT_MISMATCH`. The first that applies rejects the row with exactly that one issue (field `row`). No field rule is evaluated. A totals row with too few cells is therefore `NON_DATA_ROW`, not a count mismatch.
2. Otherwise every required field is evaluated independently, so one row can carry several issues across fields.
3. Within one field the steps run in order and the first REJECTED issue ends that field: trim (records `WHITESPACE_TRIMMED` if it changed the cell); empty gives `MISSING_REQUIRED_FIELD`; unsafe leading character or control character gives `UNSAFE_CHARACTERS`; then the field's own normalisations and validations in the order SPEC 10.4 lists them. FIXED issues recorded before a REJECTED one in the same field are kept.
4. The file level rules of SPEC 10.5 (duplicate VIN, existing loan lookups) apply only to rows that passed steps 1 to 3.

Consequences reviewers should expect: a cell `=1+1` in the loan number yields `UNSAFE_CHARACTERS` only (not also `LOAN_NUMBER_INVALID`); an empty VIN yields `MISSING_REQUIRED_FIELD` only (not also a length problem). RUBRIC BANK-01 lists each expected problem as "among" the row's problems, so it holds unchanged.

**Rationale.** Evaluating field rules on a totals row or on a row whose cells are shifted would produce misleading issues. Stopping a field at its first rejection avoids cascades of meaningless secondary errors while still showing every field that is wrong. datagen and the bank agent implement the rule independently; they must agree on it for the scorecard to be honest.

**Affects.** WP2. BANK-01, DATA-07 (the committed expected file was generated under this order), E2E-02.

## 14. Moved rows are identical to their S01 rows, balance included

**Decision.** Ratified. SPEC 5.7 says the loan is "pledged unchanged". The balance is neither tokenised nor checked, so an unchanged balance six weeks later has no effect on any outcome. It is slightly unrealistic and is not worth a generator change.

**Affects.** Nothing.

## 15. Resolving a case does not sign a checkpoint

**Decision.** Ratified. SPEC 7.2 signs a checkpoint after each submission; it does not require one after a position based resolution. `CASE_RESOLVED` events are covered by the next checkpoint. `POST /_test/checkpoint` exists for a test that needs one sooner. The bank side never relies on a checkpoint to learn a case outcome (that arrives as a `CASE_UPDATE` message).

**Affects.** WP3, WP5: no test or scene may assume a new checkpoint right after a case resolution. SEC-12 is unaffected (its tamper modes sign their own checkpoint).

## 16. `integration` marker wording

**Decision.** Ratified. The marker now reads as covering tests that touch PostgreSQL, the file system or child processes. `unit` remains "no I/O at all". Later packages use the markers the same way.

**Affects.** TEST-01.

## 17. `db provision` derives role names from the two URLs

**Decision.** Ratified: the command takes the owner and app role names and passwords from `MIGRATION_DATABASE_URL` and `DATABASE_URL`, and refuses to run unless they are `{db}_owner` and `{db}_app` for the database named in the URL. `onepledge-bank db provision` in WP2 works the same way, so the naming rule of SPEC 3.2 is enforced in one place per service rather than restated in configuration.

**Affects.** WP2, WP5 (the launcher builds URLs that satisfy this). SEC-02.

## 18. DATA-06 expects `var/log/outside-records.jsonl`

**Decision.** No change to the rubric. That file is written by the WP5 stack launcher, and DATA-06 is graded in full at the WP5 review, as `WORK_PACKAGES.md` already provides. For the WP1 review the reviewer starts `onepledge-outside serve` by hand with stdout redirected to a file and runs the same grep on that file; every other part of DATA-06 is graded now.

**Affects.** WP1 review procedure only.

---

## WP1 corrections in one list

All are small and none changes generated data or committed example files.

1. Answer key: add the top-level `inherent_fixes` map by facility code; `verify` checks it (decision 1).
2. Outside records: access record uses key `client` with the client name; `clients add` enforces the name pattern (decision 5).
3. Doubles: optional `path` filter on `/_test/fail-next` (decision 6).
4. Typing: add `types-PyYAML` and `types-jsonschema` to the dev extras, remove the four `type: ignore[import-untyped]` comments (decision 9).
5. `CODEOWNERS`: comments only, no handle, until the team confirms them (decision 11).

The contract enum additions of decision 12 are already made in this commit.
