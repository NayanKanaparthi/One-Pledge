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
