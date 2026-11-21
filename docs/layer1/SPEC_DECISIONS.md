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
