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

---

# Round 2 (2026-11-21): WP1 independent review 1, and WP2 questions

Inputs: `docs/layer1/reviews/WP1-review-1.md` (commit `7c4e115`), and the WP2 implementer's questions (commits 2a921a4 to ffcf40d). Decisions 19 to 33 follow the same format. Lists of required changes are at the end of this round.

Rules that apply to every layer 1 service from now on are marked **(all services)**. WP2 to WP5 implement them in the bank agent without a further ruling.

## 19. Does grading rule 8 attach defects found outside a procedure to DATA-06 and DX-05?

**Question.** DATA-06 and DX-05 pass every literal step. The reviewer failed them for defects found by going further: outside records answers 500 to malformed integers, and the registry double deviates from `registry.yaml` in two places.

**Decision.** Confirmed. Both fail.

- DATA-06 requires the service to be "contract conformant". A 500 on malformed input breaks BUILD_PLAN section 7 ("validate every input at the service edge") and the status list in `outside_records.yaml`. DATA-06 is the closest criterion.
- DX-05 cites SPEC 7. SPEC 7.2 and CONTEXT require the doubles to implement the contract semantics exactly. A double that answers with the wrong match, or with 500 on a float, is not the executable reference DX-05 is there to protect. DX-05 is the closest criterion.

General reading of rule 8, binding for all reviews: a reproducible defect that violates BUILD_PLAN, CONTEXT, SPEC or this file fails the closest criterion even when every literal step of that criterion's procedure passes. The reviewer names the criterion and says in one line why it is the closest. A finding that violates none of those documents is a quality finding: it does not fail a criterion, and the architect rules on it (as in decision 22).

**Affects.** WP1 fixes F1 to F3 below. All later reviews.

## 20. Is `--json` binding on the WP1 command line tools?

**Decision.** Binding, with a precise scope. **(all services)**

- Every command that prints a result has `--json`: `onepledge-datagen generate` and `verify`; `onepledge-contracts check`; `onepledge-outside db provision`, `db migrate`, `seed`, `clients add`; and every `onepledge-bank` and `onepledge-eval` command that is not listed as exempt.
- Exempt: commands that run until stopped and print no result (`serve` of every service, `worker` without `--once`, `stack up` while it waits). They reject `--json` as a usage error (exit 2).
- With `--json`, stdout carries exactly one JSON document and nothing else, on success and on failure. On failure the document is `{"ok": false, "error": {"code": "...", "message": "..."}}` and the exit code is unchanged; the one sentence message still goes to stderr. On success the document has `"ok": true` and the same facts the text output shows (counts, paths, ids, per check results).
- Logs never share stdout with a result (decision 27).

**Rationale.** SPEC 19 says "all commands". The harness in WP5 and any CI job will parse these outputs; a tool that is machine readable only in one package is not finished.

**Affects.** WP1 fix F5. WP2 to WP5. PRES-03.

## 21. Marker greps that hit the fixed documents

**Question.** The literal marker greps of CODE-04 and PRES-06 print lines from the governing documents, because those documents name the forbidden words.

**Decision.** The governing documents are excluded from the marker greps (the `TODO|FIXME|XXX|HACK|TBD|lorem` grep of CODE-04, and the `TODO|TBD|FIXME|lorem|coming soon|placeholder` grep of PRES-06). The exact exclusion list:

```
docs/BUILD_PLAN.md
docs/layer1/CONTEXT.md
docs/layer1/SPEC.md
docs/layer1/RUBRIC.md
docs/layer1/WORK_PACKAGES.md
docs/layer1/SPEC_DECISIONS.md
docs/layer1/reviews/          (every file under it)
```

Nothing else is excluded. In particular `README.md`, the package READMEs, `ARCHITECTURE.md`, `THREAT_MODEL_L1.md`, `contracts/README.md`, every source, test, YAML, TOML file and the Makefile are still scanned, and a hit there fails. A product file that must name one of these words to do its job (for example a pre-commit hook pattern) is not exempt: write the pattern so the grep does not match it (a character class such as `TO[D]O`).

The em dash checks exclude nothing.

**Affects.** CODE-04 and PRES-06 procedures at every review. No product change.

## 22. The eleven quality findings of WP1 review 1

Default applied: a bank-grade build fixes them. All eleven are fixed; none is accepted as it is.

| # | Finding | Ruling |
|---|---|---|
| 1 | Unknown field names echoed in error details | **Fix (WP1).** It violates SPEC 4. Rule **(all services)**: every `details[].field` value comes from a closed set defined by the contract (schema property names, parameter names, with array indexes such as `items[3].asset_token`). A name invented by the client is never returned. An unknown body key is reported once as `{"field": "body", "issue": "UNKNOWN_FIELD"}`, an unknown query parameter as `{"field": "query", "issue": "UNKNOWN_PARAMETER"}`. A test sends a canary as a key and asserts it is absent from the response. |
| 2 | Oracle payment window edges untested where they are implemented | **Fix (WP1).** Add unit tests on the oracle's own window filter (`invariants.py`): a payment on the first day of the window counts, on the last day counts, one day before the first and one day after the last do not. The mutation the reviewer used (excluding either edge) must then fail a test. |
| 3 | Unhandled errors logged twice | **Fix (WP1).** One failure, one `error` line. The service's own handler logs it; the duplicate from `uvicorn.error` is suppressed. Rule **(all services)**. A test raises inside a route and counts exactly one `error` record. |
| 4 | `hmac.compare_digest` that compares a value with itself | **Fix (WP1).** Remove the call in outside records and say in the docstring what the control actually is: the stored value is a SHA-256 of a 256 bit random key and the lookup is an indexed equality on that hash. Code must not claim a control it does not exercise. SPEC 4's `compare_digest` requirement applies where a service compares in memory (the doubles compare against a configured list: they compare against every entry with `compare_digest` and do not stop at the first match). |
| 5 | README says `serve` never reads the owner URL, but settings parse it | **Fix the code (WP1), keep the README sentence.** Rule **(all services)**: settings are scoped to the command. `serve` (and `worker`) build a settings object that has no field for `MIGRATION_DATABASE_URL` or `PG_ADMIN_URL`; only `db`, `seed`, `seed-reference` and `clients` commands parse the ones they need. A test builds the serve settings from an environment that contains both variables and asserts neither value is reachable from the object. WP5's launcher must not put either variable into a serving process's environment (RUBRIC SEC-02 checks this for the bank agent). |
| 6 | Two service names | **Fix (WP1).** Rule **(all services)**: the `service` value in logs is the contract's health value: `outside_records`, `registry`, `key_holder`, `bank_agent`. The hyphenated names (`outside-records`, `registry-fake`, `keyholder-1-fake`, `bank-harbor`) are launcher process names, used only for pid files, log file names and compose services. |
| 7 | Different issue codes for the same mistake | **Fix (WP1).** One vocabulary **(all services)**, used in `details[].issue`: `REQUIRED` (missing), `INVALID_TYPE` (wrong JSON type), `INVALID_INTEGER` (text that is not an integer), `OUT_OF_RANGE` (a well formed number or length outside its bounds, including negative, zero below a minimum of 1, and too large), `PATTERN_MISMATCH`, `INVALID_VALUE` (not in an enum), `INVALID_DATE`, `TOO_LONG`, `TOO_SHORT`, `TOO_MANY_ITEMS`, `TOO_FEW_ITEMS`, `DUPLICATE`, `UNKNOWN_FIELD`, `UNKNOWN_PARAMETER`, plus the specific codes a contract names (`NOT_A_VALID_POINT`, `DUPLICATE_ASSET_TOKEN`). So `offset=-1`, `limit=0`, `limit=201` and `after_seq=-1` are all `OUT_OF_RANGE`; `limit=abc` is `INVALID_INTEGER`. The existing codes of the doubles are renamed to these. |
| 8 | Checker prints a Python exception name and reports one broken file twice | **Fix (WP1).** No output of any tool names a Python class. When a vector cannot be recomputed because a file it depends on is already reported invalid, the checker prints one line: `vectors/signatures.json: not checked, depends on registry/certificate.response.json which is invalid`, and counts one problem, not two. |
| 9 | `TapeFormatError` derives from `Exception` | **Fix (WP1).** Derive it from `DataGenError` (decision 8). |
| 10 | Three undocumented private methods in the oracle simulation | **Fix (WP1).** One line docstring each. The simulation is the independent oracle the answer key rests on; it should read completely. |
| 11 | `M_AMOUNT_ALT_FORMAT` never uses the plain form without decimals | **Fix (WP1).** The three forms of SPEC 5.7 (`23450`, `$23,450`, `23450.5`) are assigned in rotation by row index within the kind, so every profile that plants at least three rows of this kind has all three, and `verify` checks it. This changes generated output: set `generator_version` to `1.0.1`. The committed example files under `contracts/examples/tapes/` must stay byte identical (the example has no row of this kind); if they do not, regenerate and commit them in the same change and say so. |

Finding 12 is decision 21.

**Affects.** WP1 fixes F6 to F16. The rules marked (all services) bind WP2 to WP5. SEC-08, PRES-03, PRES-04, PRES-05, TEST-04.

## 23. Defects the review found under DATA-06, DX-05, PRES-03, PRES-06

Stated here so the required behaviour is unambiguous.

**(a) Integer parameters (all services).** A query or path integer is accepted only if its text matches `^[0-9]{1,18}$` (ASCII digits only; a sign, a space, a non-ASCII digit or more than 18 digits is `INVALID_INTEGER`), and is then checked against its bounds (`OUT_OF_RANGE`). `offset` has an upper bound of 1,000,000 everywhere. Nothing unvalidated reaches a SQL parameter. Tests send: `9223372036854775808`, `-1`, `+1`, ` 1`, `1.0`, `1e3`, a superscript two, an Arabic-Indic digit, an empty value, and a repeated parameter (`limit=1&limit=2`, which is `VALIDATION_ERROR` with issue `DUPLICATE`). None may produce a 500.

**(b) JSON types (all services).** Request models are strict: an integer field rejects `1.0`, `true` and `"1"`; a string field rejects numbers; a boolean rejects `0` and `1`. The answer is 422 `VALIDATION_ERROR` with `INVALID_TYPE`. A float anywhere in a request body is rejected before any hashing. Tests cover `schema_version` as `1.0`, `true` and `"1"` on the registry double, and the same class of input on every request model.

**(c) Repeated conflict in the registry double.** When the same claimant conflicts again with the same lock while the case is OPEN, the item result carries the `match_kind`, `match_basis` and `kinds_matched` computed for this item against the lock, exactly as `registry.yaml` words them. The stored case keeps the values it was opened with, the same `case_id` is returned, no event is appended and no new alert is queued (SPEC 7.2). The certificate item is HELD. A test submits an identical copy and then a copy with different amount and date codes, in both orders, and asserts each result.

**(d) No traceback, ever (all services).** Every command catches `OSError` (and its subclasses) around file and directory work and prints one sentence naming the path and the problem ("cannot write to PATH: it is a file", "cannot create PATH: permission denied"). The top level of every CLI has a last resort handler that prints `Unexpected error. Run with --debug for details.` to stderr and exits 1; `--debug` prints the traceback. Tests cover an output path under a file, a read-only directory, and a missing parent.

**(e) Plurals.** Every fixed message that contains a count uses the right number ("1 invariant violation", "2 invariant violations"). One small helper per package does this; no message is assembled with a bare `s`.

**Affects.** WP1 fixes F1 to F4, F16. WP4 implements (a), (b), (d), (e) in the bank agent. DATA-06, DX-05, PRES-03, PRES-06, SEC-07, SEC-08.

## 24. Owner `TRUNCATE loans_raw` fails with 0A000, not OP001

**Question.** PostgreSQL refuses to truncate a table that a foreign key references before any `BEFORE TRUNCATE` trigger runs. `loans_raw` is referenced by `cleaning_issues.raw_id` and `submission_items.raw_id`. RUBRIC SEC-01 expects OP001 for every owner statement.

**Decision.** Keep the foreign keys. Do not replace them with constraint triggers. The SEC-01 pass condition is read as follows:

- For the owner role, `UPDATE` and `DELETE` on each of the nine insert-only tables fail with SQLSTATE `OP001`.
- For the owner role, plain `TRUNCATE t` fails with `OP001`, or with `0A000` when the table is referenced by a foreign key (today only `loans_raw`).
- For every table where the plain `TRUNCATE` failed with `0A000`, the reviewer also runs `TRUNCATE t CASCADE` as the owner; it must fail with `OP001` (the statement level triggers on the table and on the referencing tables fire).
- Row counts are unchanged after every statement. The app role conditions are unchanged (all 42501).

WP2 adds an integration test for each of these, including `TRUNCATE loans_raw CASCADE`, and the integrity control `LOANS_RAW_IMMUTABILITY_TRIGGER_ENABLED` stays as specified.

**Rationale.** A declared foreign key is a stronger, more inspectable guarantee than a hand written constraint trigger, and the table cannot be emptied either way. Trading referential integrity for a uniform error code would weaken the schema to satisfy the wording of a test.

**Affects.** WP2 (test only). SEC-01.

## 25. Raw rows must be verbatim and reproducible

**Question.** `raw_payload` is a jsonb object, so cell order is lost, the "first cell" and column count rules cannot be applied faithfully, and a row is not "exactly what the lender sent".

**Decision.** This supersedes SPEC 10.2, the `loans_raw` and `tape_submissions` rows of SPEC 12.1 where they differ, the raw data step of SPEC 15.6, and the hash recomputation in RUBRIC BANK-02.

Storage:

| Table | Column | Content |
|---|---|---|
| `tape_submissions` | `has_bom boolean not null` | True when the file began with a UTF-8 byte order mark. |
| `tape_submissions` | `header_text text not null` | The exact decoded text of the header record, including its line terminator (BOM excluded). |
| `tape_submissions` | `header_cells jsonb not null` | JSON array of the header cell texts in file order, exactly as parsed (nothing trimmed). |
| `tape_submissions` | `trailer_text text not null` | Any text after the last data record (blank lines). Empty string when there is none. |
| `loans_raw` | `raw_text text not null` | The exact decoded source text of this record: everything from the end of the previous record (or of the header) to the end of this record's line terminator. Blank lines before the record are therefore part of it. A quoted cell with an embedded line break stays inside one record. |
| `loans_raw` | `raw_cells jsonb not null` | JSON array of this record's cell texts in file order, exactly as parsed. Check: `jsonb_typeof(raw_cells) = 'array'`. |
| `loans_raw` | `raw_payload jsonb not null` | Kept. The object of SPEC 10.2 (header text to cell text, `__extra_n` for surplus cells), now a derived convenience for display. |

All five new columns are covered by the insert-only trigger (`loans_raw`) or by the tape guard trigger (`tape_submissions`: the four new columns are immutable, SQLSTATE `OP004`).

Rules:

1. Reconstruction: `(BOM bytes if has_bom) + utf8(header_text + raw_text of every row in row_number order + trailer_text)` equals the uploaded bytes exactly, so its SHA-256 equals `file_sha256`. Intake asserts this before committing; if it does not hold the upload is refused with 422 `TAPE_MALFORMED` (the file uses a construct the parser cannot attribute to records) and nothing is stored.
2. Record boundaries: split the decoded text into physical lines on `\r\n`, `\n` or `\r`, keeping the terminators, and feed those lines to the CSV parser (strict, delimiter `,`, quote `"`). After each parsed record, the lines consumed since the previous record are its `raw_text`. A completely blank line produces no record and no row number; its text joins the next record's `raw_text`, or `trailer_text` after the last record.
3. Hashes: `row_sha256 = sha256(utf8(raw_text))`. `rows_sha256` is unchanged in form: SHA-256 of the row hashes joined by `\n` in row order.
4. Cleaning reads `header_cells` and `raw_cells` only, never `raw_payload`. "First cell" is `raw_cells[0]`. `COLUMN_COUNT_MISMATCH` is `len(raw_cells) != len(header_cells)`. A file where every row lacks a trailing column is now a count mismatch on every row, as it should be.
5. Release instructions (`source = API_INSTRUCTION`): `has_bom` false, `header_text` `"loan_id\n"`, `header_cells` `["loan_id"]`, `trailer_text` empty, and per loan id `raw_text` `"<loan_id>\n"`, `raw_cells` `["<loan_id>"]`, `raw_payload` `{"loan_id": "<loan_id>"}`. `file_sha256` stays the hash of the canonical JSON instruction (SPEC 14.6). Reconstruction is checked differently for this source: rebuilding the canonical JSON from `as_of_date`, `facility_id` and the sorted loan ids must hash to `file_sha256`.
6. Deep verification, raw data step, for every submission: recompute every `row_sha256` from `raw_text`; recompute `rows_sha256`; compare `row_count`; re-parse `header_text` and each `raw_text` and require the result to equal `header_cells` and `raw_cells`; rebuild `raw_payload` from them and require equality with the stored object; run the reconstruction of rule 1 (or rule 5) against `file_sha256`. Any difference puts the submission in `bad_submission_ids`. An edit of any one of the stored forms is therefore detected, including the edit RUBRIC AUD-03 makes to `raw_payload`.

Contract: `LineageRawRow` in `bank_agent.yaml` gains two optional fields, `raw_cells` and `raw_text` (added in this commit; additive). WP4 returns both.

RUBRIC BANK-02 is read as: the file hash equals `sha256sum`; `raw_cells` holds the cell texts unchanged and in order; the reviewer recomputes `row_sha256` as the SHA-256 of `raw_text` and `rows_sha256` as before; and the reviewer rebuilds the file from the stored columns and gets the same SHA-256 as the uploaded file.

**Rationale.** "Exactly what the lender sent" has to be a property someone can verify, not a description. With the verbatim text stored per record, a reviewer can rebuild the original file from the database and compare one hash. The parsed array keeps cleaning order-aware; the object stays for readable display.

**Affects.** WP2 (migration `0001_schema`, models, intake, cleaning input, hashes, tests; the 20 loan example test must still pass). WP3 (deep verification as in rule 6, release instruction rows as in rule 5). WP4 (lineage returns the new fields). BANK-01, BANK-02, AUD-03, AUD-04, CODE-05.

## 26. Failure code when outside records refuses a request

**Decision.** Add `OUTSIDE_RECORDS_REJECTED` (added to the `FailureCode` enum of `bank_agent.yaml` in this commit, and to the `failure_code` check constraint in WP2).

| Situation | Classification | Failure code |
|---|---|---|
| Transport error, timeout, 429, 5xx (after the client's own retries) | Transient, stage retried with backoff | `OUTSIDE_RECORDS_UNAVAILABLE` after `PIPELINE_MAX_ATTEMPTS` |
| 401, 403, 422, any other 4xx except 404, or a response that does not match the contract | Permanent, FAILED at once | `OUTSIDE_RECORDS_REJECTED` |
| 404 on titles or contracts | Not an error (a check result, SPEC 11.1) | none |

The audit entry `TAPE_FAILED` carries `detail.dependency` (`outside_records`), `detail.http_status` (or null) and `detail.reason` from the closed set `UNAUTHENTICATED`, `FORBIDDEN`, `REQUEST_REFUSED`, `MALFORMED_RESPONSE`. The same three detail keys are used for `KEY_HOLDER_REJECTED` and `REGISTRY_REJECTED_REQUEST` in WP3, so the precise reason is in the audit log and not only in service logs.

Retry (amends SPEC 13.8): `POST /v1/tapes/{id}/retry` is allowed for every failure code except `KEY_VERSION_MISMATCH` and `CERTIFICATE_INVALID`. A rejection is usually a configuration fault (a wrong or revoked key) that an operator fixes and then retries; the retry is a deliberate, audited action by a credit officer, every stage is idempotent, and if the cause persists the tape fails again with the same code.

**Affects.** WP2 (constraint, mapping, audit detail, tests), WP3 (retry rule, same detail keys for the other two dependencies). REL-05, PRES-04 (`TAPE_NOT_RETRYABLE` is provoked with a `KEY_VERSION_MISMATCH` or `CERTIFICATE_INVALID` tape, or with a tape that is not FAILED).

## 27. Logs and `--json` on the same stream

**Decision (all services).** Amends SPEC 16.5.

- `serve` writes its JSON log lines to stdout. Nothing else is written to stdout by `serve`.
- Every other command (including `worker`, with or without `--once`) writes its JSON log lines to stderr. stdout carries only the command's result: aligned text by default, exactly one JSON document with `--json` (decision 20).
- Human error sentences go to stderr, as plain text, not as JSON log lines. A command therefore may put both log lines and one final sentence on stderr; stdout stays clean.
- `LOG_LEVEL` applies to both. A new common flag `--quiet` on result-printing commands sets the log level to WARNING for that invocation.
- WP5's launcher captures stdout and stderr of each service into its log file, as SPEC 20.2 says. RUBRIC PRES-05 (every line of a log file is one JSON object) is read as applying to lines that start with `{`; the launcher prefixes nothing and a serving process writes nothing else, so in practice every line qualifies. A non-JSON line in a service log file during a clean run fails PRES-05.

A test runs `onepledge-bank worker --once --json` in a child process and asserts that stdout parses as one JSON document and that every stderr line parses as a JSON object.

**Affects.** WP1 fix F5 (same rule for its CLIs), WP2 (change now), WP3 to WP5. PRES-03, PRES-05.

## 28. Where the two process log canary test belongs

**Decision.** WP5. It needs the real processes and their log files, which exist only with the stack launcher: the end to end test (SPEC 21.7) and the scorecard's privacy scan cover bank agent and outside records logs as separate processes, and RUBRIC SEC-09 grades it there.

Until then: WP2's canary test with outside records in-process is accepted for the stages WP2 built. WP3 extends the same in-process canary over the whole pipeline and over the failing paths (SPEC 21.5), capturing the log output of the bank agent, the in-process outside records app and both doubles. WP1's outside records must have its own test that a looked-up VIN never appears in its log output (add it if it is missing; fix F17).

**Affects.** WP1 (F17), WP3, WP5. SEC-09.

## 29. Smaller WP2 choices

| Choice | Ruling |
|---|---|
| A header cell starting with `__extra_` is refused as `TAPE_HEADER_INVALID` | Ratified. The prefix is reserved for surplus cells in `raw_payload`. |
| An empty or dot-only file name is stored as `unnamed` | Ratified, stated exactly: after the sanitising of SPEC 10.1 step 4, a name that is empty or consists only of `.` and `_` characters becomes `unnamed`. |
| A cell longer than 131,072 characters gives `TAPE_TOO_LARGE` | Ratified. That is the CSV parser's own field limit; exceeding it is an oversized cell (413 `TAPE_TOO_LARGE`), not a malformed file. The process-wide parser limit must not be changed (it is global state). The 512 character cell limit of SPEC 10.1 still applies to everything below that. |
| Creating a loan writes `LOAN_STATE_CHANGED` | Ratified, with `detail.from` null and `detail.to` `PENDING`. Both keys are always present. RUBRIC AUD-01 counts creation as a state change. |
| An unknown facility is audited as `TAPE_UPLOAD_REJECTED` | Ratified: `object_id` null, `detail.error_code` `FACILITY_NOT_FOUND`. |
| `ACCESS_DENIED` records method, route and operation | Ratified, provided `route` is the route template and `operation` is the `operationId` from `bank_agent.yaml`. Never the path. |
| `seed-reference` writes no audit entry | **Correction**, see decision 30. |
| Extra CHECK constraints and indexes beyond SPEC 12.1 | Ratified under three conditions: none rejects a state that SPEC allows; each is listed with a one line reason in a table "Additions to SPEC 12.1" in `bank_agent/README.md` (WP5 copies it into `ARCHITECTURE.md`); the model versus migration drift test covers them. RUBRIC CODE-05 ("match SPEC 12.1 column for column") is read as: everything SPEC 12.1 and this file require is present and identical, and anything extra is in that table. An undocumented extra fails CODE-05. |
| Settings parse only the variables WP2 uses | Ratified as an intermediate state. Each package adds the variables of the features it builds; after WP4 the bank agent parses every variable of SPEC 3.3 and `serve` refuses to start when a required one is missing. Settings stay scoped to the command (decision 22, finding 5). |

**Affects.** WP2 (the README table), WP4, WP5. AUD-01, CODE-05.

## 30. Administrative commands are audited

**Question.** `seed-reference` changes lenders and facilities and writes no audit entry, because SPEC 17.2 has no action for it.

**Decision.** Seeding reference data is an auditable administrative act. Three actions are added to `AuditAction` (SPEC 17.2), all with `actor_type` SYSTEM, `actor_user_id` null and `detail.via` `"cli"`:

| Action | Object type | Written by | Detail |
|---|---|---|---|
| `REFERENCE_DATA_SEEDED` | `REFERENCE_DATA` | `onepledge-bank seed-reference`, in the same transaction as the changes, also when nothing changed | `source_sha256` (of `reference.json`), and for lenders and for facilities the counts `inserted`, `updated`, `unchanged` |
| `SCHEMA_MIGRATED` | `SCHEMA` | `onepledge-bank db migrate`, after a successful upgrade that changed the revision | `from_revision` (null on a new database), `to_revision` |
| `USER_CREATED` (existing action) | `USER` | `onepledge-bank users create` (WP4) | changed field names, `via` |

Both new entries go through the normal hash chained writer. The owner role, which runs these two commands, owns `audit_log` and can insert; the chain guard trigger applies to it as to anyone. A downgrade writes no entry (after `downgrade base` the table no longer exists); downgrades are a development operation and the README says so.

**Rationale.** Facilities and lenders decide which lender name a title is compared against. A silent change to them would be a hole in "who did what".

**Affects.** WP2 (both commands, tests). WP4 (`users create`). AUD-01, AUD-02 (the chain must verify with these entries in it).

## 31. Strict request models and the error vocabulary bind the bank agent

Stated separately so WP4 cannot miss it: decisions 22 (findings 1, 3, 5, 6, 7) and 23 (a), (b), (d), (e) apply to the bank agent's HTTP API and CLI in full. In particular: no client supplied name in `details[].field`; one issue vocabulary; strict JSON types; ASCII only integer parameters with an `offset` ceiling of 1,000,000; one `error` log line per failure; `service` is `bank_agent`; serving settings hold no owner credential; no traceback without `--debug`.

**Affects.** WP4. SEC-07, SEC-08, PRES-03, PRES-04, PRES-05.

## 32. Review files

`docs/layer1/reviews/` holds reviewers' reports. They are working papers: they stay untracked unless Nayan decides to keep them in the repository. Product code and tests never reference them. They are on the exclusion list of decision 21 either way.

## 33. Contract changes made in this round (draft, additive)

- `bank_agent.yaml`: `FailureCode` gains `OUTSIDE_RECORDS_REJECTED`; `LineageRawRow` gains optional `raw_cells` and `raw_text`.

`onepledge-contracts check` passes after the change. Vedant should be told of both when the contracts go for approval.

---

## Round 2: required fixes in WP1

| # | Fix | Decision |
|---|---|---|
| F1 | Outside records: integer parameters parsed as ASCII digits only, bounded (`offset` at most 1,000,000), never a 500; tests for every listed input | 23 (a) |
| F2 | Registry double: a repeated conflict returns this item's freshly computed match; test in both orders | 23 (c) |
| F3 | Doubles and outside records: strict JSON types; `1.0`, `true`, `"1"` for an integer are 422 `INVALID_TYPE`; no float reaches hashing | 23 (b) |
| F4 | datagen: no traceback on an unusable output path; last resort handler and `--debug` in every WP1 CLI | 23 (d) |
| F5 | `--json` on every result-printing WP1 command; logs on stderr for non-serving commands | 20, 27 |
| F6 | No client supplied key in `details[].field`; `UNKNOWN_FIELD` on `body`, `UNKNOWN_PARAMETER` on `query` | 22.1 |
| F7 | Unit tests on the oracle's payment window edges | 22.2 |
| F8 | One `error` log line per unhandled failure | 22.3 |
| F9 | Remove the self-comparing `compare_digest` in outside records; doubles compare against every configured entry | 22.4 |
| F10 | Command scoped settings: serving processes hold no owner or admin URL | 22.5 |
| F11 | `service` in logs equals the health value | 22.6 |
| F12 | One issue code vocabulary | 22.7 |
| F13 | Checker: no class names, one problem per broken file | 22.8 |
| F14 | `TapeFormatError` derives from `DataGenError`; docstrings on the three simulation methods | 22.9, 22.10 |
| F15 | `M_AMOUNT_ALT_FORMAT` uses all three forms in rotation; `verify` checks it; `generator_version` 1.0.1; example files stay byte identical or are regenerated in the same change | 22.11 |
| F16 | Correct singular and plural in every counted message | 23 (e) |
| F17 | Outside records test: a looked-up VIN never appears in its log output (add if missing) | 28 |

## Round 2: required changes in WP2

| # | Change | Decision |
|---|---|---|
| C1 | Verbatim raw storage: `has_bom`, `header_text`, `header_cells`, `trailer_text` on `tape_submissions`; `raw_text`, `raw_cells` on `loans_raw`; reconstruction asserted at intake; `row_sha256` over `raw_text`; cleaning reads the arrays; guards extended to the new columns | 25 |
| C2 | Keep the foreign keys on `loans_raw`; add tests for owner `TRUNCATE` (0A000) and `TRUNCATE ... CASCADE` (OP001) | 24 |
| C3 | `OUTSIDE_RECORDS_REJECTED` in the check constraint and the failure mapping; `dependency`, `http_status`, `reason` in the `TAPE_FAILED` audit detail | 26 |
| C4 | Logs to stderr for every command except `serve`; stdout holds only the result; `--quiet`; child process test for `worker --once --json` | 27 |
| C5 | Audit `REFERENCE_DATA_SEEDED` in `seed-reference` and `SCHEMA_MIGRATED` in `db migrate` | 30 |
| C6 | Table "Additions to SPEC 12.1" in `bank_agent/README.md` listing every extra constraint and index with its reason | 29 |
| C7 | `unnamed` rule exactly as worded; `LOAN_STATE_CHANGED` on creation with `from` null | 29 |
| C8 | Exception names, settings scope, error vocabulary, `service` value and single error log line follow the all-services rules | 8, 22, 31 |

---

# Round 3 (2026-11-21): WP1 review 2, WP2 review 1, WP3 questions

Inputs: `docs/layer1/reviews/WP1-review-2.md` and `WP2-review-1.md` (both on commit `ed00b46`), and the WP3 implementer's questions (commits bb796f5 to e73cfdf). Decisions 34 to 62. The lists of required fixes are at the end of the round. "(all services)" and "(all packages)" rules bind every package, including those not yet built.

## A. WP1 review 2

### 34. The failures are confirmed

All confirmed as the reviewer graded them: DATA-06, DX-05, CODE-03, F3, F8, F12.

- F8 is read the strict way: every response with status 500 has exactly one `error` log record, and that record carries the `request_id`. A 500 with no error record, or an error record without the request id, fails.
- F3: decision 23 (b) covers every request body a service parses, including `/_test/` bodies.
- F12: an absent header or parameter is `REQUIRED`; a present one of the wrong shape is `PATTERN_MISMATCH`.
- CODE-03 is literal. The fix is to rename the constant (`VERBATIM_COPY_KINDS`), not to hide it from the grep. The same grep hit in `bank_agent/tests/unit/test_architecture.py` is a pattern that searches for those names; there the pattern is rewritten so the grep does not match it (`EXACT_[C]OPY`, and so on), as decision 21 describes.

### 35. No redirects, and trailing slashes (all services)

No layer 1 service ever answers with a 3xx. Applications are built with slash redirection off. A path with a trailing slash that is not a declared route is an unknown route: 404 `NOT_FOUND` in the error envelope, no `Location` header, nothing of the request echoed in any header or body. Tests request every route with a trailing slash and with a canary in the path and query, and assert status 404 and that the canary is absent from the status line, all headers and the body.

### 36. Request id on every log record of a request (all services)

The request id stays bound in the log context until the response has been sent and every handler (including the catch-all and the named error handlers) has logged. The middleware unbinds it last. Header parsing that can fail (for example `Content-Length`) happens inside the guarded section, so a failure there is handled like any other and the context is always cleared. Tests assert that the `error` record of a provoked 500 carries the same `request_id` as the response, and that a request after a failed one does not inherit the previous id.

### 37. Integer text: decision 23 (a) and 22.7 reconciled (all packages)

This replaces the pattern in decision 23 (a). One rule for every integer read from text (query and path parameters, headers such as `Content-Length`, environment variables, CLI arguments, CSV cells where SPEC allows an integer):

1. The text is a well formed integer only if it matches `^-?[0-9]{1,18}$` with ASCII digits. Anything else (a `+` sign, spaces, a decimal point, an exponent, non-ASCII digits, an empty value, more than 18 digits) is `INVALID_INTEGER`.
2. A well formed integer outside its bounds, including any negative value where the minimum is 0 or 1, is `OUT_OF_RANGE`.

So `-1` is `OUT_OF_RANGE` and `+1` is `INVALID_INTEGER`, as built.

Enforcement: each package has exactly one function that turns text into an integer under this rule, and everything uses it. `str.isdigit`, `str.isdecimal`, `str.isnumeric`, and `\d` in a pattern compiled without `re.ASCII` are banned in every `src/` tree and in every independent oracle or test kit; an architecture test in each package fails on any of them. Settings loaders use the same function, so a malformed numeric setting stops startup with one line naming the variable (this also covers the key holder double's `FAKE_KH_KEYS` and `FAKE_KH_ACTIVE_VERSION`, and the bank agent's `_Reader.integer`).

### 38. Malformed JSON bodies (all services)

A new issue code is added to the vocabulary of decision 22.7: `INVALID_JSON`, reported once as `{"field": "body", "issue": "INVALID_JSON"}` with 422 `VALIDATION_ERROR`. It covers every body that is not one well formed JSON document acceptable to the service: a syntax error, invalid UTF-8, nesting deeper than 32 levels, a number literal longer than 30 characters, a duplicate key in any object, `NaN` or `Infinity`, or a top level value that is not an object. Parsing uses a bounded parser (depth and number length checked before values are built; duplicate keys detected with an object pairs hook), so none of these can raise an unhandled exception. `INVALID_VALUE` goes back to meaning "not one of the allowed values".

A JSON `null` where a value is required is `INVALID_TYPE` (it is present and of the wrong type); only an absent key is `REQUIRED`.

Content type: a request with a JSON body must declare `Content-Type: application/json` (parameters such as `charset` allowed). Otherwise 422 `VALIDATION_ERROR` with `{"field": "Content-Type", "issue": "INVALID_VALUE"}`.

Test control issue codes folded into `INVALID_VALUE` (reviewer ruling c) are ratified.

### 39. Reserved whole-dollar loans in datagen (reviewer ruling d)

Ratified. To plant the `23450` and `$23,450` forms of `M_AMOUNT_ALT_FORMAT` without changing an amount, the generator reserves a deterministic set of honest loans whose original amount and balance have zero cents. This departs from SPEC 5.4 ("plus uniform random cents 0 to 99") for those loans only. Required: one sentence in `datagen/README.md` saying so and why, and `verify` keeps checking the rotation.

### 40. Decision 20 reads "at least" (reviewer ruling e)

A `--json` document contains at least `ok`, and on failure at least `error.code` and `error.message`. Further keys that carry the facts the text output shows are allowed. Within one document the fields must agree: a checker document with `"status": "FAILED"` must not carry a summary sentence that describes success.

### 41. Health endpoints, test control routes, contract gaps (reviewer ruling f)

- `/healthz` and `/readyz` ignore query parameters on every service (probes add cache busters). They never answer 422. This is the one exemption from decision 22.1.
- Every other route applies the unknown parameter rule, including the `/_test/` GET routes of the doubles.
- Contract: 422 is now listed on every operation of `bank_agent.yaml` and `registry.yaml` that lacked it, except the health paths (additive edit in this commit; `key_holder.yaml` and `outside_records.yaml` already had it everywhere).
- A malformed path id answering 404 with the resource's specific code stays as built.

### 42. Reviewer ruling (g): tests that replace a collaborator

Ratified. Replacing a collaborator or a standard library boundary to reach a branch that cannot be reached otherwise (a permission error as root, the last resort handler) is acceptable when the test asserts on output. Where the collaborator can be injected through a constructor instead, it must be (see decision 53).

### 43. The thirteen quality findings of WP1 review 2

All fixed except two, which are settled by a rule.

| # | Finding | Ruling |
|---|---|---|
| 1 | Oracle accepts non-ASCII digits in amounts and dates | **Fix.** Decision 45 (ASCII only) applies to the oracle and to `tape_format.py`. |
| 2 | `verify` proves consistency, not conformance | **Fix.** `verify` gains a second stage after the 5.8 invariants: it regenerates the data set in memory from the manifest's `seed` and `profile` and requires every file on disk, including `manifest.json`, `reference.json` and the answer key, to be byte identical to the regenerated one. A difference is reported per file ("differs from what seed S, profile P generates"). If the manifest's `generator_version` is not the installed one, `verify` says so in one sentence and exits 1. A manifest whose `seed` or `profile` is missing or invalid is reported as such. Every tampering in the reviewer's list must then fail. |
| 3 | The last resort sentence answers ordinary mistakes | **Fix.** The last resort sentence is for defects only. Each case the reviewer lists gets a specific sentence: a malformed data set file names the file and the problem; an invalid `BIND_HOST` names the variable; an admin URL whose role is not a superuser says so. Every setting is validated in the settings loader (type, range, format), so a bad value never reaches the code that uses it. |
| 4 | `serve` starts with the owner role | **Fix (all services).** At startup, `serve` and `worker` check the role they are connected as and refuse to start, with one line naming `DATABASE_URL`, when the role is a superuser, owns any table in the schema, or holds a privilege it must not have. Outside records: any of INSERT, UPDATE, DELETE, TRUNCATE on any table. Bank agent: UPDATE, DELETE or TRUNCATE on any insert-only table, DELETE or TRUNCATE on any table, or CREATE on the schema. Tests start each service with the owner URL and expect exit 1. |
| 5 | 405 without an `Allow` header | **Fix (all services).** Every 405 carries `Allow` listing the methods of that route. |
| 6 | `library_log` records are empty | **Fix (all services).** Records from third party loggers below WARNING are dropped. WARNING and above are kept with `logger`, `level` and, when there is one, `exception_type`; the message is never rendered. A service that needs the information a library logged at INFO logs its own event: `db migrate` writes one `schema_migration_applied` record per applied revision. `revision` (pattern `^[0-9a-z_]{1,64}$`) is added to every log allow-list. |
| 7 | Contract gaps | Decision 41. |
| 8 | `Content-Length` parsed with `isdigit()` | **Fix.** Decisions 36 and 37. |
| 9 | uvicorn's plain text 400 for a protocol error | **Accepted.** Decision 12's "no framework default body" applies to requests that reach the application. A byte stream that is not HTTP is answered by the server below it with a fixed text that contains nothing of the request. No handler is required. |
| 10 | Duplicate JSON keys and any content type accepted | **Fix.** Decision 38. |
| 11 | Python reprs in human output | **Fix (all packages).** No human readable output contains a Python repr. Lists are printed as comma separated plain values, pairs as `ROLE MATCH_KIND`, empty lists as `none`. A test greps the output of every failing `verify` and checker variant for `[`, `'` and `(`. |
| 12 | CLI rough edges | **Fix.** An empty `--out` or `--data-dir` is a usage error (exit 2). The metavar for a data set directory is `DIR` in every command of every package. `--quiet` stays on every result-printing command for a uniform interface; where a command writes no log lines its help says "no effect: this command writes no log lines". |
| 13 | A key holder scalar of 1 is accepted | **Fix.** The key holder double refuses to start when a configured scalar is zero or one (one line naming `FAKE_KH_KEYS`). |

## B. WP2 review 1

### 44. The failures are confirmed

BANK-09, BANK-01, AUD-06, REL-05, CODE-03 and the SEC-13 extra finding are all confirmed as graded. The fixes are decisions 45 to 49 and 37.

### 45. Digits are ASCII digits (all packages)

Wherever SPEC writes a digit (`d`, `d+.dd`, `YYYY-MM-DD`, `MM/DD/YYYY`, "7 digits"), it means one of the ten ASCII characters `0` to `9`. A cell that uses any other Unicode digit is not in an accepted format:

- amounts: `AMOUNT_UNPARSEABLE`; dates: `DATE_UNPARSEABLE`; VIN: `VIN_CHARACTER_INVALID`.
- Month names in `DD-Mon-YYYY` are the twelve English abbreviations in ASCII letters, any case.
- Borrower reference (tightens SPEC 9.1 and 10.4): only these separators are removed: space, `-`, `_`, `.`, `/`. Any other character outside `A-Za-z0-9` (including every non-ASCII character) gives `BORROWER_REF_INVALID`. Nothing else is silently dropped.
- Whitespace trimming removes ASCII space, tab, carriage return and line feed only. A cell that begins or ends with any other Unicode space is not trimmed and then fails its field's format rule.

This binds the bank agent's cleaning, the datagen oracle, and `bank_testkit/independent.py`. The fix is verified by the neutral case table of decision 50, which contains these inputs.

### 46. The tape guard must make SPEC 8.3 the only path (BANK-09)

`onepledge_tape_guard()` is tightened. On `UPDATE`:

| Change | Allowed only when |
|---|---|
| status unchanged and not FAILED | `resume_status`, `failed_stage`, `failure_code` stay null. |
| forward step (RECEIVED to CLEANED, ..., SUBMITTED to COMPLETED) | Exactly the next status of SPEC 8.3; the three failure columns stay null. |
| into FAILED | `OLD.status` is one of the five non-terminal statuses; `NEW.resume_status = OLD.status`; `NEW.failed_stage` is the status that follows `OLD.status`; `NEW.failure_code` is not null. |
| FAILED to FAILED | `resume_status`, `failed_stage` and `failure_code` are all unchanged. |
| out of FAILED | `NEW.status = OLD.resume_status`, and the three failure columns become null. |
| anything else | Refused. |

Every refusal is SQLSTATE `OP004`. Tests: every pair of statuses for both roles, the two forged statements of the review (both refused), a rewrite of each failure column while FAILED, and an entry into FAILED with each wrong `resume_status` and `failed_stage`.

### 47. Inserts go through the state machines too (quality finding 1)

SPEC 8.3, 8.4 and 8.5 allow creation in one state only. The database enforces it:

| Table | `BEFORE INSERT` requires | SQLSTATE |
|---|---|---|
| `loans` | `state = 'PENDING'` | `OP002` |
| `tape_submissions` | `status = 'RECEIVED'`, `resume_status`, `failed_stage`, `failure_code`, `completed_at`, `next_attempt_at` null, `attempts = 0` | `OP004` |
| `alerts` | `status = 'NEW'`, `case_status = 'OPEN'`, `bank_position`, `position_submitted_at`, `reviewed_by`, `reviewed_at` null | `OP006` |

A new guard `onepledge_alerts_guard()` (`BEFORE INSERT OR UPDATE ON alerts`) also enforces SPEC 8.5 on update: only the transitions of that table; CLEARED and ESCALATED rows change in nothing; `registry_case_id`, `loan_id`, `role`, `match_kind`, `match_basis`, `kinds_matched`, `kinds_not_matched`, `received_at` never change; `bank_position` may go from null to a value once and never changes after; `case_status` may leave OPEN once. SQLSTATE `OP006`. The integrity report gains the control `ALERT_STATE_GUARD_TRIGGER_ENABLED`, and `LOAN_STATE_GUARD_TRIGGER_ENABLED` now also requires the insert guards on `loans` and `tape_submissions`.

Test fixtures that need a loan in a later state reach it by legal updates, never by inserting it.

### 48. `failed_checks` lists failures only (AUD-06)

In `LOAN_STATE_CHANGED` detail, `failed_checks` is the list of check types whose outcome is FAIL, in the fixed order TITLE_EXISTS, LIENHOLDER_MATCH, PAYMENTS_RECEIVED, CONTRACT_PRESENT. An UNKNOWN is never listed, tolerated or not. The same definition applies to `counts.loans_held_local` reasoning, to the lineage, and to the scorecard's "failed check set".

### 49. Every outbound HTTP failure is classified (REL-05)

One classification for all four dependency clients (outside records, key holder 1, key holder 2, registry). Nothing raised by the HTTP library may reach the stage as an unexpected error.

| What happened | Class | `reason` |
|---|---|---|
| Connection refused or reset, DNS failure, any other transport error | transient | `CONNECTION_FAILED` |
| Connect, read, write or pool timeout | transient | `TIMEOUT` |
| 429 | transient | `RATE_LIMITED` |
| 500, 502, 503, 504 | transient | `SERVER_ERROR` |
| 401 | permanent | `UNAUTHENTICATED` |
| 403 | permanent | `FORBIDDEN` |
| Any other 4xx that the contract does not give a meaning to for that call | permanent | `REQUEST_REFUSED` |
| A 3xx; a body that cannot be decoded (content encoding, charset); not JSON; JSON that does not match the contract schema; a response about a different object than asked; a page that is not complete (decision 52) | permanent | `MALFORMED_RESPONSE` |

Implementation rule: the client catches the HTTP library's base error class and maps by this table; an error class it does not recognise is `MALFORMED_RESPONSE`, never `INTERNAL_ERROR`. Redirects are not followed. Tests cover each row for each client, including a gzip header with a body that is not gzip.

### 50. What "independent" means for an oracle (quality finding 3) (all packages)

An independent implementation (the datagen oracle, `bank_testkit/independent.py`, and any future one) must satisfy all of these:

1. It imports nothing from the code it checks.
2. It does not share technique or text with it. The bank agent's cleaning may use regular expressions; the independent implementations parse amounts, dates, VINs and references by explicit character level code (walk the string, classify each character against literal ASCII sets, build the value by arithmetic). No regular expression at all in the oracle's cleaning module or in the test kit's independent module. An architecture test asserts the absence of `import re` there.
3. All implementations are anchored to a neutral table that none of them produced: `contracts/examples/cleaning_cases.json`, a list of `{"field", "kind", "as_of_date", "input", "expected": {"action": "ACCEPT" | "FIXED" | "REJECTED", "problems": [...], "canonical": ...}}` written by hand from SPEC 10.4 and decisions 13 and 45. It has at least 120 cases and covers, for every field: each accepted form, each FIXED rule, each REJECTED rule, the boundaries (amount 99.99, 100.00, 250,000.00, 250,000.01; origination on and one day after the as-of date; 1999-12-31 and 2000-01-01; balance equal to original), and adversarial inputs (full width digits, Arabic-Indic digits, superscripts, a non-breaking space, a zero width space, a right-to-left mark, lower case `ſ`, `1e3`, `0x10`, `1_000`, `٢٣`, a 40 digit amount, leading `+`, thousands separators in wrong places, 30 February, month 13, two digit years, a VIN with a Cyrillic `А`).
4. The bank agent's tests, the datagen oracle's tests and the test kit's tests each run the whole table and assert every case. The table is expected values, not an answer key; it may be read by any package.

`onepledge-contracts check` validates the table's shape.

### 51. Shutdown stops at the next unit, not at the end of the stage (quality finding 4)

SPEC 13.1 stands. The worker checks its stop flag after every committed unit: after each check chunk, each tokenise chunk, each registry request and its stored response, each certificate, each outbox message. When the flag is set it commits nothing further, releases the advisory lock and exits 0. The stage is left where it is and resumes on the next start (every stage is already resumable at these points). The clean stage is one transaction and runs to its commit. An HTTP call in flight is allowed to finish or time out, so shutdown takes at most one unit plus `HTTP_READ_TIMEOUT_SECONDS`.

Tests: a stop requested after the first check chunk of a four chunk tape leaves the status CLEANED with one chunk committed, and a second run completes with the same final state as an uninterrupted run. The same for the tokenise and submit stages. The README sentence is corrected.

### 52. A payments page must be provably complete (quality finding 9)

The payments client reads the whole window. It requests `limit=200` and pages with `offset` until it holds `total` items. It treats the response as `MALFORMED_RESPONSE` (decision 49) when: a page has more items than `limit`; a page is empty while fewer than `total` items are held; `total` differs between pages; an item's `vin` is not the one asked for; an item's `paid_on` is outside the requested window; items are not ordered newest first; or `total` exceeds 10,000. Evidence `payments_in_window` is the number of items actually held, which then equals `total`. Tests cover each condition with a stand-in service.

### 53. The remaining quality findings of WP2 review 1

| # | Finding | Ruling |
|---|---|---|
| 1 | Inserts bypass the state machines | **Fix.** Decision 47. |
| 2 | Survived mutation on the duplicate lookup | **Fix.** Add tests: the same bytes uploaded under a different kind, and under a different facility, are accepted as new submissions; the same bytes for the same facility and kind are refused. |
| 3 | Oracles share pattern text | **Fix.** Decision 50. |
| 4 | SIGTERM waits for the whole stage | **Fix.** Decision 51. |
| 5 | Two vocabularies for one failure | **Fix.** Decision 58 (one shape, one vocabulary). `OUTSIDE_RECORDS_PROTOCOL` disappears. |
| 6 | README cites SPEC 8.3 for loans | **Fix.** SPEC 8.4. |
| 7 | Contentless log lines from Alembic | **Fix.** Decision 43, finding 6. |
| 8 | Keys starting with `_` dropped without being named | **Fix.** Keys that the logging framework itself injects are removed by an earlier processor that lists them by exact name. `RedactionProcessor` then names every key it drops in `redacted_keys`, whatever its first character. |
| 9 | Payments page trusted to be complete | **Fix.** Decision 52. |
| 10 | The reconstruction guard is reachable only by patching the module under test | **Fix.** The record splitter becomes a collaborator injected through the constructor of the intake component. The `TAPE_MALFORMED` branch is tested by injecting a faulty splitter, with no patching. The guard itself stays (defence in depth). |
| 11 | Packaging notes | **Fix** the dependency: `bank_agent[dev]` declares every layer 1 package its tests import. **Accepted**: `0001_schema.py` at 663 lines. A migration is a frozen linear script outside `src/`; the 400 line limit does not apply to files under `migrations/versions/`, and splitting one would make it harder to read, not easier. |
| 12 | `--debug` tracebacks can carry values and land in the log stream | **Fix (all packages).** `--debug` prints the exception type and the frames (file, line, function) only: no exception message, no source line, no locals. This is the same rule as the `stack` log key, so the output is safe on any stream. |
| 13 | README hand run leaves the owner URL exported | **Fix.** Every README shows administrative commands with their variables set for that one command (inline or in a subshell), so the shell that later starts `serve` or `worker` never has `MIGRATION_DATABASE_URL` or `PG_ADMIN_URL` exported. |
| 14 | Note for WP4: audit actor of a failed login | **Ruling.** `LOGIN_FAILED` and `LOGIN_RATE_LIMITED` are written with `actor_type` SYSTEM and `actor_user_id` null. `object_type` is USER; `object_id` is the user id when the email belongs to an existing user and null otherwise. `LOGIN_SUCCEEDED` is written with `actor_type` USER and the user id. |
| 15 | A forged append with a correct `seq` and `prev_hash` and an arbitrary `entry_hash` is accepted | **Accepted**, with the reason stated: the hash is computed over canonical JSON, which cannot be reproduced reliably inside a trigger, and the chain is unsigned by design. A forged entry is found by deep verification (its hash does not recompute). WP5 lists this next to limitation 13 of SPEC 23 in `THREAT_MODEL_L1.md`. |

## C. WP3 questions

### 54. `checkpoints_seen.body` stored verbatim (migration 0004)

Ratified. The signed body is kept exactly as received (`body jsonb not null`) because the signature is over the canonical JSON of that body and a timestamp column cannot reproduce the original `signed_at` text. Deep verification re-verifies signatures from `body`. The column is covered by the insert-only trigger and is listed in the README table of additions.

### 55. `registry_requests.attempts` counts every POST

Ratified: `attempts` is the number of HTTP POSTs actually sent for that request, each counted in the database before it is sent, including the client's immediate retries. This is what RUBRIC REL-04 expects (one injected lost answer gives `attempts` 2). `tape_submissions.attempts` keeps its meaning: failed attempts at the current stage.

### 56. The halt rule covers every state-changing call to the registry

Ratified and widened. When the halt rule of SPEC 15.4 is active, the bank agent sends no submission (tape or resubmission) and no case position. It keeps reading (outbox, event headers, certificates, checkpoints).

The halt rule is active when `HALT_ON_REGISTRY_INTEGRITY_FAILURE` is true and any of these holds: a `checkpoints_seen` row has `verified = false`; a registry request has a failed certificate row and no verified one (decision 61); a quarantined registry message of type CHECKPOINT exists (decision 57).

Behaviour: a pipeline submit stage fails the tape with `REGISTRY_INTEGRITY_BROKEN` (as SPEC says). `POST /v1/loans/{id}/resubmit` and `POST /v1/alerts/{id}/review` with `SUBMIT_POSITION` answer 409 with the new error code `REGISTRY_INTEGRITY_BROKEN` (added to the `ErrorResponse` enum of `bank_agent.yaml`; fixed message: "The registry failed an integrity check. Nothing was sent. See the integrity report."). `POST /v1/releases` is accepted and its tape then fails at the submit stage, as any tape does.

### 57. A known outbox message with an invalid payload is quarantined, not left blocking

A message whose `type` is known but whose payload does not match the contract must not block the outbox and must not vanish.

- New insert-only table `registry_messages_quarantined`: `quarantine_id uuid` PK; `message_id uuid` unique; `position bigint`; `message_type text`; `payload jsonb` (as received, truncated to nothing: if the serialised payload exceeds 64 KiB only `payload_sha256` is kept and `payload` is null); `payload_sha256 char(64)`; `reason text` in (`SCHEMA_MISMATCH`, `UNKNOWN_ASSET_TOKEN_FORMAT`, `OVERSIZED`); `received_at`. App role: SELECT, INSERT. Same statement level insert-only trigger as the other evidence tables.
- Handling, in one transaction: insert the row, audit `REGISTRY_MESSAGE_QUARANTINED` (object type REGISTRY_MESSAGE, outcome FAILURE, detail: message type, reason, payload hash), commit, then acknowledge and continue with the next message.
- Integrity report: new section `registry_messages` with `status` and `quarantined` (added to `bank_agent.yaml` as an optional property in this commit; the bank agent always returns it). BROKEN when any quarantined row exists. A quarantined CHECKPOINT message also activates the halt rule (decision 56).
- An unknown message `type` keeps the behaviour of SPEC 14.1 (acknowledge, audit `REGISTRY_MESSAGE_IGNORED`): a new type is additive evolution, not a fault.

The table is the 19th of the bank database. RUBRIC SEC-01's table list gains it; RUBRIC CODE-05's "18 tables" reads 19; RUBRIC AUD-05's "five sections" reads six.

### 58. One shape for failure details, one vocabulary

`TAPE_STAGE_ATTEMPT_FAILED` and `TAPE_FAILED` carry the same keys, always all present (null where not applicable):

| Key | Content |
|---|---|
| `stage` | The stage being attempted (a `TapeStatus` value). |
| `failure_code` | A `FailureCode` value: for an attempt, the code the tape will get if attempts run out. |
| `attempts` | Failed attempts at this stage so far. |
| `dependency` | `outside_records`, `key_holder_1`, `key_holder_2`, `registry`, or null. |
| `http_status` | Integer or null. |
| `reason` | One of the `reason` values of decision 49, or `KEY_VERSION_MISMATCH`, `CERTIFICATE_INVALID`, `INTEGRITY_HALT`, `PROTOCOL_VIOLATION` (a key holder element or count that fails SPEC 9.3 step 4), `UNEXPECTED` (failure code `INTERNAL_ERROR`). |

`TAPE_FAILED` adds `resume_status`. The key `error_code` is not used in these details. The same six facts appear, under the same names, in the `error_code`-free log record of the event (`failure_code`, `dependency`, `http_status`, `reason`, `stage`, `attempts` are on the allow-list; add `reason` and `dependency` if missing) and in the worker's result table. No other name for a failure exists anywhere: `OUTSIDE_RECORDS_PROTOCOL` and any similar internal label are removed.

### 59. Synchronous actions when a dependency refuses

`DEPENDENCY_REFUSED` is replaced by `DEPENDENCY_REJECTED`, which is now in the contract: status 502, fixed message "A service this action depends on refused the request. Nothing was changed. Check the service credentials and configuration." It is added to the `ErrorResponse` enum and as a 502 response on `POST /v1/alerts/{id}/review`, `POST /v1/loans/{id}/resubmit`, `POST /v1/registry/sync` and `POST /v1/integrity/verify`.

Mapping for every synchronous use case and its CLI command: a transient class of decision 49 gives `DEPENDENCY_UNAVAILABLE` (503); a permanent class gives `DEPENDENCY_REJECTED` (502). The CLI prints the code's fixed message and exits 1; with `--json` the code is in `error.code`. Both codes go into the single message table.

### 60. Smaller WP3 points

| Point | Ruling |
|---|---|
| Deep verification reports a chain finding against the stored checkpoint that covers the bad seq (question 4) | Ratified: the failure entry carries the `checkpoint_id` and `checkpoint_seq` of the first stored verified checkpoint whose `last_event_seq` is at or after the bad seq. `first_bad_event_seq` is the exact seq for `CHAIN_BREAK`, and the previous good checkpoint's `last_event_seq + 1` for `HEAD_MISMATCH` (SPEC 15.6). |
| An unknown item result is stored as REJECTED with reason `UNKNOWN_RESULT` (question 6) | Ratified. It is SPEC 13.5. |
| Dependency names `key_holder_1`, `key_holder_2`, `registry` (question 9) | Ratified, with `outside_records`, as the closed set for `dependency` in audit details and logs. The application layer may split a port of SPEC 8.6 into narrower ports, or keep one port with two named instances for the key holders, as long as every class named in SPEC 8.6 exists and no use case depends on an implementation. |
| RUBRIC BANK-04 names `InvalidVin` (question 10) | Confirmed: reviewers read `InvalidVinError` (decision 8). |
| Certificate reason tests run with the halt rule off (question 11, first part) | Ratified for unit and integration tests of `VerifyCertificate`. A wrong `REGISTRY_PUBLIC_KEYS` is rightly caught first by checkpoint verification. At least one test keeps the halt rule on and shows the order: the checkpoint fails first and nothing is submitted. |

### 61. A tape whose certificate fails verification (question 11, second part)

The end state the implementer describes (loans in their new state, tape FAILED with `CERTIFICATE_INVALID`, no way forward) is not acceptable: a bank would be left with collateral records it cannot prove and cannot repair. Required behaviour:

1. Item results are applied as the registry answered them, as today. The loan states say what the registry said; the missing proof is shown separately and loudly.
2. The failed certificate is stored as evidence and never removed. `certificates` changes shape: new surrogate primary key `certificate_row_id uuid`; `certificate_id` and `request_id` are no longer unique on their own; partial unique indexes `(certificate_id) WHERE verified` and `(request_id) WHERE verified`; unique `(certificate_id, body_hash, signature)` so the same bad certificate fetched twice is stored once. Still insert-only.
3. The tape becomes FAILED with `CERTIFICATE_INVALID` (resume status SUBMITTED), the integrity report's `certificates` section is BROKEN, and the halt rule is active (decision 56), so nothing more is sent to a registry that cannot prove what it did.
4. `CERTIFICATE_INVALID` is retryable (this amends decision 26: the only failure code that cannot be retried is `KEY_VERSION_MISMATCH`). A retry runs the certify stage again: it fetches the certificate again for every request that has no verified row. A verified certificate is stored as a new row and the tape can complete. A certificate that fails again in the same way stores nothing new and the tape fails again.
5. Integrity report, `certificates` section: `total` counts requests that should have a certificate; `verified` counts requests with a verified row; `failures` lists every failed row ever stored (evidence). `status` is BROKEN only while some request has a failed row and no verified row. Once every such request has a verified row, the section is OK again, the halt rule releases, and the failed rows stay listed.
6. Visibility until then: the tape item and the lineage show the request's certificate as not verified (`certificate_id` set, `verified` false, `failure_reason` set). `GET /v1/certificates?verified=false` lists it.
7. This applies to every kind. For a release it means: the loans are RELEASED because the registry said so, the bank holds no valid proof of the release yet, and the report says exactly that until a valid certificate is fetched.

Tests: a tampered certificate (`CERTIFICATE_BODY` on the double) fails the tape, breaks the section and activates the halt rule; after the double's stored certificate is restored, a retry completes the tape, the section is OK, the failed row is still listed, and a new submission goes through. RUBRIC SEC-11 is unchanged in what it observes (the tape ends FAILED with `CERTIFICATE_INVALID`; a database edit is found by deep verification). RUBRIC PRES-04 provokes `TAPE_NOT_RETRYABLE` with a tape that is not FAILED or one FAILED with `KEY_VERSION_MISMATCH`.

### 62. Contract and rubric-reading changes of this round

Contract (draft, additive, made in this commit; `onepledge-contracts check` passes):

- `bank_agent.yaml`: 422 on every operation that lacked it except `/healthz` and `/readyz`; 409 on `POST /v1/releases`, review and resubmit where it was only described; 502 on review, resubmit, registry sync and integrity verify; error codes `REGISTRY_INTEGRITY_BROKEN` and `DEPENDENCY_REJECTED`; optional `registry_messages` section in `IntegrityReport`.
- `registry.yaml`: 422 on every operation that lacked it except `/healthz`.

How reviewers read the rubric from now on:

| Rubric text | Read as |
|---|---|
| BANK-04 "raises `InvalidVin`" | `InvalidVinError`. |
| SEC-01 list of nine insert-only tables | Ten: add `registry_messages_quarantined`. Owner `TRUNCATE` per decision 24. |
| CODE-05 "all 18 tables" | 19 tables. `certificates` and `checkpoints_seen` as amended by decisions 54 and 61. |
| AUD-05 "the five sections", "exactly the eleven control names" | Six sections; twelve control names (add `ALERT_STATE_GUARD_TRIGGER_ENABLED`). |
| BANK-09 | Also the forged resume of decision 46 and the insert guards of decision 47 (each refused with the stated SQLSTATE). |
| BANK-01 | Also the non-ASCII inputs of decision 45 (each REJECTED with the stated problem). |
| PRES-04 `TAPE_NOT_RETRYABLE` | Provoked per decision 61. Also provoke `REGISTRY_INTEGRITY_BROKEN` (409) and `DEPENDENCY_REJECTED` (502) and check them like the other codes. |
| CODE-03 third grep | Unchanged and literal. Test patterns that search for case names are written so the grep does not match them. |
| Any criterion on error bodies | Vocabulary of decisions 22.7, 37 and 38. No 3xx from any service (decision 35). |

---

## Round 3: required fixes in WP1 (doubles, datagen, outside records)

| # | Fix | Decision |
|---|---|---|
| G1 | No redirects: slash redirection off on all three services; trailing slash is 404 in the envelope; canary tests | 35 |
| G2 | Request id bound until the last handler has logged; exactly one `error` record with `request_id` per 500, including 500s raised as named errors | 34, 36 |
| G3 | Bounded JSON parsing with `INVALID_JSON`; `null` is `INVALID_TYPE`; duplicate keys refused; `Content-Type` required; applies to `/_test/` bodies too | 38 |
| G4 | One integer-from-text function per package; ban on `isdigit`, `isdecimal`, `isnumeric` and non-ASCII `\d` with an architecture test; settings name the variable; `Content-Length` inside the guarded section | 37 |
| G5 | Missing `Idempotency-Key` is `REQUIRED` | 34 |
| G6 | Rename `EXACT_COPY_KINDS` (for example `VERBATIM_COPY_KINDS`) | 34 |
| G7 | `/healthz` ignores query parameters; `/_test/` GET routes apply the unknown parameter rule | 41 |
| G8 | Oracle and `tape_format.py`: ASCII digits only, borrower separator set, ASCII trimming; no regular expression in the oracle's cleaning module; run `contracts/examples/cleaning_cases.json` | 45, 50 |
| G9 | Write `contracts/examples/cleaning_cases.json` (at least 120 hand written cases) and have the contract checker validate its shape | 50 |
| G10 | `verify` regenerates from seed and profile and compares every file byte for byte | 43.2 |
| G11 | Specific sentences for the listed ordinary mistakes; every setting validated in the loader | 43.3 |
| G12 | Outside records `serve` refuses to start as owner, superuser or with any write privilege | 43.4 |
| G13 | `Allow` header on every 405 | 43.5 |
| G14 | Library log records: drop below WARNING, keep logger and exception type above; own `schema_migration_applied` event with `revision` | 43.6 |
| G15 | No Python repr in human output; consistent fields inside one `--json` document | 43.11, 40 |
| G16 | Empty `--out` or `--data-dir` is a usage error; metavar `DIR` everywhere; `--quiet` help text | 43.12 |
| G17 | Key holder double refuses a scalar of zero or one | 43.13 |
| G18 | `datagen/README.md`: one sentence on the reserved whole-dollar loans | 39 |
| G19 | `--debug` prints exception type and frames only | 53.12 |

## Round 3: required fixes in the bank agent (WP2 and WP3 together)

| # | Fix | Decision |
|---|---|---|
| H1 | Tape guard: the table of decision 46, with tests for every forged path | 46 |
| H2 | Insert guards on `loans` and `tape_submissions`; new `alerts` guard for insert and update; new integrity control; fixtures use legal transitions | 47 |
| H3 | Cleaning: ASCII digits only, borrower separator set, ASCII trimming; run the neutral case table; `bank_testkit/independent.py` rewritten without regular expressions | 45, 50 |
| H4 | `failed_checks` lists FAIL outcomes only, in fixed order | 48 |
| H5 | One classification of outbound HTTP failures in all four clients; no library error reaches a stage as `INTERNAL_ERROR`; no redirects followed | 49 |
| H6 | Payments client pages to `total` and rejects an incomplete or inconsistent page | 52 |
| H7 | Settings: the single integer function; a malformed setting names its variable; ban on `isdigit` and non-ASCII `\d` with an architecture test | 37 |
| H8 | Rewrite the case name pattern in `test_architecture.py` so the CODE-03 grep prints nothing | 34 |
| H9 | Worker stops after the current unit; tests for check, tokenise and submit stages; README corrected | 51 |
| H10 | Tests for the duplicate lookup: other kind and other facility accepted | 53.2 |
| H11 | Failure details: one shape and one vocabulary in audit, logs and worker output | 58 |
| H12 | Framework keys stripped by name before redaction; every dropped key named | 53.8 |
| H13 | Record splitter injected; no patching of the module under test | 53.10 |
| H14 | `bank_agent[dev]` declares the layer 1 packages its tests import; README section reference; README hand run without exported owner URL | 53.6, 53.11, 53.13 |
| H15 | Library log records and `schema_migration_applied` as in WP1 | 43.6 |
| H16 | `serve` and `worker` refuse to start as owner, superuser or with a forbidden privilege | 43.4 |
| H17 | `--debug` prints exception type and frames only | 53.12 |
| H18 | Halt rule covers resubmission and case positions; three activation conditions; 409 `REGISTRY_INTEGRITY_BROKEN` | 56 |
| H19 | Quarantine table, audit action, integrity section, halt on a quarantined checkpoint message | 57 |
| H20 | `DEPENDENCY_REJECTED` (502) replaces `DEPENDENCY_REFUSED`; transient versus permanent mapping for synchronous actions | 59 |
| H21 | `certificates` with a surrogate key and partial unique indexes; `CERTIFICATE_INVALID` retryable; section status and halt as in decision 61; tests for fail, restore, retry, complete | 61 |
| H22 | No redirects, request id binding, bounded JSON, `Allow` on 405: built into the HTTP layer when WP4 writes it | 35, 36, 38, 43.5 |

Ratified as built in WP3, no change: `checkpoints_seen.body` (54), `attempts` counting every POST (55), the deep verification checkpoint reference, `UNKNOWN_RESULT`, the dependency names (60).

---

# Round 4 (2026-11-22): rulings on the round 3 fix hand-backs

Inputs: WP1 fixes G1 to G19 (commits 0034344 to 65f3a00, plus e20b026 and 041cedb) and bank agent fixes H1 to H21 (commits 61545ad to 9809aac), with the questions the coordinator passed on. Decisions 63 to 70. Required changes are listed at the end of the round. No ruling here lowers a gate.

## 63. The unit-only coverage gate for the bank agent stays, and the missing unit tests are written

**Question.** `pytest bank_agent/tests/unit --cov=onepledge_bank_agent.domain --cov=onepledge_bank_agent.application --cov-fail-under=85` reports 69.87%. The registry-side use cases of WP3 are exercised only by integration tests. Option (a): let the combined unit plus integration gate stand in for it. Option (b): write in-memory unit tests for those use cases.

**Decision.** Option (b). Both gates apply to the merged bank agent package, permanently:

1. Unit only (`-m unit`, no PostgreSQL, no network, no file system): at least 85% line coverage of `onepledge_bank_agent.domain` plus `onepledge_bank_agent.application`, branch coverage on.
2. All markers together: at least 85% on the same two packages (SPEC 2.2).

`make test` enforces both and fails if either is below 85. `make test-fast` enforces the first.

How the unit tests are built:

- One set of in-memory implementations of the ports lives in the test kit (`bank_agent/tests/bank_testkit/memory/`): an in-memory `UnitOfWork` with in-memory repositories, an in-memory `AuditLog` that keeps the chain rule, and scripted fakes for `RegistryClient`, `KeyHolderClient`, the three outside records clients, `SignatureVerifier`, `Blinder`, `Clock`, `IdGenerator`.
- Every use case of SPEC 8.6 that WP2 and WP3 built has unit tests against them covering its success path and every failure branch: each class of decision 49 for each dependency it calls, each halt condition of decision 56, each row of SPEC 13.4 and 13.5, each failure reason of SPEC 15.2 and 15.3, the quarantine path, the retry rules, and idempotent re-runs of each stage.
- The in-memory repositories must be proven faithful. For each repository port there is one behavioural test class written once against the port and run twice: against the in-memory implementation (marker `unit`) and against the PostgreSQL implementation (marker `integration`). A behaviour the two disagree on fails the suite. The in-memory unit of work must also reproduce rollback (nothing written in a failed scope is visible afterwards).
- In-memory fakes are test code. Nothing under `src/` imports them and no product code path selects them.

**Why this is the bar and not a lowered one.** BUILD_PLAN section 9 and SPEC 2.1 say the application layer depends on interfaces only. A use case that can be exercised only with a database attached has not demonstrated that, and its failure branches are the ones integration tests reach least: a registry that answers 403 on the third batch, a holder that times out after the first answered, a certificate that fails content matching. Those branches decide whether collateral records are right after a bad day. Accepting option (a) would keep the number above 85 while leaving exactly that code thinly tested. The integration tests stay: they prove the SQL, the guards and the transactions; the unit tests prove the decisions.

**Affects.** Bank agent implementer (change B1). TEST-01 (reviewers check both numbers), TEST-04, CODE-02, REL-02.

## 64. The certificate delivery key uses a SHA-256 the bank computes, not `md5`

**Question.** Decision 61 made `(certificate_id, body_hash, signature)` unique so the same bad certificate is stored once. The `CERTIFICATE_BODY` tamper changes only the body, so the tampered and the genuine delivery share that key and the genuine one would be refused. The implementer added `md5(body::text)` to the index.

**Decision.** The implementer's diagnosis is right and decision 61's key was wrong. The remedy is replaced:

- `certificates` gets a new column `delivered_body_sha256 char(64) not null` with the usual hex check: the SHA-256 of the canonical JSON (SPEC 4) of the body exactly as delivered, computed by the bank agent when it stores the row. It is never taken from the registry's answer.
- `body_hash` keeps its meaning: the hash the registry claimed. The two differ exactly when the body was altered, which is now visible in one row.
- The delivery key is unique `(certificate_id, delivered_body_sha256, signature)`. `body_hash` is not part of it. The `md5(body::text)` expression is removed from the index and from the model.
- `VerifyCertificate` compares `delivered_body_sha256` with the claimed `body_hash` for the `BODY_HASH_MISMATCH` step, so the stored column is the value the verdict rested on.
- Deep verification recomputes `delivered_body_sha256` from the stored `body` for every row and reports a difference as a certificate failure (`BODY_HASH_MISMATCH`), so an edit of a stored body is caught even if someone also edits `body_hash`.
- The column is listed in the README table of additions. `Certificate` in `bank_agent.yaml` is unchanged (the column is internal evidence; WP4 may show it in the lineage only if a later decision adds it to the contract).

Migration: no database outside test and review scratch databases exists, so `0007_certificate_evidence.py` is corrected in place rather than followed by a compensating migration. The same holds for any migration until layer 1 is released; after release, migrations are append only.

**Rationale.** MD5 is not acceptable anywhere in a schema that exists to prove integrity, whatever its role in the index, and `body::text` is PostgreSQL's rendering of jsonb, not the canonical form every other hash in the system uses. A SHA-256 over the canonical delivered body is the same primitive as everything else, is independently recomputable, and gives the auditor a column that says what actually arrived.

**Affects.** Bank agent implementer (change B2). SEC-11, AUD-03, CODE-05.

## 65. The registry double gets a control that undoes a certificate tamper

**Question.** The bank test kit serves the original certificate itself (`restore_certificate`) because the double cannot reverse `CERTIFICATE_BODY`.

**Decision.** Yes, the double gains the control and the test goes through it.

- `POST /_test/tamper` accepts a new mode `CERTIFICATE_RESTORE` with `certificate_id`. It puts the stored certificate back to exactly the document the double first signed. The double keeps that original from the moment of signing. Restoring a certificate that was never tampered is a no-op answered 200; an unknown id is 404 `CERTIFICATE_NOT_FOUND`.
- `RegistryDouble` exposes the same operation in-process.
- The bank test kit's `restore_certificate` no longer serves anything itself: it calls the control. The `_originals` and `_restored` stores in the kit are removed. `tamper_certificate` may still fetch the certificate before tampering if a test needs to compare, but it does not keep it for serving.
- The doubles' README lists the mode next to the other four.

**Rationale.** A test that answers on the double's behalf is testing the test kit. The recovery path of decision 61 is the one a bank relies on after a registry fault; it must be shown against the same surface layer 2 will be compared with.

**Affects.** WP1 implementer (change A1), then bank agent implementer (change B3). SEC-11, DX-05.

## 66. WP1 judgement calls

| Call | Ruling |
|---|---|
| A form content type on `fail-next` is answered `Content-Type INVALID_VALUE` before anything about the body | **Confirmed**, and the order is fixed for every service: (1) route and method (404, 405), (2) authentication (401, including the test control key), (3) authorisation (403), (4) size limit (413), (5) `Content-Type`, (6) JSON well-formedness (`INVALID_JSON`), (7) unknown fields and parameters, (8) field validation. An unauthenticated caller never learns anything about steps 5 to 8. |
| `BIND_HOST` must be an IP literal; `localhost` is refused | **Confirmed.** `BIND_HOST` is the address a server socket binds to, not a name clients use. It accepts an IPv4 or IPv6 literal that is an address of the machine, or the wildcard `0.0.0.0` or `::`. Compose service names are used only in the URL variables of the callers (`REGISTRY_URL`, `OUTSIDE_RECORDS_URL`, and so on), and containers bind `0.0.0.0`, so laptops and compose are unaffected. A name would make the bound interface depend on resolver configuration, which is exactly what a bind setting should not do. The refusal sentence must say what to use instead ("set BIND_HOST to 127.0.0.1 or 0.0.0.0"). The same rule applies to both doubles and to the bank agent (see decision 68, item 1). |
| Settings values are trimmed of ASCII whitespace only | **Confirmed**: space, tab, carriage return, line feed at both ends; nothing inside the value is touched; a value that still contains any other Unicode whitespace or a control character is malformed and names its variable. This applies to secrets too (a trailing newline from a secrets file is the usual cause). |
| `--seed` accepts 0 to 10^18 minus 1 | **Confirmed.** It is the integer rule of decision 37 with a minimum of 0. |
| `verify` reports `VERIFICATION_FAILED` instead of `INVARIANTS_VIOLATED` | **Confirmed with a precise split**, because the code base currently carries both names. `VERIFICATION_FAILED` is the code of `onepledge-datagen verify` when a data set on disk fails any check (a 5.8 invariant, a checksum, or the regeneration comparison of decision 43.2); its document lists each failure with a `stage` of `INVARIANT`, `CHECKSUM` or `REGENERATION`. `INVARIANTS_VIOLATED` is the code of `onepledge-datagen generate` when the generator's own pre-write invariant check fails, which is a defect in the generator and writes nothing. Neither name appears in a contract file or in SPEC, so nothing else changes. Both are listed with their meaning in `datagen/README.md`. |
| Checker closing line `contracts-check: FAILED, N problems` | **Confirmed.** It is grammatical, counted correctly, and RUBRIC DX-05 asks only for a non-zero exit that names the file. The success line stays `contracts-check: OK (N files, M examples)` exactly, because DX-05 quotes it. |
| Library WARNING records keep `stack` | **Confirmed**: frame locations only, as SPEC 16.5 defines `stack`. No message, no locals. |
| Outside records' record for a 500 raised as a named error has no `error_code` | **Overruled (all services).** Every log record written for a response with status 500 or above carries `error_code` (equal to `error.code` of the response body), `http_status`, `request_id`, and `exception_type`. An operator searching the log for the code a caller quotes must find it. |
| Alembic's messages are replaced by one fixed sentence | **Confirmed**, on three conditions that reviewers check: the sentence says what failed and what to do next; the log record carries `exception_type` and the `revision` being applied; `--debug` shows the frames. |
| Decisions 53.8 and 53.13 applied in WP1 as well | **Confirmed.** They were written under the bank agent review but are general hygiene; applying them everywhere is what the "all packages" rules intend. |

**Affects.** WP1 implementer (change A2 for the `error_code` overrule; A3 for the README lines). PRES-03, PRES-04, PRES-05, SEC-13.

## 67. Integrity runs under `unit.evidence.runs`

**Decision.** Acceptable, on a condition about what the group is.

Grouping repositories on the unit of work is a legitimate way to keep an interface small, provided each group is a real concept and not a bucket chosen to satisfy a count. `evidence` must contain exactly the insert-only proof stores and nothing else: certificates, checkpoints seen, quarantined registry messages, integrity runs. Mutable stores (alerts, registry requests and their items, tapes, loans) are not evidence and must not sit under it. The group's port has a docstring saying this in one sentence, and `ARCHITECTURE.md` (WP5) names the groups of the unit of work.

Reviewers check the membership. If `evidence` holds a mutable store, or an insert-only proof store sits outside it, the grouping is corrected (change B4). The limit of 15 public members stays as it is.

**Affects.** Bank agent implementer (B4, only if the membership is wrong). CODE-04, CODE-06.

## 68. Defects and risks the reviewers should look at first

Found while reading the two hand-backs against SPEC, BUILD_PLAN and this file. Items 1 to 3 are defects I am confident of from the code; they are required changes. Items 4 to 12 are places where the new work is most likely to be wrong; I have not proven a defect in them and the reviewers should attack them before anything else.

Confirmed, to fix:

1. **The bank agent does not validate `BIND_HOST`.** `infrastructure/settings.py` takes the text as it is (`read.text("BIND_HOST") or "127.0.0.1"`), while decision 43.3 requires every setting to be validated in the loader and decision 66 fixes the rule. A bad value would surface as a socket error at bind time, not as one line naming the variable. (B5)
2. **`md5` in the certificate index.** Decision 64. (B2)
3. **Application code tested only through PostgreSQL.** Decision 63. (B1)

To attack first:

4. **Insert guards under other statement forms.** `INSERT ... ON CONFLICT DO UPDATE`, `INSERT ... SELECT`, `COPY`, and multi-row inserts for `loans`, `tape_submissions` and `alerts`: each must be refused or must still pass through the guard. Also the alerts guard: `case_status` leaving OPEN a second time, `bank_position` changed after it was set, a CLEARED alert updated in any column.
5. **The quarantine table's controls.** App role grants (SELECT, INSERT only), the statement level insert-only trigger including `TRUNCATE`, and whether `EVIDENCE_TABLES_IMMUTABILITY_TRIGGERS_ENABLED` now also requires the trigger on `registry_messages_quarantined`. A proof table missing from the control is a silent gap.
6. **Halt and recovery order.** With a failed certificate the halt rule is active. The retry of that tape must still be able to fetch the certificate (a read), must not be blocked by the halt it is trying to clear, and must release the halt only when every affected request has a verified row. Also: a crash between storing a registry answer and storing its certificate leaves a request with no certificate row at all; that state must not activate the halt and must be completed by the certify stage on resume.
7. **Tape guard and the worker's own writes.** The tightened guard refuses a change of failure columns while FAILED. Check that every legitimate write still passes: attempt counting and `next_attempt_at` while not FAILED, entering FAILED from each of the five statuses with the right `failed_stage`, and `retry` from each.
8. **Shutdown.** A stop requested while the worker sleeps between polls, while an HTTP call is in flight, in the middle of the single clean transaction, and between a registry answer and its storage. In each case: exit 0, nothing half applied, and the next start reaches the same final state as an uninterrupted run. The last one is the dangerous one: the answer must be stored before the flag is honoured, or the retry must replay it.
9. **Payments paging against a moving total.** Decision 52 treats a `total` that changes between pages as malformed. Check the client does not loop, does not hold a partial window as evidence, and that the tape ends `OUTSIDE_RECORDS_REJECTED` with `MALFORMED_RESPONSE`, not a held loan.
10. **`verify` by regeneration.** It must fail when the answer key alone is weakened and the checksums are repaired (every case in WP1 review 2, finding 2). Also check its cost on the full profile and that it leaves no files behind.
11. **Startup role check under trust authentication.** The build machine's PostgreSQL does not check passwords, so the check must rest on the connected role's attributes and privileges (`pg_roles`, `has_table_privilege`, table ownership), not on which URL was supplied. Start each service as the owner, as a superuser, and as an app role that was granted one extra privilege by hand; all three must be refused.
12. **The neutral case table.** It is only as independent as its authorship. Reviewers read a sample of at least 30 cases of `contracts/examples/cleaning_cases.json` against SPEC 10.4 and decisions 13 and 45 by hand, confirm it was not produced by running either implementation (expected values that a hand would write, including cases where both implementations were wrong before round 3), and confirm that the bank agent, the datagen oracle and the test kit each run every case. The correction commit 041cedb to case `vin-021` should be read: a table that had to be corrected to match the code is a warning sign unless the original entry was plainly a transcription error.

## 69. Reading of TEST-01 after this round

RUBRIC TEST-01's coverage condition is read as: at least 85% for `onepledge_bank_agent.domain` plus `application` in the unit-only run and in the full run, and at least 85% for each of the other four listed packages in the full run. The reviewer runs both commands of decision 63 and records both numbers.

## 70. Nothing in the contracts changes in this round

The `CERTIFICATE_RESTORE` mode is a test control of the double and is not part of any contract file. `contracts/README.md` needs no change. `onepledge-contracts check` is unaffected.

---

## Round 4: required changes

WP1 implementer:

| # | Change | Decision |
|---|---|---|
| A1 | Registry double: tamper mode `CERTIFICATE_RESTORE` (HTTP and in-process), keeping the certificate as first signed; 200 no-op when not tampered; 404 for an unknown id; tests; README | 65 |
| A2 | Every log record for a 5xx response carries `error_code`, `http_status`, `request_id`, `exception_type`, in outside records and both doubles, including 500s raised as named errors; tests assert the keys | 66 |
| A3 | `datagen/README.md`: the two codes `VERIFICATION_FAILED` and `INVARIANTS_VIOLATED` with their meanings; `verify --json` failures carry a `stage` per failure; both doubles validate `BIND_HOST` by the same rule as outside records | 66 |

Bank agent implementer:

| # | Change | Decision |
|---|---|---|
| B1 | In-memory ports in the test kit; unit tests for every WP2 and WP3 use case with every failure branch; one behavioural test class per repository port run against both implementations; `make test` and `make test-fast` enforce the unit-only 85% gate | 63 |
| B2 | `certificates.delivered_body_sha256` computed by the bank over the canonical delivered body; delivery key `(certificate_id, delivered_body_sha256, signature)`; `md5` removed; `VerifyCertificate` and deep verification use the column; migration 0007 corrected in place; README table | 64 |
| B3 | Test kit `restore_certificate` calls the double's `CERTIFICATE_RESTORE`; the kit's own serving of certificates is removed | 65 |
| B4 | Only if membership is wrong: `evidence` holds exactly certificates, checkpoints, quarantine and integrity runs; docstring on the group | 67 |
| B5 | `BIND_HOST` validated in the settings loader by the rule of decision 66; one line naming the variable; tests | 66, 68 |
| B6 | Every log record for a 5xx (once WP4 adds the HTTP layer) and every `error` record of the worker carries the keys of A2 that apply | 66 |

Order: A1 before B3. Everything else is independent.

---

# Round 5 (2026-11-22): WP1 review 3 and bank agent review 2

Inputs: `docs/layer1/reviews/WP1-review-3.md` and `docs/layer1/reviews/BANK-review-2.md`, both on commit `65adce8`. Decisions 71 to 96. Change tables per implementer are at the end. Where a decision here differs from an earlier one, this one wins and says which it replaces. No ruling lowers a gate.

Every failure in both reviews is confirmed as graded, including the two the reviewers were unsure of (decision 71).

## A. Rules that apply to more than one package

### 71. Failures confirmed; PRES-06 covers broken documents

- WP1 review 3: DATA-06, DX-05, CODE-03, CODE-04 and PRES-06 fail. PRES-06 is read through CONTEXT: a document a human reads that renders wrongly (a broken table, a list that does not render, a dangling sentence) is sloppy text and fails PRES-06.
- Bank review 2: BANK-09, SEC-02, SEC-12, BANK-08, AUD-05 and the SEC-13 extra finding fail.
- F3 of the bank review (an alert closed without its case) is a state the table of SPEC 8.5 does not contain. It is a BANK-09 failure, not a quality finding.

Two enforcement rules follow, for every package:

1. Each package has an architecture test that enforces the limits of SPEC 2.2 on its own `src/` tree (400 lines per module, 60 lines per function, 15 public methods per class). A limit that only a reviewer's script checks will be broken again.
2. Each package has a test that parses every Markdown file it owns (and the root and `contracts/` READMEs for the package that owns them) and fails when a table has rows with differing cell counts, or when a table is interrupted by a line that is not a table row.

### 72. One definition of a least-privilege role (replaces the role part of decisions 43.4 and 68 item 11)

"Forbidden privilege" is not a list of things to look for. The runtime role must hold exactly what it was granted and nothing else. A role `R` connected to database `D` is least-privilege only when all of these hold:

1. **Attributes.** `R` is not a superuser and has none of `CREATEDB`, `CREATEROLE`, `REPLICATION`, `BYPASSRLS`.
2. **Membership.** `R` is a member of no role, directly or indirectly, including the predefined `pg_` roles.
3. **Ownership.** `R` owns nothing in `D`: no table, sequence, view, function, type, schema, and not the database.
4. **Database and schema.** On `D`: `CONNECT` only (no `CREATE`, no `TEMPORARY`). On schema `public`: `USAGE` only (no `CREATE`). No privilege on any other non-system schema.
5. **Tables: exact match.** For every table in `public`, the set of privileges `R` effectively holds equals the set the service's grant matrix gives for that table. "Effectively holds" counts grants to `R` and to `PUBLIC`, at table level and at column level: `SELECT`, `INSERT`, `UPDATE` and `REFERENCES` are held when `has_table_privilege` or `has_any_column_privilege` is true; `DELETE`, `TRUNCATE` and `TRIGGER` when `has_table_privilege` is true. An extra privilege on any table fails. A missing one fails too (the database was provisioned wrongly). A table that exists in `public` and is not in the matrix fails.
6. **Sequences.** `R` holds no privilege on any sequence.

The grant matrix is one declarative table in the code of each service. The migration that issues the grants, the startup guard, the integrity report and a test all read that one table; the test asserts it equals SPEC 12.2 as amended by this file (bank agent) or SPEC 6.1 (outside records: `SELECT` on its four tables, nothing else).

One function, `inspect_role()`, evaluates the six points and returns every violation as a code (`ROLE_IS_SUPERUSER`, `ROLE_HAS_ATTRIBUTE`, `ROLE_HAS_MEMBERSHIP`, `ROLE_OWNS_OBJECT`, `ROLE_HAS_DATABASE_PRIVILEGE`, `ROLE_HAS_SCHEMA_PRIVILEGE`, `TABLE_PRIVILEGE_EXTRA`, `TABLE_PRIVILEGE_MISSING`, `TABLE_NOT_IN_MATRIX`, `SEQUENCE_PRIVILEGE`) with the object name. The startup guard and the integrity report both call it. There is no second query anywhere.

Which commands run the guard, and refuse with one line naming `DATABASE_URL` and the first violation code:

| Service | Guarded | Exempt |
|---|---|---|
| Bank agent | Every command that connects with `DATABASE_URL`: `serve`, `worker`, `registry sync`, `integrity verify`, `tapes`, `loans`, `lineage`, `alerts`, `audit`, `users` | `integrity report` (it must be able to show a control that is false); `db provision`, `db migrate`, `db status`, `seed-reference` (they use the owner or admin URL by design) |
| Outside records | `serve` | `db`, `seed`, `clients` (owner or admin URL by design) |

The administrative commands apply the mirror check: `db migrate`, `seed-reference`, `seed` and `clients` refuse to run when the role of `MIGRATION_DATABASE_URL` is not the owner of the schema's tables, with a sentence that says so (this is the correct sentence for WP1 quality finding 8, first case).

Integrity report controls (replaces the definitions in SPEC 15.5; the names stay):

- `APP_ROLE_IS_NOT_OWNER_OR_SUPERUSER`: points 1 to 3 hold.
- `APP_ROLE_CANNOT_UPDATE_LOANS_RAW`, `APP_ROLE_CANNOT_DELETE_LOANS_RAW`, `APP_ROLE_CANNOT_UPDATE_AUDIT_LOG`, `APP_ROLE_CANNOT_DELETE_AUDIT_LOG`: no such privilege at table or column level, by the same inspection.
- New control `APP_ROLE_GRANTS_EXACT`: all six points hold. The report lists the violation codes and object names under the control when it is false.

Tests: for each of the six points at least one hand-made violation that the guard refuses and the report shows, including a column-level `UPDATE` on `audit_log`, a column-level `INSERT` on an outside records table, a grant on a reference table, a grant on `alembic_version`, `ALTER ROLE ... CREATEDB`, membership of `pg_write_all_data`, a grant to `PUBLIC`, a `TEMPORARY` grant on the database, and a missing expected grant.

### 73. `BIND_HOST`: one rule for all four services (replaces the `BIND_HOST` row of decision 66 where it differs)

1. Only `serve` reads `BIND_HOST`. No other command parses it, so a wrong value cannot stop a worker or a one-shot command, and none of them reports on it.
2. Format, in the settings loader: the value must be an IPv4 or IPv6 literal. A name, including `localhost`, is malformed.
3. Machine address, at `serve` startup before anything else is opened: the service opens a socket of the literal's family and binds it to `(BIND_HOST, 0)`, then closes it. The wildcards `0.0.0.0` and `::` and the loopback addresses go through the same probe. Outcomes:
   - bind succeeds: accepted;
   - the address family is not available on this machine (for example no IPv6): refused with "BIND_HOST is an IPv6 address and this machine has no IPv6. Set BIND_HOST to 127.0.0.1 or 0.0.0.0.";
   - the address is not assigned to this machine (for example `8.8.8.8`): refused with "BIND_HOST is not an address of this machine. Set BIND_HOST to 127.0.0.1 or 0.0.0.0.";
   - any other socket error: refused with "BIND_HOST cannot be bound. Set BIND_HOST to 127.0.0.1 or 0.0.0.0."
4. Every socket call, including creating the socket, is inside the guarded section. No socket error reaches the last resort handler.
5. Sentences name the variable and never print its value (SPEC 3.1), here and for every other setting.
6. The probe is behind a small port so unit tests can inject each outcome; one integration test per service uses the real probe with `127.0.0.1`, `0.0.0.0`, `8.8.8.8`, `::` and `::1` and accepts either "started" or the specific IPv6 sentence for the last two, never the last resort sentence.

### 74. Every file and every message a tool reads is hostile input (all packages)

A tool that reads a file it did not just write (a contract YAML, an example, a vector, a data set file, a CSV to seed, `reference.json`) parses it with bounds and reports the file by name:

- JSON through the bounded parser of decision 38 (depth 32, number literal length 30, duplicate keys refused). YAML through the safe loader with the same depth bound, a size limit of 5 MiB and aliases refused. Any parser failure, including a recursion limit, is "FILE: not valid JSON" or "FILE: nests too deeply" and counts as one problem of that file.
- A path taken from inside a file (a manifest step's `file`, a checksum entry, an example index entry) must be relative, must not contain `..`, and must resolve inside the directory being read without following a symbolic link out of it. Otherwise: "FILE names a path outside the directory."

The contract checker, `verify`, `seed`, `seed-reference` and the replay driver of WP5 all follow this.

### 75. Identifiers and encodings are canonical or refused (all services)

- A UUID in a path, query, header or body is accepted only as 36 characters, lower case hexadecimal with hyphens in the 8-4-4-4-12 positions. Braces, `urn:uuid:`, upper case, or 32 characters without hyphens are `PATTERN_MISMATCH` (422), or 404 for a path id where the contract gives 404 for a malformed id. Nothing is rewritten into canonical form.
- A base64 value is accepted only if decoding and re-encoding it gives the same text. A key holder element with non-zero padding bits is `NOT_A_VALID_POINT`.
- A client that closes the connection before the request is complete is not a server failure: one record at level `info` with event `client_disconnected`, no `error` record, no access record with status 500.

### 76. Test runs clean up after themselves, and cannot hang (all packages)

1. Every scratch database and role a test creates is named `optest_<UTC yyyymmddHHMMSS>_<pid>_<8 hex>` followed by the usual suffix. The bank agent's `opwp2t_` prefix is replaced. Reviewers keep using their own prefixes.
2. At the start of every test session that uses PostgreSQL, a sweep drops every database and role whose name starts with `optest_` and whose timestamp is more than two hours old. It never touches any other name.
3. Teardown also runs on SIGTERM: the test session installs a handler that turns SIGTERM into an orderly exit so fixtures finalise. SIGKILL cannot be handled; the sweep of point 2 covers it.
4. `make test-sweep` drops every `optest_` database and role regardless of age, and prints how many it dropped. `make clean` does not call it.
5. `pytest-timeout` is added to the allowed dev-only packages. The root configuration sets a default of 120 seconds per test and 600 for tests marked `e2e`. A test that starts a server in process, or waits for a refusal that might not come, must fail by timeout, not hang.

### 77. Markers: what `unit` means (amends decisions 16 and 63)

- `unit`: no network, no database, no child process, and no write to the file system. Reading files that are committed in the repository is allowed.
- A test that writes to `tmp_path` is `integration`, or is rewritten to work from a string.
- The tokenisation vector test is marked `contract` (SPEC 21.4 lists it there). It may also be marked `unit`.
- New marker `reliability` for the tests of SPEC 21.3. Tests are named for what they show; no test name carries a word only to match a `-k` filter. Where RUBRIC REL-02 says `-k "crash or resume or fault"`, reviewers run `-m reliability`.

### 78. Command line details (all packages)

- **Usage errors and `--json`.** When `--json` is among the arguments, a usage error prints one document on stdout, `{"ok": false, "error": {"code": "USAGE", "message": "The command line is not valid. See the usage text."}}`, the usage text on stderr, exit 2. Decision 20's "on success and on failure" has no exemption.
- **Last resort.** Whenever any command ends through the last resort handler, it also writes one `error` log record with `exception_type` and `stack` (frames only). This holds for one-shot commands as well as long running ones.
- **Sentences name the real cause.** A database that cannot be reached, a database that is reachable but has no tables, and a role that lacks a privilege are three different sentences in every command. `db provision` with an admin URL whose role is not a superuser says so. A numeric setting that is well formed but out of range says "out of range", not "must be a whole number". The same setting has the same sentence in every service. No message ends in a colon.
- **Width.** Human readable output is at most 100 columns: long identifiers are shortened as SPEC 19 says, facts wrap onto continuation lines. `--wide` turns the limit off.
- **Counts in messages are the real counts.** `seed-reference` prints the inserted, updated and unchanged counts it audits.
- **Help text matches behaviour.** `--debug` is described as "print the exception type and call frames".
- **Exit codes of the integrity commands.** `integrity report` and `integrity verify` exit 0 when the report status is OK and 1 when it is BROKEN, after printing the whole report in either case.

### 79. Migration failures (all services with migrations)

`db migrate` treats every exception raised while a migration runs as a migration failure, not as an unexpected error: any database driver or SQLAlchemy error (a statement refused, a missing privilege, an existing relation), and any Alembic error. It prints "The migration to REVISION failed and was rolled back. Fix the database state named in the log and run db migrate again.", writes one `schema_migration_failed` record with `exception_type` and `revision`, and exits 1. Tests provoke at least: a pre-existing conflicting table, a missing privilege, and an unreachable database.

## B. WP1 review 3: remaining rulings

### 80. Small literal failures

- CODE-03, `RISTRETTO_ORDER`: write the number with digit group underscores of at most eight digits per group, so no run of 17 characters exists. The grep stays literal.
- CODE-04, `web.py` at 461 lines: split by responsibility (request reading and bounded parsing; error envelope and handlers; middleware and logging). Decision 71 rule 1 then keeps it split.
- PRES-06: the two tables are repaired; decision 71 rule 2 keeps them whole.

### 81. Quality findings of WP1 review 3

| # | Finding | Ruling |
|---|---|---|
| 1 | Oracle and bank agent disagree on a borrower reference with a removable separator and a forbidden character | **The oracle is right; the bank agent is fixed.** Decision 83. The neutral table gains the cases. |
| 2 | Gaps in the neutral table | **Fix.** Decision 84. |
| 3, 3a | Interrupted runs leave databases; a missing refusal hangs the suite | **Fix.** Decision 76. |
| 4 | A client hang-up is logged as a server failure | **Fix.** Decision 75. |
| 5 | Non-canonical UUID and base64 spellings accepted | **Fix.** Decision 75. |
| 6 | `verify` follows a manifest path out of the directory | **Fix.** Decision 74. |
| 7 | Usage errors ignore `--json` | **Fix.** Decision 78. |
| 8 | Sentences that name the wrong cause | **Fix.** Decision 78, and decision 72 for the role case. |
| 9 | The startup check stops at the four write privileges | **Fix.** Decision 72 replaces the check. |
| 10 | The daily item cap also refuses a RELEASE | **Fix in the double and in the draft contract.** A member must always be able to give up a lock. The daily item cap counts BACK_BOOK and PLEDGE items only; a RELEASE submission is never refused for the cap and its items do not count toward it. `registry.yaml` gains this sentence in the description, under the decision rules: "The per member daily item cap counts BACK_BOOK and PLEDGE items only. A RELEASE is never refused for the cap." Shreyas is told when the contracts go for approval. |
| 11 | Reviewers sharing one interpreter overwrite each other's installs | **Procedure.** Every reviewer and every implementer works in a private virtual environment created from the clone under review. Nobody installs into the shared interpreter during a review. The coordinator re-installs the main repository's packages once reviews end. |

The equivalent mutation (the explicit identity test on key holder input) needs no change.

## C. Cleaning: the cross-package disagreement

### 82. The control character rule and trimming (replaces the trimming bullet of decision 45)

For every required text cell, in this order:

1. **Control characters, on the cell exactly as sent.** If the cell contains any character in U+0000 to U+001F or U+007F (this includes tab, carriage return and line feed, anywhere, also at the ends and inside a quoted cell): REJECTED `UNSAFE_CHARACTERS`. The field ends here, except that step 2 is still evaluated so the record shows both.
2. **Trimming.** ASCII space, tab, carriage return and line feed are removed from both ends. If that changed the cell: FIXED `WHITESPACE_TRIMMED`.
3. **Empty** after trimming: REJECTED `MISSING_REQUIRED_FIELD`. Field ends.
4. **Formula lead**, on the trimmed cell: first character `=`, `+` or `@`: REJECTED `UNSAFE_CHARACTERS`. Field ends.
5. The field's own steps.

Consequences, all intended: a cell with a leading or trailing tab, CR or LF is always rejected (with `WHITESPACE_TRIMMED` also recorded when trimming changed it); only ASCII spaces at the ends can be trimmed into an accepted value; a quoted cell with an embedded line break is rejected. This is what both implementations do today; the rule is now written.

### 83. Normalise, record, then validate (BANK-01)

Within a field's own steps, a normalisation that changes the cell is recorded before the validation that may reject it, and both issues are kept (decision 13, point 3). For the borrower reference:

1. `n` = the trimmed cell with ASCII letters upper cased and every occurrence of the five separators (space, `-`, `_`, `.`, `/`) removed.
2. If `n` differs from the trimmed cell: FIXED `BORROWER_REF_NORMALISED`.
3. If `n` does not match `^[A-Z0-9]{4,32}$`: REJECTED `BORROWER_REF_INVALID`.

So `LK-#123456`, `lk#123456`, `LK 12٣4`, `c004821٣` and `LK-12é4` each give FIXED `BORROWER_REF_NORMALISED` and REJECTED `BORROWER_REF_INVALID`; `#123456` gives only the rejection. The same order holds for the VIN (case, separators, substitution recorded before length, character and check digit rejections) and is what case `vin-016` already expects. The bank agent's `text_fields.py` refuses before it normalises and is corrected. The row is rejected either way; what changes is that the record is complete and identical in every implementation.

### 84. Additions to the neutral table

`contracts/examples/cleaning_cases.json` gains, written by hand from decisions 82 and 83:

- the five borrower inputs of decision 83 and `#123456`;
- a VIN in lower case with one character outside the alphabet (FIXED `VIN_CASE_NORMALISED`, REJECTED `VIN_CHARACTER_INVALID`), restoring the combination that case `vin-021` used to hold;
- for the VIN, the borrower reference, an amount and a date: a trailing tab, a trailing CR, a trailing LF, an embedded LF, a leading space with a leading `=` behind it, and trailing spaces only (the last is the one accepted case);
- a loan number with a leading and a trailing space (accepted with `WHITESPACE_TRIMMED`).

The correction of `vin-021` in commit `041cedb` is accepted as the repair of a transcription error, on the reviewer's reading. From now on a case's `input` is never edited: a wrong case is corrected in its `expected`, or deleted and replaced by a new id, and the commit message says which implementation was wrong, if any.

## D. Bank agent review 2

### 85. Poison messages: contain, record, acknowledge, continue (replaces the handling rules of decision 57; SEC-12, BANK-08)

**The class.** A poison message is any registry outbox message the bank cannot apply for a reason that will not go away by itself. It is defined by exclusion, so no case can fall outside it. Processing one message has exactly three outcomes:

| Outcome | When | What happens |
|---|---|---|
| Applied | The handler committed. | Acknowledge. |
| Deferred | Only these: the bank's database connection was lost or timed out, a serialisation failure or deadlock, or a transient class of decision 49 while calling the registry (for example fetching event headers). | Nothing is stored, the message is not acknowledged, this sync stops and the next one starts with the same message. The deferral is counted (below). |
| Poison | **Everything else**: a payload that fails the contract schema; a value the bank cannot store (out of range, too long); a message that contradicts stored state; any refusal by a database constraint or guard; a permanent class of decision 49; any exception the handler did not anticipate. | Roll back; in a new transaction insert the quarantine row and the audit entry `REGISTRY_MESSAGE_QUARANTINED`; commit; acknowledge; continue with the next message. For an unanticipated exception also one `error` log record with `exception_type`. |

Rules that make the class hold:

1. **Validation before use.** Every outbox message is validated against the full `registry.yaml` schema before any handler runs: types, patterns, bounds, `uniqueItems`, required fields. On top of the schema, every integer that will be stored must fit its column (1 to 9223372036854775807 for sequence numbers and positions). A failure is poison with reason `SCHEMA_MISMATCH` or `VALUE_OUT_OF_RANGE`. Unknown extra fields are ignored (additive evolution). The same full validation applies to every registry and key holder response (decision 49, `MALFORMED_RESPONSE`).
2. **Checkpoints.** A CHECKPOINT message that passes rule 1 is handled by SPEC 15.3: every verification failure is stored in `checkpoints_seen` with its reason and acknowledged. One that fails rule 1 is quarantined, and a quarantined CHECKPOINT activates the halt rule (decision 56).
3. **Messages that contradict stored state.** A CASE_UPDATE for an alert that is already CLEARED or ESCALATED: same status as stored is a repeat, acknowledged with no change; a different status is poison, reason `CONTRADICTS_STORED_STATE`, and the alert is not touched. An ALERT for a `(case, loan)` the bank already holds: identical facts is a repeat; different role, match kind, basis or kinds is poison with the same reason.
4. **Deferral is bounded.** New table `registry_outbox_attempts` (`message_id uuid` PK, `attempts int`, `last_reason text`, `last_at timestamptz`; app role SELECT, INSERT, UPDATE; rows are removed never, a row is simply left when its message is finally handled). Each deferral increments it in its own transaction. At 5 deferrals the message becomes poison with reason `REPEATED_FAILURE`.
5. **The sync can never starve the pipeline.** In the worker loop, whatever the sync does, including raising, the worker goes on to pick and run a stage. `worker --once` runs every runnable stage even if the sync failed, reports the sync failure in its result, and exits 1 only after the work is done. `registry sync` as a command exits 1 only when it could not reach the registry or the database.
6. **Reasons.** `registry_messages_quarantined.reason` is one of `SCHEMA_MISMATCH`, `VALUE_OUT_OF_RANGE`, `CONTRADICTS_STORED_STATE`, `REFUSED_BY_DATABASE`, `REJECTED_BY_DEPENDENCY`, `REPEATED_FAILURE`, `OVERSIZED`, `UNEXPECTED_ERROR`. (`UNKNOWN_ASSET_TOKEN_FORMAT` of decision 57 is covered by `SCHEMA_MISMATCH`.) The audit detail carries the message type, the reason, the payload hash, and for `REFUSED_BY_DATABASE` the SQLSTATE.
7. **An alert that cannot be matched to a loan** stays as SPEC 14.2 says (audit `ALERT_UNMATCHED`, acknowledge); it is not poison.

Tests, each asserting: the row is quarantined with the stated reason, the audit entry exists, the message is acknowledged, the next message in the same poll is applied, a tape uploaded afterwards advances, and the integrity report shows the section BROKEN:

- the two checkpoint messages of review F1 (a sequence number of 2^63, a 31 digit `last_event_seq`);
- the CASE_UPDATE of review F2 on a CLEARED alert;
- an ALERT repeated with different kinds; an ALERT with duplicate kinds; an ALERT without `opened_seq`;
- a handler made to raise an arbitrary exception through the injected `FaultInjector`;
- a message deferred five times;
- a sync that raises while a tape is waiting (the tape still advances).

### 86. Alerts guard: status and case status move together (BANK-09)

`alerts` gets a check constraint: `(status IN ('NEW','REVIEWING') AND case_status = 'OPEN') OR (status = 'CLEARED' AND case_status IN ('CLEARED_DATA_ERROR','CLEARED_LATE_RELEASE')) OR (status = 'ESCALATED' AND case_status = 'CONFIRMED')`. The guard of decision 47 keeps refusing every change to a CLEARED or ESCALATED row and every transition outside SPEC 8.5 (SQLSTATE `OP006`); with the constraint, an alert can no longer close without its case, and `ResubmitLoan` can no longer see a cleared alert whose case is open. The three statements of review F3 are tests and must be refused.

### 87. What may change on a row, column by column (bank quality findings 6 and 10, open question 3)

Each guarded table classifies every one of its columns as immutable, conditionally mutable or mutable, and a test fails when a column exists that is not classified. One parametrised integration test per table then tries to change each immutable column alone, for each role and for each relevant row state, and expects the guard's SQLSTATE. A guard clause that can be deleted without a test failing does not exist as far as the suite knows; this is what makes it exist.

| Table | Always immutable after insert | Conditionally mutable | Whole row frozen when |
|---|---|---|---|
| `loans` | `loan_id`, `facility_id`, `vin`, `first_submission_id`, `created_at` (`OP003`) | `borrower_ref`, `origination_date`, `original_amount_cents`, `lender_loan_number`: only while no `loan_tokens` row exists for the loan (`OP003`) | `state = 'RELEASED'` (`OP002`) |
| `tape_submissions` | the columns of SPEC 12.4 point 4 and of decision 25 (`OP004`) | `attempts`, `next_attempt_at`: only while the status is not COMPLETED and not FAILED; failure columns as decision 46 | `status = 'COMPLETED'` (`OP004`) |
| `alerts` | the columns of decision 47, plus `explanation_text`, `explanation_source` (`OP006`) | `bank_position`, `position_submitted_at`: null to a value once; `note`, `reviewed_by`, `reviewed_at`: while NEW or REVIEWING | status CLEARED or ESCALATED (`OP006`) |

`facility_id` and `vin` move to the always immutable group: the repository never updates them, and re-presentation finds the loan by exactly that pair.

### 88. The integrity report's text and JSON forms cannot differ (AUD-05)

The text renderer and the JSON renderer walk the same list of sections and the same list of failures; neither has its own. The text report shows all sections of the JSON report, and under "Open failures" lists every finding that currently makes a section BROKEN (quarantined messages with their reason, failed checkpoints, requests without a verified certificate, evidence problems, false controls with their violation codes). Failures that were later recovered appear under a separate heading "Recovered (evidence kept)". A test renders both forms from one report object with every section BROKEN and asserts every section name and every failure id appears in both.

### 89. Evidence that has disappeared is a finding (replaces point 5 of decision 61; bank quality finding 2)

A report that says OK after certificates, checkpoints or audit entries were deleted is not acceptable. Absence is checked in three ways.

**(a) Expected proofs, checked live by `GET /v1/integrity` and by the report command (cheap queries):**

- Certificates. For every tape with status COMPLETED, every one of its registry requests with a stored answer must have a verified certificate row. A request without one is listed in `certificates.failures` with the certificate id the registry announced and reason `CERTIFICATE_MISSING`. `total` is the number of answered requests of all tapes; `verified` the number of those with a verified row. `status` is BROKEN when any answered request of a COMPLETED tape lacks a verified row, or any answered request of any tape has a failed row and no verified row (decision 61). A request of a tape still in progress with no row at all is not yet a failure.
- Checkpoints. If any registry request has a stored answer and no verified checkpoint row exists, `registry_chain` is BROKEN, not NOT_VERIFIED. NOT_VERIFIED is only for a bank that has never had an answer from the registry.

**(b) Reconciliation with the audit log, done by deep verification:**

- Every audit entry that records the creation of a proof names the row's primary key as `object_id`: `CERTIFICATE_VERIFIED` and `CERTIFICATE_REJECTED` (the `certificate_row_id`), `CHECKPOINT_VERIFIED` and `CHECKPOINT_REJECTED` (the `checkpoint_id`), `REGISTRY_MESSAGE_QUARANTINED` (the `quarantine_id`), `INTEGRITY_VERIFICATION_RUN` (the `run_id`), `TAPE_UPLOADED` and `RELEASE_REQUESTED` (the `submission_id`).
- Deep verification compares, for each of these, the set of ids in the audit log with the set of primary keys in the table. An id in the audit log with no row is `ROW_MISSING`. A row with no audit entry is `ROW_UNAUDITED`.
- Stored checkpoint sequence numbers (verified and failed rows together) must be consecutive from the lowest to the highest; a hole is `SEQUENCE_GAP`.
- Every tape must have its `TAPE_UPLOADED` or `RELEASE_REQUESTED` entry and its `row_count` rows in `loans_raw`; every loan must have its creating `LOAN_STATE_CHANGED` entry (`from` null). A missing entry is `ROW_UNAUDITED` on that table.

**(c) The end of the audit log:**

- Every stored `integrity_runs.report` records the audit log's `last_seq` and `last_entry_hash` at the time of the run. The next deep verification requires the log to still contain that seq with that hash and to be at least that long. If not: `AUDIT_TAIL_MISSING`, `audit_log` BROKEN.

Findings of (b) are reported in a new report section `evidence` with `status` and a list `problems` of `{table, row_id, problem}` where `problem` is `ROW_MISSING`, `ROW_UNAUDITED` or `SEQUENCE_GAP`. The section is BROKEN when the list is not empty. `AUDIT_TAIL_MISSING` sets `audit_log.first_bad_seq` to the missing seq.

What remains outside reach, stated in `THREAT_MODEL_L1.md` by WP5: a superuser who deletes the proof rows, the matching audit entries, every later audit entry and every integrity run consistently, and rebuilds the chain. Nothing inside one database can detect that; an off-host copy of the head hash would, and none exists yet (SPEC 23, limitation 13).

Tests: each deletion the reviewer made (one certificate, all certificates, one checkpoint, the newest six, all checkpoints, all audit rows, the newest audit rows) turns the report BROKEN and names the table and, where it can be known, the rows.

### 90. Controls prove the guard is the one that was shipped (bank quality finding 3)

A trigger control is true only when all of these hold for every trigger it covers: the trigger exists on the table; `tgenabled` is `O` or `A`; `tgtype` equals the expected value (timing, level and the full set of events, so a trigger recreated for TRUNCATE only is false); the trigger calls the expected function; and the SHA-256 of that function's stored source (`pg_proc.prosrc`) equals the expected hash.

The expected values are a guard manifest in the bank agent's `infrastructure/db/` (trigger name, table, `tgtype`, function name, source hash). A test migrates a fresh database to head and asserts the manifest equals what is installed, so the manifest cannot drift from the migrations. A migration that changes a guard changes the manifest in the same commit.

Tests: replacing `onepledge_forbid_mutation()` with a function that returns NULL, replacing `onepledge_loans_guard()` with `RETURN NEW`, and recreating `audit_log_insert_only` for TRUNCATE only each turn the matching control false.

### 91. Killing tests for the two surviving mutations (bank quality findings 1 and 6)

- **Held loans are never tokenised.** (i) A unit test of `TokenizeLoans` with a submission holding one PENDING and one HELD_LOCAL loan asserts that the blinder and both key holder clients received the eight inputs of the PENDING loan only, and that no token exists for the held loan. (ii) The three bank replay test asserts, per bank: no `loan_tokens` row for any loan in HELD_LOCAL; no `registry_request_items` row for any such loan; the element count each key holder double received from the bank equals the bank's `loan_tokens` row count; the item count the registry double received equals the bank's answered request items. These are RUBRIC E2E-05 (a) to (d) as assertions.
- **Guard clauses.** Decision 87's column by column test.

RUBRIC TEST-04's mutation list gains two that reviewers apply from now on: (12) remove the PENDING filter in `TokenizeLoans`; (13) remove any one column from any guard's immutable list. Each must fail a test.

### 92. Open questions of the bank implementer

| Question | Ruling |
|---|---|
| Unit tests that read committed files or write to `tmp_path` | Decision 77. |
| Role check on one-shot commands | Decision 72: every command that connects with `DATABASE_URL`, with `integrity report` exempt. |
| `facility_id` and `vin` on a loan without tokens | Always immutable. Decision 87. |
| The memory store does not refuse what the database refuses | **Required now, not later.** The refusals are part of each repository port's contract. Each port documents the writes it must refuse and raises one port level error for them (`StoreRefusedError` with the guard code `OP001` to `OP006` or `UNIQUE`); the PostgreSQL repositories map the SQLSTATEs to it and the in-memory repositories raise it themselves. The behavioural test class of decision 63 gains refusal cases run against both: a tape saved two steps ahead; a forged resume; a loan saved in an illegal state; a change of an immutable column; a second write of the same check, token, certificate delivery or checkpoint; an alert closed without its case. A use case that double steps must fail its unit test, not only its integration test. |

### 93. Remaining quality findings of bank review 2

| # | Finding | Ruling |
|---|---|---|
| 4 | The live report sees fewer grants than the startup check | **Fix.** Decision 72 (one function). |
| 5 | Interrupted test runs leave databases and roles | **Fix.** Decision 76. |
| 7 | Last resort sentence and no `error` record on one-shot commands; `db provision` with a non-superuser | **Fix.** Decisions 78 and 85 (the two database refusals no longer reach the last resort). |
| 8 | `integrity report` exits with `SCHEMA_NOT_CURRENT` instead of showing the control false | **Fix.** `integrity report` is exempt from the "schema at head" startup check as it is from the role guard; it prints the report with `SCHEMA_AT_MIGRATION_HEAD` false and exits 1 (decision 78). If the schema is so far off that a query fails, that section is shown as `NOT_VERIFIED` with the reason and the rest is still printed. |
| 9 | Duplicate kinds accepted silently | **Fix.** Decision 85, rule 1. |
| 10 | Mutable fields outside decision 47's list | **Fix.** Decision 87. |
| 11 | A missing `BANK_CODE` is accepted by `worker` and `registry sync` | **Fix now.** `BANK_CODE` and `BANK_NAME` are required by every runtime command; a log record without the bank it belongs to is not acceptable in a multi-bank deployment. This ends the intermediate state decision 29 allowed for these two variables. |
| 12 | Wording, test names, table width | **Fix.** Decisions 77 and 78. |

### 94. A limitation that this round makes explicit

Failed checkpoints, quarantined messages and evidence problems are never removed, so the sections that report them stay BROKEN and (for checkpoints) the halt rule stays active until an operator turns it off. There is no workflow in layer 1 for an authorised person to record that a finding was investigated and accepted. That is deliberate for now (evidence is never edited) and is a known limitation: WP5 adds it to the README's limitations and to `THREAT_MODEL_L1.md` in these words. Building such a workflow needs its own decision.

## E. Contract and rubric reading

### 95. Contract changes (draft, additive)

This commit does not touch the YAML files. The WP1 implementer makes exactly these edits, re-runs `onepledge-contracts check`, and changes nothing else:

- `registry.yaml`, description, after the Rule C paragraph: the sentence of decision 81, finding 10.
- `bank_agent.yaml`, `CertificateSection.failures[].failure_reason`: add the enum value `CERTIFICATE_MISSING`.
- `bank_agent.yaml`: new optional property `evidence` on `IntegrityReport`, referring to a new schema `EvidenceSection`: object, `additionalProperties: false`, required `status` and `problems`; `status` refers to `IntegrityStatus`; `problems` is an array of objects (`additionalProperties: false`, required `table`, `row_id`, `problem`) with `table` a string matching `^[a-z_]{1,64}$`, `row_id` a string or null, `problem` one of `ROW_MISSING`, `ROW_UNAUDITED`, `SEQUENCE_GAP`.

### 96. How reviewers read the rubric from now on

| Rubric text | Read as |
|---|---|
| SEC-02 | The role is least-privilege by decision 72: each of its six points is attacked with at least one hand-made violation, including a column-level grant; the guard refuses and the report shows it. The guarded and exempt commands are those of decision 72. |
| DATA-06 "read-only" | Outside records `serve` passes decision 72 with its matrix (SELECT on four tables); a column-level write grant is refused. |
| AUD-05 | Seven sections (`evidence` added); thirteen controls (`APP_ROLE_GRANTS_EXACT` added); the text report shows the same sections and failures as the JSON (decision 88). |
| AUD-03 | Also the deletions of decision 89 (each found and located) and the replaced or narrowed guards of decision 90 (each turns its control false). |
| BANK-09 | Also the status and case status pairs of decision 86 and the column by column immutability of decision 87. |
| BANK-01 | Also the inputs of decisions 82 to 84, each with exactly the stated issues, identically in the bank agent and the oracle. |
| SEC-12, BANK-08 | Also the poison message tests of decision 85: nothing a registry sends can stop the pipeline or go unrecorded. |
| SEC-13 | `BIND_HOST` by decision 73 on all four services. |
| CODE-05 "19 tables" | 20 tables (`registry_outbox_attempts` added). SEC-01's insert-only list stays at ten; `registry_outbox_attempts` is mutable by design. |
| CODE-04 | Also: each package's own architecture test enforces the size limits (decision 71). |
| PRES-06 | Also: no broken Markdown table or list in any document (decision 71). |
| PRES-03 | Also: at most 100 columns without `--wide`; a usage error with `--json` prints one document (decision 78). |
| REL-02 | Selected with `-m reliability` (decision 77). |
| TEST-01 | Markers as decision 77 defines them; both coverage numbers (decision 69). No test without a time limit (decision 76). |
| TEST-04 | Mutations 12 and 13 of decision 91 are added to the list. |
| Every review | Private virtual environment per reviewer (decision 81, finding 11). Scratch objects use the reviewer's own prefix, never `optest_`. |

---

## Round 5: changes for the WP1 implementer (`contracts/`, `datagen/`, `outside_records/`, root tooling)

| # | Change | Decision |
|---|---|---|
| W1 | Outside records: `inspect_role()` with the six points and the grant matrix as one table; `serve` guarded by it; `db migrate`, `seed`, `clients` refuse a role that is not the owner; tests for every point including column-level grants; README sentence made true | 72 |
| W2 | `BIND_HOST`: literal check in the loader, bind probe at `serve` start, three specific sentences, no value printed, all socket calls guarded; in outside records and both doubles; probe behind a port; tests | 73 |
| W3 | Contract checker, `verify`, `seed`: bounded JSON and YAML loading, file named, one problem per file; paths from inside files confined to the directory | 74 |
| W4 | Canonical UUIDs only; canonical base64 only; `client_disconnected` at info, no error record and no 500 access record | 75 |
| W5 | Test databases named `optest_<timestamp>_<pid>_<hex>`; session start sweep of names older than two hours; SIGTERM teardown; `make test-sweep`; `pytest-timeout` with 120 s default in the root configuration; the in-process `serve` refusal tests bounded | 76 |
| W6 | Markers per decision 77 (root configuration: add `reliability`, reword `unit`) | 77 |
| W7 | Usage error with `--json` prints one document; last resort writes an `error` record; the wrong-cause sentences of review finding 8 corrected and made identical across services; no message ends in a colon; 100 column limit and `--wide` | 78 |
| W8 | Outside records `db migrate`: every exception during a migration is a migration failure with the fixed sentence and a `schema_migration_failed` record; three provoked cases tested | 79 |
| W9 | `RISTRETTO_ORDER` written with digit groups of at most eight; `web.py` split below 400 lines; the two README tables repaired | 80 |
| W10 | Architecture test for the SPEC 2.2 size limits and a Markdown table test in each of the three packages | 71 |
| W11 | Registry double: RELEASE never counted toward or refused by the daily item cap; test | 81.10 |
| W12 | Oracle: control character rule and order of decision 82 confirmed by tests; neutral table gains the cases of decision 84; from now on a case's `input` is never edited | 82, 83, 84 |
| W13 | The three YAML edits of decision 95; `onepledge-contracts check` passes | 95 |

## Round 5: changes for the bank agent implementer (`bank_agent/`)

| # | Change | Decision |
|---|---|---|
| K1 | Poison message containment exactly as decision 85: full schema and range validation before handlers; three outcomes; `registry_outbox_attempts`; quarantine reasons; sync cannot starve stages; `worker --once` and `registry sync` exit rules; the listed tests | 85 |
| K2 | `inspect_role()` and one grant matrix used by the grants migration, the startup guard and the report; guard on every `DATABASE_URL` command except `integrity report`; mirror check on `db migrate` and `seed-reference`; control `APP_ROLE_GRANTS_EXACT`; the four `APP_ROLE_CANNOT_*` controls and `APP_ROLE_IS_NOT_OWNER_OR_SUPERUSER` derived from the same inspection; tests for every point | 72 |
| K3 | Alerts check constraint on the status and case status pair; the three statements of review F3 refused | 86 |
| K4 | Column classification and guards per decision 87 (`facility_id` and `vin` always immutable; RELEASED loans, COMPLETED tapes, closed alerts frozen; `explanation_*` immutable; `attempts` and `next_attempt_at` conditional); one parametrised test per table over every column; a test that every column is classified | 87 |
| K5 | One list of sections and failures for the text and JSON reports; "Open failures" and "Recovered (evidence kept)"; test over a fully BROKEN report | 88 |
| K6 | Absence checks: `CERTIFICATE_MISSING`; `registry_chain` BROKEN with answers and no checkpoint; audit `object_id` is the proof row's primary key; reconciliation, sequence gaps and tape and loan anchors in deep verification; audit tail check against the last integrity run; `evidence` section; tests for every deletion in the review | 89 |
| K7 | Guard manifest (trigger, table, `tgtype`, function, source hash); controls compare against it; drift test against a freshly migrated database; the three replacement tests | 90 |
| K8 | Killing tests: `TokenizeLoans` unit test with a held loan; replay assertions for tokens, items and the doubles' counters | 91 |
| K9 | `StoreRefusedError` in the port contracts; in-memory repositories refuse what the database refuses; refusal cases in the shared behavioural test classes | 92 |
| K10 | `BIND_HOST` read only by `serve`, by the rule of decision 73 (loader check now; the probe when WP4 adds `serve`); removed from every other command's settings | 73 |
| K11 | `BANK_CODE` and `BANK_NAME` required by every runtime command | 93.11 |
| K12 | `integrity report`: exempt from the head check, shows the control false, exits 1 when BROKEN and 0 when OK; `integrity verify` the same exit rule | 78, 93.8 |
| K13 | Test databases, sweep, SIGTERM teardown and time limits as W5 (prefix `optest_`); markers as decision 77, including `reliability` and honest test names; the vector test marked `contract`; the `tmp_path` test moved or rewritten | 76, 77 |
| K14 | `text_fields.py`: normalise, record, then validate; the new neutral table cases pass in the bank agent and in `bank_testkit/independent.py` | 83, 84 |
| K15 | Command line details of decision 78: usage error with `--json`, `error` record on last resort, real-cause sentences, 100 columns and `--wide`, `seed-reference` counts, `--debug` help text | 78 |
| K16 | `db migrate` failure handling as decision 79 | 79 |
| K17 | Architecture test for the size limits and a Markdown table test in `bank_agent` | 71 |
| K18 | Canonical UUID and base64 rules and the `client_disconnected` record built into the HTTP layer when WP4 writes it; canonical validation of ids in registry answers and messages now | 75 |

Order: W12 and W13 before K14 and K6 (the bank agent needs the new table cases and the contract fields). Everything else is independent.

---

# Round 6 (2026-11-23): WP1 review 4 and bank agent review 3

Inputs: `docs/layer1/reviews/WP1-review-4.md` and `docs/layer1/reviews/BANK-review-3.md`, both on commit `ea71d82`. Decisions 97 to 124. Change tables per implementer and the rubric readings are at the end. Each ruling is written for a class of defect, with the instances from the reviews as its tests. Where a decision here differs from an earlier one it says which it replaces. No ruling lowers a gate.

## A. Confirmations and housekeeping

### 97. Every failure is confirmed

- WP1 review 4: DATA-06, DX-05, CODE-01, CODE-05, PRES-03 fail as graded.
- Bank review 3: SEC-12, BANK-08, AUD-05, CODE-01, TEST-01 fail as graded.
- The four readings the WP1 reviewer was unsure of are settled: the `noqa` limit is for the whole repository (decision 119); an undeclared import fails CODE-05 even when another package happens to install it (decision 120); the 100 column rule covers every line any layer 1 tool prints for a human, including usage errors and `make help` (decision 121); defects found beyond a procedure attach by rule 8 as before.

### 98. The edit to SPEC 9.2 in commit `bfa675b` is rendering only

That commit put a backslash before the pipe inside six code spans so the table renders. It changes no rule. The canonical value strings contain one pipe character, U+007C, and no backslash: `"AUTO|" + V`, `B + "|" + str(C)`, and so on. `contracts/examples/vectors/tokenization.json` is the authority for the exact bytes. No other edit to `SPEC.md` or `RUBRIC.md` is permitted without a decision.

### 99. A conflict between a decision and a contract is reported, never tested around

The bank agent's `TEXT_ONLY` list exempted findings from the "both forms" test because the contract had no field for them. When a decision cannot be met because a contract file, SPEC or another decision stands in the way, the implementer stops and reports it in the hand-back. A test that carries an exemption list for a rule of this file fails TEST-02.

## B. Values, messages and answers from outside the bank

### 100. The round-trip rule: nothing is stored that every reader cannot read back (bank F1) (all services)

**The class.** A value arriving from outside (a registry message or answer, a key holder answer, an outside records answer, a tape cell, an API request, a file) may be stored only if it lies inside the intersection of three ranges: what the contract allows, what the PostgreSQL column type holds, and what the Python type that reads it holds. A value outside any of the three is refused before storage.

Bounds, applied in one place per service (the wire model for each source):

| Kind of value | Accepted |
|---|---|
| Timestamp | RFC 3339 with an explicit offset or `Z`; at most 6 fraction digits; after conversion to UTC, from `2000-01-01T00:00:00Z` up to but not including `2100-01-01T00:00:00Z`. The conversion is checked, not assumed: an offset that pushes a boundary value outside is refused. |
| Date | `2000-01-01` to `2099-12-31`. |
| Integer for a `bigint` column | Within the column's meaning and never outside `-9223372036854775808` to `9223372036854775807`; sequence numbers and positions `1` to `9223372036854775807`. For an `int` column, never above `2147483647`. |
| Text | Valid Unicode with no U+0000, no lone surrogate, no control character unless the field's rule allows one, and a length bound taken from the contract (255 characters where the contract gives none). |
| JSON stored as `jsonb` | Passed the bounded parser of decision 38; every string inside it satisfies the text rule; every number is an integer inside the `bigint` range; serialised size at most 64 KiB. |
| UUID, hash, token, base64 | Canonical forms of decision 75 and the contract patterns. |

What a violation becomes: an outbox message is poison with reason `VALUE_OUT_OF_RANGE` (decision 101); an answer of a shared service is `MALFORMED_RESPONSE` (decision 49); a tape cell is rejected by cleaning; an API request is 422.

Enforcement, three layers, all required:

1. **Before storage.** Repositories accept domain types only (for example a `StorableTimestamp` value object), never a raw `datetime`, `int` or `str` from a wire model. The value objects check the bounds. An architecture test asserts no repository method takes a wire model.
2. **In the database.** Every timestamp or date column that holds a value supplied by another party gets a check constraint with the same bounds (`checkpoints_seen.signed_at`, `certificates.issued_at`, and every other such column the implementer finds; the README table of additions lists them). Evidence tables can then never hold a value the bank cannot read.
3. **In every reader.** The integrity report, deep verification and every list endpoint read each section and each row inside its own guard. A row or section that cannot be read is reported, not raised: the section is BROKEN with an `evidence` problem `UNREADABLE` naming the table and, where known, the row id, and every other section is still produced. `GET /v1/integrity`, `integrity report` and `integrity verify` cannot fail as a whole because of stored data.

Tests: a Hypothesis round-trip test per stored type that writes the edge values through the repository and reads them back equal; the three reproductions of bank review 3 F1 (a checkpoint with `9999-12-31T23:59:59-12:00`, one with `0001-01-01T00:00:00+12:00`, a certificate with the latter `issued_at`), each refused before storage with the stated outcome; and, on a database where such a row was forced in by a superuser with the constraint dropped, a report that still renders with that section BROKEN.

### 101. The outbox can neither block nor lose a message (replaces decision 85; bank F2, F3, quality 4 and 5)

Decision 85's intent stands. Its structure is replaced, because three paths escaped it: a page that could not be parsed, a message that could not be acknowledged, and a deferral count that turned a short outage into a permanent halt.

**Level 1: the page.** The answer to `GET /v1/outbox` is read under decision 102 and parsed by the bounded parser. It must be an object with a `messages` array and a boolean `has_more`. If it is not (not JSON, the bare token `NaN`, wrong shape, too large), the bank cannot act on anything in it: the sync ends as **incomplete** with a permanent class reason. Nothing is acknowledged.

**Level 2: each element of `messages`,** in order:

| The element | Outcome |
|---|---|
| is not an object, or has no canonical UUID `message_id` | **Unacknowledgeable.** Recorded once per distinct element: a quarantine row with `message_id` null, reason `UNACKNOWLEDGEABLE`, `payload_sha256` of the element's canonical JSON (unique on that hash, so a redelivery adds nothing), and one audit entry. Processing continues with the next element. The sync is incomplete. |
| has a usable id and its `type` is a string matching `^[A-Z][A-Z0-9_]{0,31}$` that is not ALERT, CASE_UPDATE or CHECKPOINT | **Ignored** (SPEC 14.1): audited `REGISTRY_MESSAGE_IGNORED` once, acknowledged. |
| has a usable id and anything else is wrong: `type` null, not a string, empty, too long or holding other characters; `position` or `created_at` invalid; payload failing the schema or decision 100 | **Poison.** Quarantined (stored `message_type` is the type when it matches the pattern above, else the literal `UNREADABLE`), audited, acknowledged. |
| is valid | Handled (level 3). |

**Level 3: handling a valid message** has the three outcomes of decision 85 (applied, deferred, poison), with these changes:

- **Deferral is never converted into poison.** Rule 4 of decision 85 and the reason `REPEATED_FAILURE` are withdrawn. A message is deferred only for a lost or timed out database connection, a serialisation failure or deadlock, or a transient class of decision 49 on a registry call the handler needs. It stays deferred until the cause passes. What protects the bank meanwhile is the blind rule below, which clears by itself.
- **A deferred CHECKPOINT does not hold back other kinds.** Later ALERT and CASE_UPDATE messages in the same and later pages are still handled and acknowledged. Later CHECKPOINT messages wait behind it, because checkpoints must be verified in order.
- **A CASE_UPDATE that matches no alert** is not dropped: it is quarantined with reason `UNMATCHED` (payload kept), audited `CASE_UPDATE_UNMATCHED` (outcome FAILURE), acknowledged. When an ALERT for the same `case_id` and asset token is created later, the handler applies the newest such case update to the new alert in the same transaction, audits `ALERT_CASE_UPDATED` with `detail.from_quarantine_id`, and marks the message's ledger row (below) `APPLIED_LATE`. An unmatched ALERT stays as SPEC 14.2 says.
- **A database refusal** inside a handler (`StoreRefusedError`, any constraint or guard SQLSTATE) is poison with reason `REFUSED_BY_DATABASE` and the SQLSTATE in the audit detail, never a deferral.

**Acknowledgement.** After a message is applied, ignored or quarantined, the bank acknowledges it. A transient failure of the acknowledgement is retried on the next sync. A permanent refusal (for example 404 for a message the registry itself delivered) is recorded once (audit `REGISTRY_ACK_REFUSED`, outcome FAILURE) and makes the sync incomplete. In both cases a redelivered message is recognised and is not handled or audited a second time.

**The message ledger.** Table `registry_outbox_attempts` of decision 85 is replaced by `registry_outbox_messages`: `message_id uuid` PK, `message_type text`, `first_seen_at`, `handled_at` null, `outcome` null or one of `APPLIED`, `IGNORED`, `QUARANTINED`, `APPLIED_LATE`, `ack_status` one of `PENDING`, `ACKED`, `REFUSED`, `last_reason text` null, `updated_at`. One row per message with a usable id, written in the handler's transaction. App role SELECT, INSERT, UPDATE; guard: `message_id`, `message_type`, `first_seen_at` immutable, `outcome` set once except `QUARANTINED` to `APPLIED_LATE`, a row never deleted. This is what makes "handled once" and "nothing unrecorded" checkable.

**Complete and blind.** A sync is **complete** when it reached `has_more: false`, every element had a usable id, every message was applied, ignored or quarantined, and every one was acknowledged. Single-row table `registry_outbox_state` records `last_complete_sync_at`, `incomplete_since` and `last_reason`. The bank is **blind** when `last_complete_sync_at` is null or older than `OUTBOX_BLIND_HALT_SECONDS` (new tunable, default 300).

- Being blind is the fourth condition that activates the halt rule (decision 56): the bank sends no submission and no case position to a registry whose checkpoints it is not reading. It is the only halt condition that clears by itself: the next complete sync ends it.
- Entering and leaving the blind state are each audited once (`REGISTRY_OUTBOX_BLIND`, `REGISTRY_OUTBOX_RECOVERED`), not once per poll.
- While blind, the integrity report shows `registry_messages` BROKEN with the reason and since when (decision 108).
- A bank that has never completed a sync is blind. It therefore reads the outbox to its end before its first submission, which is the right order.

**Sync and stages.** Whatever the sync does, the worker goes on to run stages (decision 85 rule 5 stands). `worker --once` exits 0 and reports `ok: true` only when its sync was complete, the bank is not halted, and no tape ended FAILED during the run; otherwise it still does all the work it can, reports each problem in its result, and exits 1. This amends SPEC 13.1. `registry sync` exits 0 only for a complete sync.

**Quarantine reasons** are now: `SCHEMA_MISMATCH`, `VALUE_OUT_OF_RANGE`, `CONTRADICTS_STORED_STATE`, `REFUSED_BY_DATABASE`, `REJECTED_BY_DEPENDENCY`, `UNACKNOWLEDGEABLE`, `UNMATCHED`, `OVERSIZED`, `UNEXPECTED_ERROR`. `registry_messages_quarantined` gains `payload_withheld boolean not null` (true whenever `payload` is null because it could not or must not be stored); the audit detail carries the same flag. Migration 0011's relaxation is accepted with that addition.

**Which quarantined rows are open.** A quarantined row is open unless its ledger row is `APPLIED_LATE`. Open rows make `registry_messages` BROKEN. A quarantined CHECKPOINT still activates the halt rule for good (decision 56); nothing else in this decision creates a permanent halt.

Tests, each asserting what is stored, audited and acknowledged, that the next message is applied, that a waiting tape advances through every stage before submit, and the exit code:

- `type` null, `7`, empty, 65 characters, holding U+0000; `message_id` missing and not a UUID; an element that is a string; a page holding `NaN`; a page that is an array;
- the acknowledgement answered 404, and answered 503 three times then 204;
- `/v1/ledger/events` answering 503 for ten syncs with the outbox healthy: the checkpoint stays deferred, alerts behind it are delivered, the bank becomes blind after the limit, nothing is quarantined, and one good sync restores everything with no permanent halt;
- the CASE_UPDATE of review F3, then its ALERT arriving later;
- with one unacknowledgeable element waiting at a bank, `REWRITE_HISTORY` on the registry: that bank is blind, a release instruction created afterwards fails with `REGISTRY_INTEGRITY_BROKEN` and sends nothing;
- a handler write refused by a trigger raising `23514`: `REFUSED_BY_DATABASE` with that SQLSTATE (kills the surviving mutation of bank quality finding 8).

### 102. Every answer from a shared service is bounded (bank F7) (every HTTP client in layer 1)

One rule for the bank agent's four clients and for every client WP5 writes:

1. **Size.** `HTTP_MAX_RESPONSE_BYTES`, default 4,194,304. A `Content-Length` above it is refused before the body is read. The body is always read as a stream and the read is abandoned as soon as the bytes received exceed the limit, whether or not a length was declared.
2. **Encoding.** The client sends `Accept-Encoding: identity`. An answer with any other `Content-Encoding` is refused without decoding.
3. **Time.** A total deadline per call, `HTTP_TOTAL_TIMEOUT_SECONDS`, default 30, on top of the connect and read timeouts, so a body that trickles cannot hold a worker.
4. **Class.** Any of these is permanent, `MALFORMED_RESPONSE` (decision 49). For the outbox it makes the sync incomplete (decision 101).

Tests per client: a body one byte over the limit with and without `Content-Length`, a gzip answer, a body sent one byte per second. Memory use of the reproduction of F7 (400 MB of padding) stays under 50 MB above the idle process.

## C. Privileges and controls

### 103. Least privilege, completed (adds to decision 72; bank quality 1 and 6, WP1 quality 9)

Decision 72's six points stand, with point 5 tightened and four points added. `inspect_role()` returns these further violation codes, and the startup guard and the report keep using that one function.

5. **Tables, tightened.** No column-level privilege exists at all for the role or for `PUBLIC` on any table in `public` (`COLUMN_PRIVILEGE`). The expected privileges are then compared at table level only, so a privilege held on one column never counts as held.
7. **Session and role settings.** The service sets its own session parameters on every new connection (`search_path`, `statement_timeout`, `lock_timeout`, `idle_in_transaction_session_timeout`) and does not rely on role or database defaults. The role has no row in `pg_db_role_setting` for any database (`ROLE_HAS_SETTING`); no database-wide setting of `session_replication_role` exists; the role holds no privilege on any configuration parameter (`PARAMETER_PRIVILEGE`); and in the connected session `session_replication_role` is `origin` (`TRIGGERS_DISABLED_FOR_SESSION`). The last check also runs on every new pooled connection, which is refused if it fails.
8. **Functions.** No function in a non-system schema is `SECURITY DEFINER` (`SECURITY_DEFINER_FUNCTION`). The role holds `EXECUTE` on no function in a non-system schema, directly or through `PUBLIC` (`FUNCTION_PRIVILEGE`); the migrations revoke `EXECUTE` on the guard functions from `PUBLIC` (trigger functions do not need it to fire).
9. **Default privileges.** `pg_default_acl` has no entry in the database that grants anything to the role or to `PUBLIC` (`DEFAULT_PRIVILEGE`).
10. **Grant options.** The role holds nothing `WITH GRANT OPTION` on any object (`GRANT_OPTION`).

`DATABASE_URL` and `MIGRATION_DATABASE_URL` must not carry query parameters; the settings loader refuses them with one line naming the variable (this is the first case of WP1 review 4, failure 2).

New integrity control `TRIGGERS_FIRE_FOR_APP_ROLE`: true only when point 7 holds. `APP_ROLE_GRANTS_EXACT` covers points 1 to 6 and 8 to 10.

Sentences. The refusal names `DATABASE_URL`, the first violation code, and advice that fits the code, from one table: an attribute, membership, ownership or extra privilege says "use the app role as provisioned"; `TABLE_PRIVILEGE_MISSING` and `TABLE_NOT_IN_MATRIX` say "the database was not provisioned or migrated correctly; run db migrate as the owner"; a setting or parameter violation says "remove the role's settings".

`db migrate` refuses to run unless `DB_APP_ROLE` is exactly `{database}_app` and is not the owner (WP1 quality 4). The mirror check distinguishes "the role of MIGRATION_DATABASE_URL is not the owner" from "a table in the schema is owned by another role; the schema was changed outside db migrate" (WP1 quality 5).

Tests: every new code with a hand-made violation, for outside records and for the bank agent, including `ALTER ROLE ... IN DATABASE ... SET session_replication_role = replica`, `GRANT SET ON PARAMETER`, a `SECURITY DEFINER` function executable by `PUBLIC`, `ALTER DEFAULT PRIVILEGES`, `SELECT ... WITH GRANT OPTION`, a column-level `SELECT`, `ALTER ROLE ... SET statement_timeout`, `ALTER ROLE ... SET search_path`. Each refuses startup with its own code and turns the matching control false.

### 104. Startup is a list of named checks, and none ends in the last resort sentence (WP1 F2) (all services)

Startup runs a fixed sequence of named checks (settings, database reachable, session parameters set, schema at head, role inspection, bind probe). Each check has its own failure sentence. Any database error raised inside a check is caught at the check's boundary and reported as that check's failure with the SQLSTATE: "The database refused a statement of the startup check NAME (SQLSTATE X). ..." An ordinary condition of the environment never reaches the last resort handler. Tests cover the three reproductions of WP1 review 4, failure 2, on every service that has a database.

### 105. The schema is compared against a manifest, not sampled (extends decision 90; bank quality 2, schema part)

The guard manifest becomes a schema manifest: tables, columns with types and nullability, every constraint with its definition, every index, every trigger with `tgtype` and function, every function with its source hash, and the expected emptiness of rules (no `pg_rewrite` entry other than a view's), row security policies and extra objects. One canonical rendering of the live catalogue is hashed and compared with the expected hash; the test that migrates a fresh database to head keeps the expected value honest.

New control `SCHEMA_MATCHES_MANIFEST`. When false the report lists the differing object names. A dropped check constraint, an added rule, an added trigger, a changed column type each turn it false. The existing trigger controls keep their meaning.

## D. Evidence: what is guarded, what is detected, what remains

### 106. No row is ever deleted, and an answered request is frozen (bank quality 3)

**Deletion.** No table of the bank database permits `DELETE` or `TRUNCATE` for anyone. The insert-only tables already refuse them. Every other table (`lenders`, `facilities`, `users`, `login_attempts`, `tape_submissions`, `loans`, `registry_requests`, `registry_request_items`, `alerts`, `registry_outbox_messages`, `registry_outbox_state`) gets a statement level trigger refusing both with SQLSTATE `OP008`. Users are deactivated, never removed.

**Answered requests.** `registry_requests` and `registry_request_items` get a guard, SQLSTATE `OP007`:

| Table | Always immutable | Mutable until answered | Frozen when |
|---|---|---|---|
| `registry_requests` | `request_id`, `submission_id`, `origin`, `batch_index`, `idempotency_key`, `kind`, `key_version`, `item_count` | `attempts`, `sent_at`, `last_attempt_at`; the answer columns may be set once, together | `response_status` is not null: every column |
| `registry_request_items` | `request_id`, `item_ref`, `position`, `loan_id` | the result columns may be set once, together | `result` is not null: every column |

Both tables join the column classification test of decision 87. The app role can no longer rewrite what the registry answered, and neither can the owner without first disabling a trigger, which the controls show.

### 107. Deep verification covers every decision the bank acts on (replaces the scope of SPEC 15.6 and decision 89 where wider; bank quality 2)

A check result, a token, a loan state or a registry result that can be edited without a trace is not acceptable. Three mechanisms, each locating the finding to a table and a row or a tape and stage. Findings go in the `evidence` section.

**(a) Stage digests.** When a stage completes, its `TAPE_STAGE_ADVANCED` audit entry carries digests of what the stage produced. Each digest is the SHA-256 of the canonical JSON of the rows, in a fixed order, with fixed columns:

| Entry for the move to | Detail key | Rows covered, for this submission |
|---|---|---|
| CLEANED | `items_sha256`, `issues_sha256` | `submission_items` (row number, disposition, loan id); `cleaning_issues` (row number, field, problem, action) |
| CHECKED | `checks_sha256` | `local_checks` (loan id, check type, result, reason code, evidence) |
| SUBMITTED | `tokens_sha256`, `requests_sha256` | `loan_tokens` of every loan in the submission's request items (loan id, kind, key version, token); `registry_requests` and their items (idempotency key, key version, item refs, loan ids, every answer and result column, SHA-256 of the canonical stored response body) |

`LOAN_RESUBMITTED` carries `requests_sha256` for its request. Deep verification recomputes every digest from the tables and compares. A difference is problem `DIGEST_MISMATCH` with the table, the submission id and the stage. This covers an edited or deleted check row, token, issue, item result and response body.

**(b) Re-derivation.** Cleaning is a pure function of the raw row, so its results are checked by running it again. For every raw row deep verification re-runs `clean_row` and requires the stored issues to equal the result (covered already by the digest, and this names the row). For every loan it takes the loan's most recent accepted submission item, re-derives the canonical terms and balance from that raw row, and requires `vin`, `borrower_ref`, `origination_date`, `original_amount_cents`, `current_balance_cents` and `lender_loan_number` to equal them. A difference is `DERIVATION_MISMATCH` with the loan id. The raw rows themselves are already proven by their hashes.

**(c) Replay of the audit log.** For every loan, the `LOAN_STATE_CHANGED` entries in order must form a chain from null to the current state through legal transitions; a loan with no entries, entries for a loan that does not exist, or a current state that differs is `STATE_MISMATCH`. The same replay is done for every tape from its `TAPE_UPLOADED`, `TAPE_STAGE_ADVANCED`, `TAPE_FAILED` and `TAPE_RETRY_REQUESTED` entries, and for every alert from `ALERT_RECEIVED`, `ALERT_REVIEW_STARTED`, `ALERT_POSITION_SUBMITTED` and `ALERT_CASE_UPDATED` (their details carry the resulting status and case status). `ALERT_RECEIVED` names the alert id as `object_id`, so a deleted alert is `ROW_MISSING` under decision 89.

Reference data and users:

- `REFERENCE_DATA_SEEDED` gains `content_sha256`: the digest of the `lenders` and `facilities` tables after the seed. Deep verification compares the current tables with the digest of the latest such entry (`DIGEST_MISMATCH` on `lenders` or `facilities`).
- `USER_CREATED` and `USER_UPDATED` carry the resulting `role` and `active` values (codes, not secrets). Deep verification replays them per user and compares (`STATE_MISMATCH` on `users`).

**What remains outside reach, and must be stated exactly so in `THREAT_MODEL_L1.md`** (this list replaces the residual statement of decision 89):

1. A superuser who rewrites data and then rewrites the audit chain from that point on, the integrity runs, and every later entry consistently. Nothing inside one database detects that. An off-host copy of the head hash would.
2. `users.password_hash`, `users.display_name`, `users.email`: a superuser can replace them undetected. Only role and active are replayed.
3. `login_attempts` and the outbox ledger tables are operational records, not evidence, and are not reconciled.
4. A local check's evidence proves what the outside source answered at the time. Deep verification detects a later edit of the stored result; it does not ask the source again.

Tests: every tamper of bank review 3 quality finding 2 (a check flipped to PASS, a check deleted, a token changed, a loan's tokens deleted, an issue's action changed, a loan's state changed with the guard off, a loan's amount changed, an alert deleted, a held loan deleted with its items, an item result changed, a response body replaced), each made as a superuser with the relevant trigger disabled and re-enabled, each found by `integrity verify` with the stated problem and location. The dropped constraints and the added rule are found by decision 105.

### 108. The JSON report carries everything the text report shows (bank F4; replaces nothing in decision 88, completes it)

The contract gains the fields the findings need (decision 123). The JSON report then carries, and the text report renders from the same objects: the role violations with code and object; every quarantined message with its id, type, reason, time, whether its payload was withheld and whether it is open; the outbox state (last complete sync, blind or not, since when, reason); every evidence problem with table, row id, stage and problem. Lists are capped at 200 entries, newest first, each with a `total`. The `TEXT_ONLY` list is deleted and the test asserts both directions: every finding in the text is in the JSON and every finding in the JSON is in the text.

Wrapping: a line breaks only between facts (after a comma or before a new key), never inside one, and never leaves a line ending in a colon.

## E. WP1 review 4: remaining rulings

### 109. `seed` validates every cell against the contract before anything is written (WP1 F1)

1. **One validator per record type** (`TitleRecord`, `Payment`, `Contract`) in the domain of outside records, with every rule of `outside_records.yaml`: patterns, lengths, enums, minimums, formats, plus the text rule of decision 100 (no control characters, no U+0000). The CSV loader builds the record exactly as the API will return it and passes it to the validator.
2. **Tested against the contract, not written twice.** A contract test reads `outside_records.yaml`, derives boundary probes from each field's schema (a string at `maxLength` and one longer, each enum value and one outside, values just inside and outside each minimum, and a hand-written list of matching and non-matching strings for each pattern), and asserts the domain validator and a JSON Schema validation of the same record agree on every probe. A field added to the YAML without a validator fails the test.
3. **All or nothing.** Every row of all three files is validated, including uniqueness inside the files (`payment_id`, `contract_id`, one title per VIN), before the first write. On any problem nothing is loaded, exit 1.
4. **Sentences name the place.** Each problem is `FILE line N, column NAME: PROBLEM` (for example `titles.csv line 2, column state: must be two upper case letters`). At most 20 are printed, followed by the count of the rest; `--json` carries the same list. The sentence "The database rejected the records because a constraint failed" no longer appears for a data problem.
5. **The database holds the same rules.** Outside records gets check constraints for every contract rule (state pattern, status enum, name lengths, bank code pattern, hash pattern, no control characters) and the unique keys, as a new migration. A row that would make the API answer outside its contract cannot exist, whoever writes it. This adds to SPEC 6.1.
6. `clients add` with a key that another client already has says so.

Tests: each reproduction of the failure, one probe per contract rule, and a live check that after any refused seed the three lookups for the touched VIN answer exactly as before.

### 110. Files are regular files inside the directory (extends decision 74; WP1 quality 6 and 7)

Every path a tool opens, whether named in a file or fixed by convention (`examples/index.json`, `examples/vectors/`, `manifest.json`, `answer_key/`, each CSV), must be a regular file (or a real directory where a directory is expected) that resolves inside the directory given on the command line, with no symbolic link anywhere on the way. A link, a FIFO, a device or a socket is refused by name ("FILE is not a regular file" or "FILE is a link out of the directory") before it is opened. The checker and `verify` apply the same rule, an unexpected extra directory is reported by `verify`, and a directory where a file is expected is reported as that. Size limits: 5 MiB for JSON and YAML, 256 MiB for a seed CSV.

### 111. The contract checker resolves every reference and contains its validator (WP1 F3 and quality 1)

1. Before any library is called, the checker walks each YAML file and resolves every `$ref` itself. A reference must be local to the file (`#/...`) and must point at an existing node. Otherwise: `FAIL openapi FILE: the reference at POINTER does not resolve`, one line per bad reference, and the file's other checks are skipped with a `SKIP` line. A reference to another file is refused as such.
2. The third-party validator is called behind one boundary that turns anything it raises into one `FAIL` line for that file. This is the single place in layer 1 where a broad catch is allowed, because the exception classes of that library are not a stable interface; it is commented as such and still prints no class name.
3. Examples are validated against the schema reached through the operation (request body or response of the path), not only against the component by name, so an operation pointing at nothing or at the wrong schema fails.

Tests: the five reproductions of failure 3 and the dangling request body reference of quality finding 1, each exiting 1 and naming the file.

### 112. Remaining WP1 quality findings

| # | Finding | Ruling |
|---|---|---|
| 2 | Unit isolation does not see a database connection, a file write or most child processes | **Fix.** Decision 116. |
| 3 | One malformed `optest_` name would stop every session | **Fix.** Decision 115. |
| 8 | Repeated headers accepted silently | **Fix (all services).** A request that repeats `Authorization`, `X-API-Key` or `X-Test-Control-Key` is 401 `UNAUTHENTICATED`. A request that repeats `Idempotency-Key`, `Content-Type`, `Content-Length` or `X-Request-ID` is 422 with `{"field": "<header>", "issue": "DUPLICATE"}`. |
| 10 | Command line edges | **Fix.** Decision 121. |
| 11 | `contracts/fakes/README.md` lists a module that no longer exists | **Fix**, and each package gets a test that every path named in a layout block of its README exists and every module of its `src/` tree is named there. |
| 12 | Weak or shared secrets accepted | **Fix (all services).** Settings refuse: a signing seed of all zero bytes; two principals with the same key hash (a member and the auditor, two members, two clients); in the bank agent, any two of its four outbound API keys being equal (SPEC 16.4 says each is distinct), and a `JWT_SECRET` equal to any API key. A key holder scalar of L minus 1 is a valid scalar and stays accepted. |
| 13 | The tooling's own tests create names no sweep finds | **Fix.** Decision 115. |

Findings 1, 4, 5, 6, 7 and 9 are decisions 111, 103, 103, 110, 110 and 103. The directory `/nonexistent_ro` left at the file system root by a reviewer's probe is not a product defect; the coordinator removes it.

## F. Bank review 3: remaining rulings

### 113. Remaining quality findings and the reviewer's rulings on open items

| Item | Ruling |
|---|---|
| Quality 7: a withheld payload is not marked | **Fix.** `payload_withheld`, decision 101. |
| Quality 8: test gap on a database refusal in a handler | **Fix.** The test listed in decision 101. Added to the mutation list as number 14: treat `StoreRefusedError` as a deferral. |
| Quality 9: `pytest-timeout` missing from `bank_agent[dev]` | **Fix.** Decision 117. |
| Quality 10: help and README details | **Fix.** `worker --help` names all four dependencies and their variables. The README says `BANK_CODE` is any code of 3 to 12 upper case letters and gives the three demo codes as examples. `seed-reference` with a code that is not in `reference.json` says so. |
| Quality 11: wrapping | **Fix.** Decision 108. |
| Open item 1: text the bank cannot store is quarantined with the payload withheld (migration 0011) | **Accepted**, with the `payload_withheld` column and the timestamp class of decision 100. A compensating migration is as acceptable as an in-place edit until release; the chain must stay linear and the up, down, up test must pass. |
| Open item 2: an unusable `type` blocks the page | **Not accepted.** Decision 101. |
| Open item 3: no size limit on answers | **Not accepted.** Decision 102. |
| Open item 4: `bank_testkit/independent.py` compares canonical values, not issue lists | **Accepted.** Decision 50 point 4 is amended: the bank agent's cleaning and the datagen oracle are each tested on every case of the neutral table for action, problems and canonical value; the test kit oracle is tested on every case for action and canonical value. Two independent implementations of the issue order plus the hand-written table are enough. |
| Open item 5: a wrapped line ending in a colon | **Accepted** as read; the wrapping rule of decision 108 removes the possibility. |

## G. Tests and tooling

### 114. The process environment of this build

Recorded because both reviews met it: several agents share one machine and one PostgreSQL. Nobody uses `pkill` or kills a process by name; a process is stopped by the pid its starter recorded. Every reviewer and implementer works in a private virtual environment (decision 81, finding 11) and under a private database prefix.

### 115. Scratch objects: nothing left behind, and the sweep cannot fail (bank F6, WP1 quality 3 and 13)

1. **Register before creating.** A fixture records the name it is about to create in the session's own list before the first `CREATE`, and SIGTERM is deferred for the duration of each provisioning call (create roles, create database, grant). The object being created when a signal arrives is therefore known and is dropped.
2. **Drop by ownership at the end.** When the session ends for any reason other than SIGKILL (normal end, failure, SIGINT, SIGTERM), a final step drops every database and role whose name starts with this session's own `optest_<timestamp>_<pid>_` stem, whatever the fixtures did or did not finalise.
3. **The sweep tolerates anything.** A name that starts with `optest_` and whose timestamp cannot be parsed (month 13, fourteen zeros, a year of 9999) is treated as stale and dropped. Any error while examining or dropping one object is caught, written as one warning line, and the sweep goes on. `pytest_sessionstart` never fails because of the sweep.
4. **No test creates a name outside the scheme.** The tooling's own tests prove "never touches any other name" in two ways only: unit tests of the selection function with an injected list of names (pinning the Python side, which kills the surviving mutation), and integration tests that use `optest_` names with old and new timestamps. `sweepcheck_`, `sweephost_` and untimed names are removed.
5. Test: a script sends SIGTERM to a running integration session at 20 different moments, including during provisioning, and after each the server holds no object with that session's stem.

### 116. What the `unit` marker enforces is what its docstring says (WP1 quality 2)

The isolation for tests marked `unit` is enforced through an interpreter audit hook (active only while a unit test runs) together with explicit replacement of the database entry points, and refuses, with a failure that names the rule: opening any socket connection or binding or sending on one (TCP and UDP); `psycopg.connect`, `psycopg.Connection.connect` and `sqlalchemy.create_engine`; starting any child process by any route (`subprocess`, `os.system`, `os.posix_spawn`, `os.fork`, `os.exec*`); and creating, writing, renaming or removing any file or directory (including through `tempfile`, `tmp_path` and `Path.write_*`). Reading files under the repository root is allowed.

The docstring lists exactly these and claims nothing more. A test per refused operation (the seven probes of the review among them) shows it fails, and one shows a committed file can be read. If an operation cannot be intercepted, the docstring says so by name instead of claiming it.

### 117. Each package can be tested from its own declaration

Every plugin and package that the root pytest configuration or a package's own tests need is declared in that package's `dev` extra (`pytest-timeout` in `bank_agent[dev]`, and whatever else is missing). A test in each package reads its `pyproject.toml` and the root configuration and fails if a required plugin is not declared. `tools/onepledge_testtools` gets a `pyproject.toml` so it is installed like the other packages and declared as a dev dependency of the packages that use it, not loaded through a path setting.

### 118. Mutation list

RUBRIC TEST-04's list now has fourteen entries: the ten of the rubric, 12 and 13 of decision 91, and 14 (treat `StoreRefusedError` in an outbox handler as a deferral). Number 11 is the second half of rubric mutation 6. Each must fail a test.

## H. Code and presentation rules

### 119. `noqa`: ten in the whole repository, reached by removing the need (CODE-01)

The limit of RUBRIC CODE-01 is for the repository and stays at 10 `noqa` and 10 `type: ignore`, counted with `git grep`. It is reached by changing code, not by moving the suppression into configuration:

- No new per-file ignore is added to the ruff configuration. The only ones allowed remain those of SPEC 2.2 (`S101` and `PLR2004` under `tests/`).
- Child processes in tests are started through one helper in `onepledge_testtools` (fixed interpreter, argument list, no shell). That one function carries the one `S603` suppression with its reason. The nine scattered ones go.
- The wildcard address appears in tests through one named constant in `onepledge_testtools` carrying the one `S104` suppression. The four scattered ones go.
- `PLR0913` on the test kit's `certificate` builder is removed by taking a small parameter object or keyword-only fields with defaults in a dataclass. `S608` in the deleted-evidence test is removed by building the statement from a fixed table of statements.
- That leaves about four. From now on a commit that adds a `noqa` says in its message why the code could not be written to pass.

### 120. Declared dependencies (CODE-05)

`starlette` is added to the allowed runtime packages (it is the framework FastAPI is built on, and `ClientDisconnect` is only available from it). Every package that imports it declares it in its `pyproject.toml`: outside records and the doubles now, the bank agent when WP4 needs it. An architecture test's allow-list of web packages must equal what the package declares; a name on the allow-list that is not declared fails the test.

### 121. Command lines: the same rules for every line and every argument (PRES-03, WP1 quality 10)

1. **Width.** Every line a layer 1 tool prints for a human is at most 100 columns: results, errors, usage errors, help, and each line of `make help`. `--wide` lifts it for results only.
2. **Usage errors are the product's own sentences.** The argument parser's built-in messages are replaced. An invalid choice reads `argument --profile must be one of: full, small, example`; no quotes, brackets or parentheses from a Python repr; no echo of the value typed. Each sentence is wrapped to the width.
3. **Arguments are exact.** Abbreviated options are off (`--se` is a usage error). An option given twice is a usage error. `--json` takes no value; any argument that starts with `--json` selects the JSON form for the usage error that follows, so `--json=1` still prints one document.
4. A test in each package runs every command with about a dozen malformed command lines and asserts width, absence of repr characters, exit 2, and one JSON document when asked.

## I. Contract, rubric, order

### 122. Service names in help and documents

Unchanged rules, restated because two findings touched them: every statement in a README or help text is true of the code at that commit; a document that lists files lists the files that exist (decision 112, finding 11).

### 123. Contract changes (draft, additive)

This commit does not touch the YAML files. The WP1 implementer makes exactly these edits in `contracts/bank_agent.yaml`, re-runs `onepledge-contracts check`, and changes nothing else:

- `DatabaseControlsSection`: new optional property `violations`, an array (at most 200) of objects with `additionalProperties: false`, required `control`, `code`, `object`: `control` and `code` refer to `Code`; `object` is a string of at most 200 characters or null. New optional integer `violations_total`.
- `RegistryMessagesSection`: new optional property `messages`, an array (at most 200) of objects with `additionalProperties: false`, required `quarantine_id`, `message_id`, `message_type`, `reason`, `received_at`, `payload_withheld`, `open`: `quarantine_id` a UUID; `message_id` a UUID or null; `message_type` a string of at most 32 characters; `reason` refers to `Code`; `received_at` a date-time; the last two booleans. New optional property `outbox`, an object with `additionalProperties: false`, required `last_complete_sync_at` (date-time or null), `blind` (boolean), `blind_since` (date-time or null), `reason` (`Code` or null).
- `EvidenceSection.problems[].problem`: add the values `DIGEST_MISMATCH`, `DERIVATION_MISMATCH`, `STATE_MISMATCH`, `UNREADABLE`. New optional property `stage` on a problem (a `TapeStatus` or null). New optional integer `problems_total` on the section.
- `FailureCode` and the tape schemas are unchanged.

`contracts/README.md` gains one line in its table of decisions for Vedant: the integrity report grew the sections and fields of decisions 95 and 123.

### 124. How reviewers read the rubric from now on

| Rubric text | Read as |
|---|---|
| SEC-12, BANK-08 | Decision 101 in full: no message and no page can block the outbox for good or go unrecorded; a blind bank does not submit; a short registry outage creates no permanent state. Decision 102: every answer bounded. Decision 100: no stored value that a reader cannot read. |
| AUD-05 | Seven sections; fifteen controls (`TRIGGERS_FIRE_FOR_APP_ROLE` and `SCHEMA_MATCHES_MANIFEST` added to the thirteen); text and JSON carry the same findings in both directions (decision 108); the report renders whatever the tables hold (decision 100). |
| AUD-03 | Also every tamper of decision 107 and every schema change of decision 105, each found and located. |
| SEC-02, DATA-06 | Decision 72 with the ten points of decision 103. |
| BANK-09, SEC-01 | Also: no table permits `DELETE` or `TRUNCATE` (`OP008`); an answered registry request and its items are frozen (`OP007`). SEC-01's insert-only list stays at ten. |
| CODE-05 | 22 tables in the bank database (`registry_outbox_attempts` replaced by `registry_outbox_messages`; `registry_outbox_state` added). Outside records as SPEC 6.1 plus the constraints of decision 109. Declared dependencies per decisions 117 and 120. |
| DATA-06 | Also decision 109: a seed that would make the service answer outside its contract is refused with file, line and column. |
| DX-05 | Also decision 111: every reference resolved; a broken reference exits 1 and names the file. |
| CODE-01 | At most 10 `noqa` and 10 `type: ignore` in the repository, no per-file ignore beyond SPEC 2.2 (decision 119). |
| PRES-03 | Decision 121: 100 columns for every line including usage errors and `make help`; no repr in any message. |
| TEST-01 | Also decision 115 (SIGTERM at any moment leaves nothing; the sweep cannot fail), decision 116 (unit isolation as documented), decision 117. |
| TEST-02 | Also: no exemption list for a rule of this file (decision 99). |
| TEST-04 | Fourteen mutations (decision 118). |
| REL-06 | "Both extra workers exit 0" holds when the sync was complete and no tape failed (decision 101). |

---

## Round 6: changes for the WP1 implementer (`contracts/`, `datagen/`, `outside_records/`, root tooling, `tools/`)

| # | Change | Decision |
|---|---|---|
| X1 | The YAML edits of decision 123 and the README line; `onepledge-contracts check` passes | 123 |
| X2 | Outside records `seed`: record validators with every contract rule; contract test deriving probes from `outside_records.yaml`; validate everything before writing; `FILE line N, column NAME: PROBLEM`, at most 20 plus a count; new migration with the check constraints and unique keys; `clients add` duplicate key sentence | 109 |
| X3 | Regular files inside the directory, no links, size limits, in the checker, `verify` and `seed`; the same sentences in all three | 110 |
| X4 | Checker: own `$ref` resolution, local references only, validator behind one boundary, examples validated through the operation | 111 |
| X5 | `inspect_role()` in outside records: points 5 (tightened), 7, 8, 9, 10; URL parameters refused; session parameters set by the service; advice per violation code; `db migrate` requires `DB_APP_ROLE` to be `{database}_app`; the two mirror-check sentences | 103 |
| X6 | Startup as named checks with their own sentences and SQLSTATE, in outside records | 104 |
| X7 | Repeated headers: 401 or 422 `DUPLICATE`, in outside records and both doubles | 112.8 |
| X8 | Settings refuse an all-zero signing seed and duplicate key hashes among principals | 112.12 |
| X9 | README layout test and the corrected doubles README | 112.11 |
| X10 | Test tooling: register before create, signals deferred during provisioning, drop by session stem at exit, sweep that tolerates any name and any error, no test names outside the scheme, selection function unit tested, the 20 moment SIGTERM script | 115 |
| X11 | Unit isolation by audit hook and database entry points; docstring equal to what is enforced; one test per refused operation | 116 |
| X12 | `tools/onepledge_testtools` becomes an installable package declared by its users; each package's dev extra declares every required plugin; a test checks it | 117 |
| X13 | One child process helper and one wildcard constant in `onepledge_testtools`; scattered `S603` and `S104` suppressions removed in WP1 packages | 119 |
| X14 | `starlette` declared in outside records and the doubles; web allow-list equals the declaration | 120 |
| X15 | Command lines: 100 columns everywhere including usage errors and `make help`; product sentences for usage errors; no abbreviations, no repeated options, `--json=...` handled; tests | 121 |
| X16 | Canonical text bounds of decision 100 applied to what the doubles and outside records store or return (timestamps in range, text rule) | 100 |

## Round 6: changes for the bank agent implementer (`bank_agent/`)

| # | Change | Decision |
|---|---|---|
| Y1 | Round-trip rule: storable value objects, repositories take domain types only (architecture test), check constraints on externally supplied timestamps, readers guard each section and row, `UNREADABLE` problems, Hypothesis round-trip tests, the three F1 reproductions | 100 |
| Y2 | Outbox per decision 101: page level and element level handling, `UNACKNOWLEDGEABLE`, unusable type quarantined, deferral without conversion, checkpoints not holding back other kinds, `UNMATCHED` case updates applied late, acknowledgement refusals, `registry_outbox_messages` and `registry_outbox_state`, complete and blind, `OUTBOX_BLIND_HALT_SECONDS`, fourth halt condition, exit rules of `worker --once` and `registry sync`, `payload_withheld`, every listed test | 101 |
| Y3 | Bounded answers in all four clients: size, identity encoding, total deadline; tests | 102 |
| Y4 | `inspect_role()` points 5 (tightened), 7 to 10; per-connection session parameters and `session_replication_role` check; URL parameters refused; advice per code; control `TRIGGERS_FIRE_FOR_APP_ROLE`; `db migrate` requires the exact app role name | 103 |
| Y5 | Startup as named checks, never the last resort for an environment condition | 104 |
| Y6 | Schema manifest replacing the guard manifest; control `SCHEMA_MATCHES_MANIFEST`; drift test | 105 |
| Y7 | `OP008` no-delete triggers on every table that lacks one; `OP007` guards on `registry_requests` and `registry_request_items`; both tables in the column classification test | 106 |
| Y8 | Deep verification: stage digests in the audit detail and their recomputation, re-derivation of issues and loan terms, replay for loans, tapes and alerts, reference data digest, user role and active replay; every tamper of the review as a test; the residual list recorded for WP5 | 107 |
| Y9 | JSON report with violations, messages, outbox state and evidence problems; `TEXT_ONLY` removed; two-way parity test; wrapping rule | 108, 99 |
| Y10 | Settings refuse equal outbound keys and a JWT secret equal to an API key | 112.12 |
| Y11 | Repeated header rule built into the HTTP layer when WP4 writes it; README layout test now | 112.8, 112.11 |
| Y12 | Help and README corrections; `seed-reference` with an unknown bank code | 113 |
| Y13 | `bank_agent[dev]` declares `pytest-timeout` and `onepledge-testtools`; scattered suppressions removed (child process helper, wildcard constant, parameter object for `certificate`, fixed statements in the deleted-evidence test) | 117, 119 |
| Y14 | Command line rules of decision 121 | 121 |
| Y15 | The killing test for mutation 14 | 113, 118 |

## Order

1. X1 first: Y9 and Y8 need the contract fields.
2. X12 and X13 before Y13: the bank agent uses the installable test tools and the shared helpers.
3. X10 and X11 before the bank agent implementer's final run, so its suite runs under the corrected tooling.
4. Inside the bank agent: Y1 before Y2 (the outbox uses the storable types); Y7 before Y8 (deep verification tests disable and re-enable the new guards); Y6 after Y7 and Y2 (the manifest must include their tables and triggers).

Everything else is independent.

---

# Round 7 (2026-11-23): leftovers of the round 6 implementation

Inputs: the hand-backs for X1 to X16 and Y1 to Y15 (commits 7368d26 to ee87bc2). Decisions 125 to 131.

## 125. Size bounds for stored JSON depend on whether a contract bounds the document (amends the `jsonb` row of decision 100)

The 64 KiB bound of decision 100 was written for documents with no upper size of their own. It is wrong for a document whose size the contract already bounds, and the implementer was right to report the conflict instead of truncating or testing around it (decision 99). Two classes:

| Class | Columns | Bound |
|---|---|---|
| **Contract-bounded.** The document passed full validation against a contract schema that limits every array and every string. | `registry_requests.response_body` (a `SubmissionResult`, at most 500 results), `certificates.body` (a `CertificateBody`, at most 500 items), `checkpoints_seen.body` | The serialised canonical JSON is at most `HTTP_MAX_RESPONSE_BYTES` (decision 102, default 4,194,304). Nothing is stored that did not pass the schema, so the schema's own limits are the effective bound; the byte cap is the backstop. |
| **Free-form.** No contract schema limits the document, or the document failed its schema. | `registry_messages_quarantined.payload`, `audit_log.detail`, `local_checks.evidence`, `integrity_runs.report`, `loans_raw.raw_payload` and `raw_cells`, any other `jsonb` | 64 KiB, as decision 100 says, except: `integrity_runs.report` is bounded by its capped lists (decision 108) and at most 1 MiB; `raw_payload` and `raw_cells` are bounded by the tape limits of SPEC 10.1 (64 columns of 512 characters). A quarantined payload above 64 KiB is withheld (`payload_withheld`), as built. |

Every other part of decision 100 applies to both classes: the bounded parser, the text rule for every string, integers only and inside the `bigint` range. A test stores and reads back a contract-valid submission answer with 500 results of the longest legal form, and a certificate with 500 items.

## 126. A stop signal ends the test session; no test can absorb it (confirms and sharpens decision 115)

Required behaviour when SIGTERM reaches a test session:

1. No further test starts. The test that was running is reported as interrupted, not as failed or passed.
2. All fixtures finalise and the session's own scratch objects are dropped (decision 115, points 1 and 2).
3. The session ends with a non-zero exit code and a final line that says it was stopped by SIGTERM. It never ends with a summary such as "1 failed, 35 passed" that reads like a completed run.

How: the handler records the signal in the session and raises an exception that derives from `BaseException`, not from `Exception`, so `except Exception` in a test or in product code under test does not catch it. Because a bare `except:` or a `finally` that swallows could still absorb it, the plugin also checks the recorded signal before every test starts and after every test ends, and stops the session there if it is set. During a provisioning call the signal is held and acted on when the call returns (decision 115, point 1).

Tests: SIGTERM delivered while a test is inside `try: ... except Exception: pass`, inside a bare `except:`, inside `pytest.raises(Exception)`, inside a fixture, and during provisioning. Each ends the session as above and leaves no scratch object.

## 127. Parity tests cover every method of every repository port, including the new reads

Required now. Decision 63 asks for one behavioural test class per repository port, run against both implementations; that means every method of the port. `stage_rows`, `request_rows`, `reference_rows` and any other read added for deep verification get parity cases: the same data written through the port, the same rows returned in the same order by the in-memory and the PostgreSQL implementation, including the empty case, the multi-submission case and the ordering the digests of decision 107 rely on. Those reads feed the digests; a difference in order or in a column between the two implementations would make a unit-tested digest disagree with the real one.

To keep this from recurring, a test in the bank agent enumerates the public methods of every repository port and fails when a method has no parity case (by a naming convention or a registry the shared test classes fill in).

## 128. Implementer choices

| Choice | Ruling |
|---|---|
| The ledger guard on `registry_outbox_messages` uses SQLSTATE `OP009` | **Confirmed.** The guard codes are now: `OP001` insert-only, `OP002` loan state, `OP003` loan immutable columns, `OP004` tape, `OP005` audit chain, `OP006` alert, `OP007` answered registry request, `OP008` no delete or truncate, `OP009` outbox ledger. `bank_agent/README.md` lists them in one table; `StoreRefusedError` carries the same codes. |
| `pydantic-core` declared as a dependency of the doubles | **Confirmed**, and added to the allowed runtime packages: SPEC 2.1 requires a package to declare what it imports, and `pydantic_core` is imported by name. Rule for every package: where the name needed is exported by `pydantic` itself, import it from there; declare `pydantic-core` only where the name exists nowhere else. |
| The schema startup check is named "schema in place" | **Confirmed as a name, with its meaning fixed.** The check passes only when the database's revision equals the head revision of the installed migrations (SPEC 12.5). Its failure sentence says which of two things is wrong: the tables are missing (run `db migrate`), or the database is at another revision than this program expects. A check that only looks for the tables would not be enough. |
| A database-wide `session_replication_role` setting is reported as `TRIGGERS_DISABLED_FOR_SESSION` with object `database X` | **Overruled, small.** The session code describes a symptom; the operator has to fix a database setting. Use a distinct code, `DATABASE_HAS_SETTING`, with object `database X: session_replication_role`. `ROLE_HAS_SETTING` stays for role settings and `TRIGGERS_DISABLED_FOR_SESSION` for a session found in `replica` or `local` for a reason the other two checks did not explain. The advice sentence for the new code is "remove the database setting". `TRIGGERS_FIRE_FOR_APP_ROLE` is false for all three. |
| `seed --json` carries the first 20 problems and `problems_total` | **Confirmed.** It is what decision 109 point 4 asks for. |
| The WP1 implementer worked in the shared interpreter | **Not acceptable going forward.** Decision 81 (finding 11) already says "every reviewer and every implementer"; decision 114 repeats it. Implementers work in a private virtual environment built from their own working tree. Only the coordinator installs into the shared interpreter, and only between review rounds. No code change follows from this. |

## 129. Where the residual list lives

Confirmed. The list of what deep verification cannot detect (decision 107) is security documentation. Its one authoritative copy is `docs/layer1/THREAT_MODEL_L1.md`, written by WP5. Until WP5 lands it stays in `bank_agent/README.md`. When WP5 writes the threat model, the README keeps a single sentence pointing to it and no second copy of the list, so the two cannot drift. The wording of the four items of decision 107 is carried over unchanged, together with the limitation of decision 94.

## 130. Allowed packages, restated

After decisions 9, 76, 120 and 128 the lists of SPEC 2.1 read: runtime adds `starlette` and `pydantic-core` (each only where imported); dev-only adds `types-PyYAML`, `types-jsonschema`, `pytest-timeout`; `PyYAML`, `jsonschema` and `openapi-spec-validator` are runtime dependencies of the doubles package only. Nothing else has been added.

## 131. No contract or rubric reading changes in this round

`contracts/` is unchanged. Readings of decision 124 stand; TEST-01 is additionally read with decision 126 (a signalled session stops) and decision 127 (parity for every port method).

---

## Round 7: changes

WP1 implementer (`tools/`, root tooling, `outside_records/`):

| # | Change | Decision |
|---|---|---|
| Z1 | Test tooling: signal recorded and raised as a `BaseException` subclass; checked before and after every test; interrupted test reported as interrupted; non-zero exit with a "stopped by SIGTERM" line; the five absorption tests | 126 |
| Z2 | Outside records `inspect_role()`: code `DATABASE_HAS_SETTING` with object `database X: session_replication_role` and its advice sentence | 128 |
| Z3 | Check each WP1 package imports from `pydantic` where the name is exported there; keep `pydantic-core` declared only where needed | 128 |

Bank agent implementer (`bank_agent/`):

| # | Change | Decision |
|---|---|---|
| V1 | Size bounds by class: contract-bounded documents up to `HTTP_MAX_RESPONSE_BYTES` after schema validation, free-form as the table says; tests with a 500 result answer and a 500 item certificate | 125 |
| V2 | Parity cases for `stage_rows`, `request_rows`, `reference_rows` and every other port read; a test that fails when a port method has no parity case | 127 |
| V3 | `DATABASE_HAS_SETTING` as Z2; control false for all three causes | 128 |
| V4 | README table of the nine guard codes; schema startup check passes only at head, with the two sentences | 128 |

Order: Z1 before the bank agent implementer's final run (its suite uses the tooling). Nothing else depends on order.

For WP5: the threat model takes over the residual list and the README keeps a pointer (decision 129).

---

# Round 8 (2026-11-23): two rulings before the fresh reviews

Decisions 132 to 134.

## 132. Outside records reads its schema revision the way the bank agent does

**Question.** Decision 128 says the schema startup check passes only when the database's revision equals the head of the installed migrations. The outside records app role has no privilege on `alembic_version` (SPEC 6.1 gives it `SELECT` on four tables only), and decision 103 forbids a `SECURITY DEFINER` function.

**Decision.** The outside records grant matrix gains one entry: `SELECT` on `alembic_version`. This is exactly what the bank agent's matrix already has (SPEC 12.2), and both services then verify the revision the same way: the app role reads `version_num` and compares it with the head revision of the migrations package installed next to the program.

- The grant is issued by a new grants migration in outside records (a new revision, so existing scratch databases move forward by `db migrate`).
- `inspect_role()` in outside records expects exactly `SELECT` on the five tables. `INSERT`, `UPDATE`, `DELETE`, `TRUNCATE`, `REFERENCES` or `TRIGGER` on `alembic_version`, or a column-level grant on it, stays a violation, as on every other table.
- The check's two sentences are those of decision 128: tables missing (run `db migrate`), or the database is at another revision than this program expects. A missing `SELECT` on `alembic_version` is reported by the role inspection as `TABLE_PRIVILEGE_MISSING` with the advice "run db migrate as the owner", because that is what fixes it.
- SPEC 6.1 is amended accordingly: the app role holds `SELECT` on the four data tables and on `alembic_version`, nothing else. "Read-only" is unchanged: the role can still write nothing.

**Rationale.** The revision string is not sensitive, a read privilege does not weaken the read-only guarantee, and one mechanism in two services is better than a second mechanism invented to avoid one grant. The alternatives were worse: no check (a service running against a schema it was not built for), or a definer function (forbidden for good reason).

**Affects.** WP1 implementer. DATA-06 (the reviewer's matrix for outside records is five tables with `SELECT`).

## 133. Text is compared and ordered by code point everywhere, and that is pinned in the schema

**Question.** Stage reads sort text columns with the database's default collation. Under a locale or ICU collation PostgreSQL's order can differ from the in-memory order, so a stage digest (decision 107) could differ between two banks, or between a bank and a re-derivation.

**Facts the decision rests on.** Python compares `str` values by code point. PostgreSQL's `"C"` collation compares the bytes of the server encoding. For UTF-8 those two orders are the same for every valid string, including characters outside the Basic Multilingual Plane (UTF-8 was designed to preserve code point order; UTF-16 does not). Lone surrogates, the one case where they could differ, are already refused by the text rule of decision 100. The equivalence holds only when the database encoding is UTF8.

The exposure is wider than ordering. A database default or column collation that is non-deterministic (for example a case-insensitive ICU collation) also changes **equality**: unique indexes, the `(facility_id, vin)` key, token lookups and email uniqueness would then treat different strings as equal.

**Decision.** Four parts, all required, for every layer 1 service that has a database (the bank agent and outside records).

1. **Encoding.** `db provision` creates the database with `ENCODING 'UTF8'` from `template0`. A startup check "database encoding" refuses to start, with its own sentence, when `server_encoding` is not `UTF8`. The schema manifest records it.

2. **Every text column is declared `COLLATE "C"`.** Every column of type `text`, `char(n)`, `varchar` and every `text[]` in the schema carries an explicit `COLLATE "C"`, set in the migrations (a new migration alters the existing columns; SQLAlchemy models declare the collation so the drift test agrees). Comparisons, unique indexes, check constraints with patterns, and `ORDER BY` on those columns are then by code point whatever the database default is. A test walks the catalogue of a migrated database and fails if any text-typed column has another collation, so a column added later cannot be missed. The schema manifest of decision 105 includes each column's collation, so `SCHEMA_MATCHES_MANIFEST` turns false if one is changed.

3. **Queries never depend on a default collation.** An `ORDER BY`, `DISTINCT`, `GROUP BY`, `min`, `max` or comparison on an expression that is not a plain column (a cast to text, a concatenation, a function result) states `COLLATE "C"` explicitly. Case mapping is not done in SQL on external text: lower casing an email or upper casing a code happens in the application under the ASCII rules already decided, and the stored value is compared as stored. An architecture test over the SQL of each service fails on an ordering or comparison of a text expression without an explicit collation.

4. **Digests own their order.** The function that computes a stage digest sorts its rows itself, by a key defined once in the domain (tuples of canonical strings and integers; UUIDs as their canonical lower case text; null first), using Python's code point order. The digest therefore does not depend on the order in which a repository returned the rows. The port contract still states the order of each digest-feeding read, and the parity tests of decision 127 still compare it, so the two implementations cannot drift.

Tests:

- **A non-C database.** An integration test provisions a scratch bank database whose default collation is an ICU collation (`und-x-icu`, and a second one with a case-insensitive non-deterministic collation where the server supports it), migrates it, runs a tape whose values include upper and lower case letters, digits, punctuation and characters above U+FFFF in fields that are sorted (loan numbers, borrower references, reason codes, evidence strings), and asserts: every stage digest equals the digest computed on a `C` database and by the in-memory implementation; the parity cases of decision 127 pass; unique keys distinguish strings that differ only in case; deep verification is OK. If the build machine's PostgreSQL has no ICU support the test says so in its name and uses a libc locale other than `C` instead; it does not silently pass.
- **A column moved off `"C"`.** A superuser alters one column's collation: the control turns false and the report names the column.
- **Outside records** gets the same non-C database test for its three lookups (results and their order unchanged) and the catalogue test.

**Exposure of the other components.**

- **Outside records: yes, for equality and uniqueness, not for its documented orderings.** Its three lookups order by dates and UUIDs, which no collation affects. But VIN lookups, client name uniqueness and key hash lookups compare text, so parts 1 to 3 apply to it in full.
- **The doubles: no.** They hold their state in memory and sort with Python. Nothing to change. The rule for them is only the one that already holds: no `locale` aware sorting or comparison anywhere (`locale.strcoll`, `str.casefold` for keys, `sorted(key=str.lower)` on external text).
- **datagen and the scorecard (WP5): no database.** The same no-locale rule applies; determinism by seed already depends on it. An architecture test in every package bans `locale.strcoll`, `locale.strxfrm` and `functools.cmp_to_key` over them.

**Rationale.** A digest that depends on where a database was created is not evidence. Declaring the collation on the columns, instead of only on the queries that happened to be noticed, fixes ordering and equality together and makes the property visible in the schema, checkable by the manifest, and impossible to forget on the next query.

**Affects.** Both implementers. AUD-03, BANK-02, BANK-09 (the `(facility_id, vin)` key), CODE-05, DATA-06.

## 134. Rubric reading

- DATA-06: the outside records role holds `SELECT` on five tables (decision 132).
- CODE-05: every text-typed column of both services is `COLLATE "C"`; the database encoding is UTF8 (decision 133).
- AUD-03: also a changed column collation is found by `SCHEMA_MATCHES_MANIFEST`; digests are equal across database collations.

No contract file changes.

---

## Round 8: changes

WP1 implementer (`outside_records/`, and the no-locale test in `contracts/fakes/` and `datagen/`):

| # | Change | Decision |
|---|---|---|
| Q1 | New grants migration: `SELECT` on `alembic_version` for the app role; matrix of five tables in `inspect_role()`; schema check reads the revision and passes only at head, with the two sentences; tests | 132 |
| Q2 | `db provision` with `ENCODING 'UTF8'` from `template0`; startup check "database encoding" | 133.1 |
| Q3 | Migration setting `COLLATE "C"` on every text-typed column; models declare it; catalogue test that no text column has another collation | 133.2 |
| Q4 | Explicit collation on any ordering or comparison of a text expression; no case mapping of external text in SQL; architecture test over the SQL | 133.3 |
| Q5 | Non-C scratch database test for the three lookups | 133 |
| Q6 | Architecture test banning locale aware comparison in outside records, the doubles and datagen | 133 |

Bank agent implementer (`bank_agent/`):

| # | Change | Decision |
|---|---|---|
| R1 | `db provision` with `ENCODING 'UTF8'` from `template0`; startup check "database encoding"; encoding in the schema manifest | 133.1 |
| R2 | Migration setting `COLLATE "C"` on every text-typed column and `text[]`; models declare it; catalogue test; collations in the schema manifest and the control | 133.2 |
| R3 | Explicit collation on any ordering or comparison of a text expression; no case mapping of external text in SQL; architecture test over the SQL | 133.3 |
| R4 | Digest function sorts its own rows by one domain key; port contracts keep their stated order | 133.4 |
| R5 | Non-C scratch database test (ICU, and case-insensitive where supported): digests equal to the C database and to memory, parity cases pass, unique keys case-sensitive, deep verification OK; the altered-collation tamper test | 133 |
| R6 | Architecture test banning locale aware comparison | 133 |

No ordering dependency between the two implementers.
